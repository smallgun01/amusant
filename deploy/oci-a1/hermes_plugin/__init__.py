"""Option D runtime plugin using the Facade's narrow artifact writer channel."""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import secrets
import stat
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

MAX_REPORT_BYTES = 64 * 1024
MAX_PRESENTATION_BYTES = 3000
MAX_RESPONSE_BYTES = 8192
SESSION_ID_RE = re.compile(r"^amusant-session-[A-Za-z0-9_-]{20,80}$")
ARTIFACT_REF_RE = re.compile(r"^art_[A-Za-z0-9_-]{20,80}$")
PRESENTATION_FIELDS = {"schema_version", "speak", "card", "details_md"}
_SAFE_ERRORS = {
    "invalid_arguments", "report_invalid", "report_over_cap",
    "trusted_request_context_unavailable", "artifact_conflict",
    "artifact_commit_failed", "artifact_integrity_invalid",
    "artifact_unavailable", "presentation_invalid",
    "presentation_conflict", "writer_unavailable",
}


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _read_writer_key() -> str:
    path_value = os.environ.get("AMUSANT_FACADE_WRITER_API_KEY_FILE", "")
    try:
        if not path_value:
            raise ValueError
        fd = os.open(Path(path_value), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError
            if os.name == "posix":
                mode = stat.S_IMODE(metadata.st_mode)
                shared_group = (
                    metadata.st_uid == 0 and metadata.st_gid == 20000
                    and 20000 in os.getgroups() and mode == 0o440
                )
                owner_only = metadata.st_uid == os.geteuid() and mode == 0o400
                if not (shared_group or owner_only):
                    raise ValueError
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError
        value = raw.decode("utf-8").rstrip("\r\n")
        if len(value) < 12 or "\x00" in value:
            raise ValueError
        return value
    except (OSError, UnicodeDecodeError, ValueError):
        raise RuntimeError("facade writer credential is unavailable") from None


@lru_cache(maxsize=1)
def _writer_settings():
    raw = os.environ.get("AMUSANT_FACADE_WRITER_URL", "")
    try:
        parsed = urlparse(raw)
        if (
            parsed.scheme != "http" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.path not in {"", "/"}
            or parsed.query or parsed.fragment or parsed.port != 8644
        ):
            raise ValueError
        address = ipaddress.ip_address(parsed.hostname)
        if (
            address.is_unspecified or address.is_link_local or address.is_multicast
            or address.is_reserved or not (address.is_private or address.is_loopback)
        ):
            raise ValueError
        key = _read_writer_key()
        upstream_key = os.environ.get("API_SERVER_KEY", "")
        if upstream_key and secrets.compare_digest(key, upstream_key):
            raise ValueError
        return address.compressed, parsed.port, key
    except (ValueError, TypeError):
        raise RuntimeError("facade writer endpoint is invalid") from None


def _writer_request(path: str, session_id: str, body: dict):
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return 400, {"error": {"code": "trusted_request_context_unavailable"}}
    if path not in {"/internal/v1/artifacts", "/internal/v1/presentation"}:
        return 404, {"error": {"code": "writer_unavailable"}}
    try:
        host, port, key = _writer_settings()
        data = _json(body).encode("utf-8")
        if len(data) > MAX_REPORT_BYTES * 6 + 4096:
            return 413, {"error": {"code": "report_over_cap"}}
        connection = http.client.HTTPConnection(host, port, timeout=3)
        try:
            connection.request(
                "POST", path, body=data,
                headers={
                    "Authorization": "Bearer " + key,
                    "X-Amusant-Session-ID": session_id,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = connection.getresponse()
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return 502, {"error": {"code": "writer_unavailable"}}
            payload = json.loads(raw or b"{}")
            if not isinstance(payload, dict):
                return 502, {"error": {"code": "writer_unavailable"}}
            return response.status, payload
        finally:
            connection.close()
    except Exception:
        return 502, {"error": {"code": "writer_unavailable"}}


def _safe_error(payload, fallback):
    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and code in _SAFE_ERRORS else fallback


def save_report(args, *, session_id=None, **_kwargs):
    """Send report text to the private Facade writer; no identity/ref is model supplied."""
    if not isinstance(args, dict) or set(args) != {"report"}:
        return _json({"error": "invalid_arguments"})
    report = args.get("report")
    if not isinstance(report, str) or not report.strip():
        return _json({"error": "report_invalid"})
    try:
        encoded = report.encode("utf-8")
    except UnicodeError:
        return _json({"error": "report_invalid"})
    if len(encoded) > MAX_REPORT_BYTES:
        return _json({"error": "report_over_cap"})
    status, payload = _writer_request(
        "/internal/v1/artifacts", session_id, {"report": report}
    )
    if status == 200 and payload.get("committed") is True:
        return _json({"committed": True})
    return _json({"error": _safe_error(payload, "artifact_commit_failed")})


def _valid_presentation(value):
    if not isinstance(value, dict) or set(value) != PRESENTATION_FIELDS:
        return False
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return False
    speak = value.get("speak")
    details = value.get("details_md")
    card = value.get("card")
    if not isinstance(speak, str) or not 1 <= len(speak) <= 240:
        return False
    if not isinstance(details, str) or len(details) > 512:
        return False
    if not isinstance(card, dict) or set(card) != {"title", "lines"}:
        return False
    if not isinstance(card.get("title"), str) or not 1 <= len(card["title"]) <= 80:
        return False
    lines = card.get("lines")
    return (
        isinstance(lines, list)
        and 1 <= len(lines) <= 4
        and all(isinstance(line, str) and 1 <= len(line) <= 120 for line in lines)
    )


def transform_llm_output(response_text, session_id="", **_kwargs):
    """Ask Facade to bind the bounded presentation to its private committed artifact."""
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        return None
    if not isinstance(response_text, str) or len(response_text.encode("utf-8", errors="replace")) > MAX_PRESENTATION_BYTES:
        return None
    try:
        value = json.loads(response_text)
    except (ValueError, TypeError):
        return None
    if not _valid_presentation(value):
        return None
    status, payload = _writer_request(
        "/internal/v1/presentation", session_id, {"presentation": response_text}
    )
    output = payload.get("output") if isinstance(payload, dict) else None
    if status != 200 or not isinstance(output, str):
        return None
    if len(output.encode("utf-8")) > MAX_PRESENTATION_BYTES:
        return None
    try:
        envelope = json.loads(output)
    except (ValueError, TypeError):
        return None
    if (
        not isinstance(envelope, dict)
        or envelope.get("presentation_status") != "ready"
        or not isinstance(envelope.get("artifact_ref"), str)
        or not ARTIFACT_REF_RE.fullmatch(envelope["artifact_ref"])
    ):
        return None
    return output


def register(ctx):
    ctx.register_tool(
        name="save_report",
        toolset="amusant_option_d",
        schema={
            "name": "save_report",
            "description": "Save the complete report to the trusted artifact sink. Pass only report text.",
            "parameters": {
                "type": "object",
                "properties": {"report": {"type": "string"}},
                "required": ["report"],
                "additionalProperties": False,
            },
        },
        handler=save_report,
        description="Write the complete report to the private runtime artifact sink.",
        emoji="📄",
    )
    ctx.register_hook("transform_llm_output", transform_llm_output)
