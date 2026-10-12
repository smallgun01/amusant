"""boto3 DynamoDB ledger. Does not create tables."""
from __future__ import annotations

from typing import Any

from botocore.exceptions import ClientError

from .dynamodb_task_store import (
    QUERY_ITEM_BOUND,
    LedgerAmbiguous,
    LedgerConditionFailed,
)
from .task_store import AdmissionUnavailable

_CONDITIONS = {
    "exists": "attribute_exists(pk)",
    "claimed": "outbox_state = :claimed",
    "claimed_unsent": "outbox_state = :claimed AND attribute_not_exists(dispatch_started_at)",
    "claimed_started": "outbox_state = :claimed AND attribute_exists(dispatch_started_at)",
    "pending_queued": "outbox_state = :pending AND #status = :queued",
}


class Boto3DynamoLedger:
    """Low-level DynamoDB client adapter. Never calls CreateTable."""

    def __init__(self, client: Any, table_name: str) -> None:
        if not isinstance(table_name, str) or not table_name.strip():
            raise ValueError("AMUSANT_DDB_TABLE must be set")
        from boto3.dynamodb.types import TypeDeserializer, TypeSerializer

        self._client = client
        self._table = table_name.strip()
        self._ser = TypeSerializer()
        self._de = TypeDeserializer()

    def _dump(self, item: dict[str, object]) -> dict[str, object]:
        return {key: self._ser.serialize(value) for key, value in item.items() if value is not None}

    def _load(self, item: dict[str, object]) -> dict[str, object]:
        return {key: self._de.deserialize(value) for key, value in item.items()}

    def _call(self, method: str, **kwargs: object) -> dict[str, object]:
        try:
            return getattr(self._client, method)(**kwargs)
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code")
            if code in {"ConditionalCheckFailedException", "TransactionCanceledException"}:
                raise LedgerConditionFailed() from exc
            if code in {"TransactionConflictException"}:
                raise LedgerAmbiguous() from exc
            raise AdmissionUnavailable("task_store_unavailable") from exc

    def get_item(self, pk: str, sk: str, *, consistent: bool = True) -> dict[str, object] | None:
        response = self._call(
            "get_item",
            TableName=self._table,
            Key={"pk": {"S": pk}, "sk": {"S": sk}},
            ConsistentRead=consistent,
        )
        item = response.get("Item")
        return None if not item else self._load(item)

    def query_prefix(self, pk: str, sk_prefix: str) -> tuple[list[dict[str, object]], bool]:
        rows: list[dict[str, object]] = []
        kwargs: dict[str, object] = {
            "TableName": self._table,
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :prefix)",
            "ExpressionAttributeValues": {":pk": {"S": pk}, ":prefix": {"S": sk_prefix}},
            "ConsistentRead": True,
        }
        truncated = False
        while True:
            response = self._call("query", **kwargs)
            for item in response.get("Items") or []:
                rows.append(self._load(item))
                if len(rows) > QUERY_ITEM_BOUND:
                    return rows[:QUERY_ITEM_BOUND], True
            start = response.get("LastEvaluatedKey")
            if not start:
                break
            kwargs["ExclusiveStartKey"] = start
        return rows, truncated

    def transact_put(
        self,
        puts: list[dict[str, object]],
        *,
        absent: list[tuple[str, str]],
    ) -> None:
        del absent
        items = []
        for put in puts:
            items.append(
                {
                    "Put": {
                        "TableName": self._table,
                        "Item": self._dump(put),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                }
            )
        try:
            self._client.transact_write_items(TransactItems=items)
        except ClientError as exc:
            code = (exc.response.get("Error") or {}).get("Code")
            if code == "TransactionCanceledException":
                reasons = exc.response.get("CancellationReasons") or []
                if any((reason or {}).get("Code") == "ConditionalCheckFailed" for reason in reasons):
                    raise LedgerConditionFailed() from exc
                raise LedgerAmbiguous() from exc
            if code == "TransactionConflictException":
                raise LedgerAmbiguous() from exc
            raise AdmissionUnavailable("task_store_unavailable") from exc

    def update_item(
        self,
        pk: str,
        sk: str,
        patch: dict[str, object],
        *,
        condition: str,
    ) -> dict[str, object]:
        expression = _CONDITIONS.get(condition)
        if expression is None:
            raise ValueError(f"unknown ledger condition {condition}")
        sets: list[str] = []
        removes: list[str] = []
        names: dict[str, str] = {}
        if "#status" in expression:
            names["#status"] = "status"
        values: dict[str, object] = {}
        if ":claimed" in expression:
            values[":claimed"] = {"S": "claimed"}
        if ":pending" in expression:
            values[":pending"] = {"S": "pending"}
        if ":queued" in expression:
            values[":queued"] = {"S": "queued"}
        for index, (key, value) in enumerate(patch.items()):
            alias = f"#p{index}"
            names[alias] = key
            if value is None:
                removes.append(alias)
            else:
                token = f":v{index}"
                values[token] = self._ser.serialize(value)
                sets.append(f"{alias} = {token}")
        parts: list[str] = []
        if sets:
            parts.append("SET " + ", ".join(sets))
        if removes:
            parts.append("REMOVE " + ", ".join(removes))
        if not parts:
            raise ValueError("empty patch")
        kwargs: dict[str, object] = {
            "TableName": self._table,
            "Key": {"pk": {"S": pk}, "sk": {"S": sk}},
            "UpdateExpression": " ".join(parts),
            "ConditionExpression": expression,
            "ExpressionAttributeValues": values,
            "ReturnValues": "ALL_NEW",
        }
        if names:
            kwargs["ExpressionAttributeNames"] = names
        response = self._call("update_item", **kwargs)
        return self._load(response["Attributes"])
