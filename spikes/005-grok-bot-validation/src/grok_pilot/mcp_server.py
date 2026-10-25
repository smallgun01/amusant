"""MCP SDK adapter. G2 exposes only a loopback-capable submit_result tool."""

from __future__ import annotations

import hmac

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .contract import ContractError, validate_report
from .store import PilotStore, StoreError

MCP_SCOPE = "grok-pilot:submit"
MCP_PATH = "/mcp"
MCP_BODY_LIMIT = 16 * 1024


class StaticOwnerVerifier(TokenVerifier):
    """Single-owner bearer verifier for tests and isolated local demonstrations."""

    def __init__(self, expected_token: str, owner_id: str):
        self._expected_token = expected_token
        self.owner_id = owner_id

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._expected_token):
            return None
        return AccessToken(
            token=token,
            client_id="offline-owner-client",
            subject=self.owner_id,
            scopes=[MCP_SCOPE],
        )


def create_mcp_server(store: PilotStore, owner_id: str, owner_bearer_token: str) -> MCPServer:
    """Build an isolated SDK server protected by a test/local-only Owner token."""
    if not owner_bearer_token:
        raise ValueError("an Owner bearer token is required")
    verifier = StaticOwnerVerifier(owner_bearer_token, owner_id)
    server = MCPServer(
        name="amusant-grok-pilot",
        version="0.1.0",
        instructions="Submit one bounded report for an already prepared pilot task.",
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url="https://offline-owner.invalid",
            resource_server_url="https://offline-owner.invalid/mcp",
            validate_token_resource=False,
            required_scopes=[MCP_SCOPE],
        ),
    )

    @server.tool(name="submit_result", description="Persist one immutable report for a prepared pilot task")
    async def submit_result(
        schema_version: str,
        task_id: str,
        result_token: str,
        reported_outcome: str,
        speak: str,
        card: dict[str, object],
        native_result_locator: str | None = None,
    ) -> str:
        payload = {
            "schema_version": schema_version,
            "task_id": task_id,
            "result_token": result_token,
            "reported_outcome": reported_outcome,
            "speak": speak,
            "card": card,
            "native_result_locator": native_result_locator,
        }
        try:
            validated = validate_report(payload)
            receipt = store.submit_report(owner_id, validated)
        except (ContractError, StoreError) as exc:
            # MCP error text is intentionally generic; never echo the token or report.
            raise ToolError(str(exc)) from None
        import json

        return json.dumps(
            {
                "accepted": True,
                "task_id": receipt.task_id,
                "receipt_id": receipt.receipt_id,
                "received_at": receipt.received_at,
                "duplicate": receipt.duplicate,
                "late": receipt.late,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    # MCP SDK 2.3.0's generated Pydantic argument model ignores unknown fields by
    # default. Tighten this pinned model so owner_id and every other extra are rejected.
    registered = server._tool_manager._tools["submit_result"]
    argument_model = registered.fn_metadata.arg_model
    argument_model.model_config["extra"] = "forbid"
    argument_model.model_rebuild(force=True)
    registered.parameters = argument_model.model_json_schema(by_alias=True)

    return server


def create_mcp_app(store: PilotStore, owner_id: str, owner_bearer_token: str):
    """Return the SDK's Streamable HTTP ASGI app with a bounded request body."""
    server = create_mcp_server(store, owner_id, owner_bearer_token)
    return server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=False,
        max_request_body_size=MCP_BODY_LIMIT,
        host="127.0.0.1",
    )
