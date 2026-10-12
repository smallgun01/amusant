"""boto3 DynamoDB ledger. Does not create tables."""
from __future__ import annotations

import hashlib
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from .dynamodb_task_store import (
    DEDUP_SK_PREFIX,
    QUERY_ITEM_BOUND,
    LedgerAmbiguous,
    LedgerConditionFailed,
)
from .task_store import AdmissionUnavailable

_AMBIGUOUS_WRITE_CODES = {
    "InternalServerError",
    "ServiceUnavailable",
    "TransactionConflictException",
}

_CONDITIONS = {
    "exists": "attribute_exists(pk)",
    "claimed": "outbox_state = :claimed",
    "claimed_unsent": "outbox_state = :claimed AND attribute_not_exists(dispatch_started_at)",
    "claimed_started": "outbox_state = :claimed AND attribute_exists(dispatch_started_at)",
    "pending_queued": "outbox_state = :pending AND #status = :queued",
    "delivery_can_speak": "attribute_not_exists(delivery_state) OR delivery_state <> :ds_spoken",
    "delivery_can_read": (
        "attribute_not_exists(delivery_state) OR delivery_state = :ds_pending "
        "OR delivery_state = :ds_notified"
    ),
}


def _error_code(exc: ClientError) -> str | None:
    return (exc.response.get("Error") or {}).get("Code")


def _http_status(exc: ClientError) -> int | None:
    status = (exc.response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return status if isinstance(status, int) else None


def _client_request_token(puts: list[dict[str, object]]) -> str:
    if not puts:
        return ""
    target = next(
        (item for item in puts if str(item.get("sk") or "").startswith(DEDUP_SK_PREFIX)),
        puts[0],
    )
    raw = f"{target.get('pk')}|{target.get('sk')}|{target.get('payload_sha256')}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _raise_transact_error(exc: ClientError) -> None:
    code = _error_code(exc)
    if code == "TransactionCanceledException":
        reasons = exc.response.get("CancellationReasons") or []
        if any((reason or {}).get("Code") == "ConditionalCheckFailed" for reason in reasons):
            raise LedgerConditionFailed() from exc
        raise LedgerAmbiguous() from exc
    if code in _AMBIGUOUS_WRITE_CODES:
        raise LedgerAmbiguous() from exc
    status = _http_status(exc)
    if status is not None and status >= 500:
        raise LedgerAmbiguous() from exc
    raise AdmissionUnavailable("task_store_unavailable") from exc


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
            code = _error_code(exc)
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
        kwargs: dict[str, object] = {"TransactItems": items}
        token = _client_request_token(puts)
        if token:
            kwargs["ClientRequestToken"] = token
        try:
            self._client.transact_write_items(**kwargs)
        except ClientError as exc:
            _raise_transact_error(exc)
        except BotoCoreError as exc:
            raise LedgerAmbiguous() from exc

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
        if ":ds_spoken" in expression:
            values[":ds_spoken"] = {"S": "spoken"}
        if ":ds_pending" in expression:
            values[":ds_pending"] = {"S": "pending"}
        if ":ds_notified" in expression:
            values[":ds_notified"] = {"S": "notified"}
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
