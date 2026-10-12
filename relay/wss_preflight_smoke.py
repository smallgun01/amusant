"""One-task, no-model WSS/SG preflight before the ACP smoke admission.

This is for the explicitly authorized P4-T4b one-off path only. It validates
DNS pinning, TLS/hostname, auth rejection, route isolation, TCP/8080 denial,
and an empty per-client DynamoDB TASK# partition before calling acp_smoke
exactly once. It does not provision AWS resources.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import errno
import ipaddress
import json
import logging
import os
import socket
import ssl
from typing import Callable, Mapping
from urllib.parse import urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .acp_client import MAX_ACP_CA_PEM_CHARS, _is_allowed_destination, load_acp_executor
from .acp_smoke import EXIT_CONFIG, EXIT_FAILED, SmokeReport, run_smoke
from .store_factory import build_task_store
from .task_store import _ID_RE

EXIT_PREFLIGHT_FAILED = 5
PROBE_TIMEOUT_SECONDS = 3.0
PREFLIGHT_TIMEOUT_SECONDS = 20.0
PREFLIGHT_CLIENT_PREFIX = "p4-t4b-"
WRONG_BEARER = "invalid-p4-t4b-preflight-token"
NETWORK_PROBE_NAMES = (
    "dns_current_task_ip",
    "untrusted_ca",
    "wrong_san_sni",
    "missing_bearer",
    "invalid_bearer",
    "non_acp_path",
    "relay_8080_unreachable",
)
PROBE_NAMES = ("client_partition_empty", *NETWORK_PROBE_NAMES)

# websockets DEBUG can log headers and frames. Keep probe transport logs sanitized.
_PREFLIGHT_WEBSOCKETS_LOGGER = logging.getLogger("amusant.acp.wss_preflight")
_PREFLIGHT_WEBSOCKETS_LOGGER.setLevel(logging.INFO)


class WssPreflightFailed(RuntimeError):
    """Sanitized, fail-closed preflight error."""

    def __init__(self, code: str, probe: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.probe = probe


@dataclass(frozen=True, slots=True)
class WssPreflightReport:
    exit_code: int
    probes: dict[str, str]
    smoke: SmokeReport | None
    error: str | None


def _validate_configuration(environ: Mapping[str, str]) -> tuple[str, str, int, str, str, str]:
    if (environ.get("AMUSANT_TASK_STORE") or "").strip().lower() != "dynamodb":
        raise WssPreflightFailed("task_store_must_be_dynamodb")
    for name in ("AMUSANT_DDB_ENDPOINT", "AWS_ENDPOINT_URL", "AWS_ENDPOINT_URL_DYNAMODB"):
        if (environ.get(name) or "").strip():
            raise WssPreflightFailed("custom_dynamodb_endpoint_forbidden")
    if (environ.get("AMUSANT_ACP_ALLOW_PLAINTEXT") or "").strip() == "1":
        raise WssPreflightFailed("plaintext_mode_forbidden")

    url = environ.get("AMUSANT_ACP_URL")
    if not isinstance(url, str) or not url.strip():
        raise WssPreflightFailed("invalid_acp_url")
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except ValueError:
        raise WssPreflightFailed("invalid_acp_url") from None
    if (
        parsed.scheme != "wss"
        or parsed.path != "/acp"
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.hostname
        or port != 8443
    ):
        raise WssPreflightFailed("invalid_acp_url")
    hostname = parsed.hostname
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise WssPreflightFailed("acp_hostname_must_be_dns_name")

    client_id = environ.get("AMUSANT_CLIENT_ID")
    if (
        not isinstance(client_id, str)
        or not _ID_RE.fullmatch(client_id)
        or not client_id.startswith(PREFLIGHT_CLIENT_PREFIX)
    ):
        raise WssPreflightFailed("fresh_preflight_client_id_required")

    expected_raw = environ.get("AMUSANT_ACP_EXPECTED_PRIVATE_IP")
    try:
        expected = ipaddress.ip_address(expected_raw or "")
    except ValueError:
        raise WssPreflightFailed("expected_private_ip_required") from None
    if (
        expected.version != 4
        or expected.is_loopback
        or not _is_allowed_destination(expected)
    ):
        raise WssPreflightFailed("expected_private_ip_invalid")

    ca_pem = environ.get("AMUSANT_ACP_CA_PEM")
    if (
        not isinstance(ca_pem, str)
        or not ca_pem.strip()
        or len(ca_pem) > MAX_ACP_CA_PEM_CHARS
        or "-----BEGIN CERTIFICATE-----" not in ca_pem
    ):
        raise WssPreflightFailed("private_ca_required")
    auth_key = environ.get("AMUSANT_ACP_AUTH_KEY")
    if not isinstance(auth_key, str) or len(auth_key.strip()) < 12:
        raise WssPreflightFailed("acp_auth_key_required")

    # Parse the configured CA before any network work while retaining system roots.
    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cadata=ca_pem)
    except (ssl.SSLError, ValueError):
        raise WssPreflightFailed("private_ca_invalid") from None
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise WssPreflightFailed("tls_verification_required")

    # Reuse the production ACP configuration validator; it performs no DNS or dial.
    if load_acp_executor(environ) is None:
        raise WssPreflightFailed("acp_executor_unavailable")
    return url.strip(), hostname, port, expected.compressed, ca_pem, client_id


async def _resolve_expected_task_ip(
    hostname: str,
    port: int,
    expected_ip: str,
    *,
    deadline: float,
) -> str:
    loop = asyncio.get_running_loop()
    remaining = deadline - loop.time()
    if remaining <= 0:
        raise WssPreflightFailed("dns_timeout", "dns_current_task_ip")
    try:
        answers = await asyncio.wait_for(
            loop.getaddrinfo(hostname, port, family=socket.AF_INET, type=socket.SOCK_STREAM),
            timeout=min(remaining, PROBE_TIMEOUT_SECONDS),
        )
    except (OSError, asyncio.TimeoutError):
        raise WssPreflightFailed("dns_lookup_failed", "dns_current_task_ip") from None
    resolved: set[str] = set()
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer[4][0])
        except ValueError:
            raise WssPreflightFailed("dns_answer_invalid", "dns_current_task_ip") from None
        if address.version != 4 or not _is_allowed_destination(address):
            raise WssPreflightFailed("dns_answer_not_private", "dns_current_task_ip")
        resolved.add(address.compressed)
    if resolved != {expected_ip}:
        raise WssPreflightFailed("dns_target_mismatch", "dns_current_task_ip")
    return expected_ip


def _tls_context(ca_pem: str) -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cadata=ca_pem)
    except (ssl.SSLError, ValueError):
        raise WssPreflightFailed("private_ca_invalid") from None
    if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
        raise WssPreflightFailed("tls_verification_required")
    return context


def _connect_kwargs(
    *,
    dial_ip: str,
    port: int,
    server_hostname: str,
    context: ssl.SSLContext,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "ssl": context,
        "open_timeout": PROBE_TIMEOUT_SECONDS,
        "max_size": 64 * 1024,
        "logger": _PREFLIGHT_WEBSOCKETS_LOGGER,
        "host": dial_ip,
        "port": port,
        "server_hostname": server_hostname,
        "proxy": None,
    }
    if headers is not None:
        kwargs["additional_headers"] = headers
    return kwargs


def _has_certificate_verification_error(error: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return True
        current = current.__cause__ or current.__context__
    return False


async def _expect_tls_rejection(uri: str, kwargs: dict[str, object], probe: str) -> None:
    try:
        async with connect(uri, **kwargs):
            pass
    except Exception as error:
        if _has_certificate_verification_error(error):
            return
        raise WssPreflightFailed("tls_probe_inconclusive", probe) from None
    raise WssPreflightFailed("tls_probe_unexpectedly_accepted", probe)


async def _probe_auth_rejected(
    uri: str,
    kwargs: dict[str, object],
    *,
    probe: str,
) -> None:
    try:
        async with connect(uri, **kwargs) as websocket:
            await websocket.send(
                json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}}
                )
            )
            raw = await asyncio.wait_for(websocket.recv(), timeout=PROBE_TIMEOUT_SECONDS)
            if isinstance(raw, str):
                try:
                    response = json.loads(raw)
                except json.JSONDecodeError:
                    raise WssPreflightFailed("auth_probe_inconclusive", probe) from None
                if isinstance(response, dict) and "error" in response and "result" not in response:
                    raise WssPreflightFailed("auth_probe_inconclusive", probe) from None
            raise WssPreflightFailed("bearer_unexpectedly_accepted", probe)
    except InvalidStatus as error:
        if getattr(getattr(error, "response", None), "status_code", None) in {401, 403}:
            return
        raise WssPreflightFailed("auth_probe_unexpected_http_status", probe) from None
    except ConnectionClosed as error:
        # Treat only the expected policy close as bearer rejection; other closes are inconclusive.
        close_code = getattr(getattr(error, "rcvd", None), "code", None)
        if close_code == 1008:
            return
        raise WssPreflightFailed("auth_probe_unexpected_close", probe) from None
    except asyncio.TimeoutError:
        raise WssPreflightFailed("auth_probe_timeout", probe) from None
    except WssPreflightFailed:
        raise
    except Exception:
        raise WssPreflightFailed("auth_probe_transport_failed", probe) from None


async def _probe_non_acp_path(
    hostname: str,
    dial_ip: str,
    port: int,
    context: ssl.SSLContext,
) -> None:
    uri = f"wss://{hostname}:{port}/not-acp"
    kwargs = _connect_kwargs(
        dial_ip=dial_ip,
        port=port,
        server_hostname=hostname,
        context=context,
        headers={"Authorization": f"Bearer {WRONG_BEARER}"},
    )
    try:
        async with connect(uri, **kwargs):
            raise WssPreflightFailed("non_acp_path_unexpectedly_accepted", "non_acp_path")
    except InvalidStatus as error:
        if getattr(getattr(error, "response", None), "status_code", None) == 404:
            return
        raise WssPreflightFailed("non_acp_path_unexpected_status", "non_acp_path") from None
    except WssPreflightFailed:
        raise
    except Exception:
        raise WssPreflightFailed("non_acp_path_transport_failed", "non_acp_path") from None


async def _probe_relay_port_8080_unreachable(dial_ip: str) -> None:
    writer = None
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(dial_ip, 8080), timeout=PROBE_TIMEOUT_SECONDS
        )
        del reader
    except asyncio.TimeoutError:
        return
    except OSError as error:
        if isinstance(error, (ConnectionRefusedError, ConnectionResetError)) or error.errno in {
            errno.ECONNREFUSED,
            errno.ETIMEDOUT,
            errno.EHOSTUNREACH,
            errno.ENETUNREACH,
            errno.ECONNRESET,
            errno.EACCES,
        }:
            return
        raise WssPreflightFailed("relay_8080_probe_inconclusive", "relay_8080_unreachable") from None
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
    raise WssPreflightFailed("relay_8080_reachable", "relay_8080_unreachable")


async def run_network_probes(
    *,
    url: str,
    hostname: str,
    expected_ip: str,
    port: int,
    ca_pem: str,
    deadline: float,
) -> dict[str, str]:
    dial_ip = await _resolve_expected_task_ip(hostname, port, expected_ip, deadline=deadline)
    results = {"dns_current_task_ip": "pass"}
    parsed = urlsplit(url)
    correct_uri = urlunsplit(("wss", parsed.netloc, "/acp", "", ""))

    # System roots intentionally omit the private one-off CA; the private leaf must fail closed.
    await _expect_tls_rejection(
        correct_uri,
        _connect_kwargs(
            dial_ip=dial_ip,
            port=port,
            server_hostname=hostname,
            context=ssl.create_default_context(),
        ),
        "untrusted_ca",
    )
    results["untrusted_ca"] = "pass"

    trusted = _tls_context(ca_pem)
    wrong_hostname = "preflight-wrong-san.invalid"
    await _expect_tls_rejection(
        f"wss://{wrong_hostname}:{port}/acp",
        _connect_kwargs(
            dial_ip=dial_ip,
            port=port,
            server_hostname=wrong_hostname,
            context=trusted,
        ),
        "wrong_san_sni",
    )
    results["wrong_san_sni"] = "pass"

    await _probe_auth_rejected(
        correct_uri,
        _connect_kwargs(
            dial_ip=dial_ip,
            port=port,
            server_hostname=hostname,
            context=trusted,
            headers={},
        ),
        probe="missing_bearer",
    )
    results["missing_bearer"] = "pass"
    await _probe_auth_rejected(
        correct_uri,
        _connect_kwargs(
            dial_ip=dial_ip,
            port=port,
            server_hostname=hostname,
            context=trusted,
            headers={"Authorization": f"Bearer {WRONG_BEARER}"},
        ),
        probe="invalid_bearer",
    )
    results["invalid_bearer"] = "pass"

    await _probe_non_acp_path(hostname, dial_ip, port, trusted)
    results["non_acp_path"] = "pass"
    await _probe_relay_port_8080_unreachable(dial_ip)
    results["relay_8080_unreachable"] = "pass"
    return results


async def run_preflight_smoke(
    environ: Mapping[str, str],
    *,
    store_builder: Callable[..., object] | None = None,
    probe_runner: Callable[..., object] | None = None,
    smoke_runner: Callable[..., object] | None = None,
) -> WssPreflightReport:
    """Run read-only network/queue probes, then the existing one-prompt ACP smoke once."""
    try:
        url, hostname, port, expected_ip, ca_pem, client_id = _validate_configuration(environ)
        smoke_environ = dict(environ)
        smoke_environ["AMUSANT_CLIENT_ID"] = client_id
        builder = store_builder or build_task_store
        store = builder(smoke_environ, max_pending_tasks=1)
        has_client_tasks = getattr(store, "has_client_tasks", None)
        if not callable(has_client_tasks):
            raise WssPreflightFailed("task_store_queue_guard_unavailable")
        if has_client_tasks(client_id):
            return WssPreflightReport(
                EXIT_FAILED, {"client_partition_empty": "failed"}, None, "client_partition_not_empty"
            )
    except WssPreflightFailed as error:
        exit_code = EXIT_PREFLIGHT_FAILED if error.probe else EXIT_CONFIG
        probes = {}
        if error.code == "task_store_queue_guard_unavailable":
            probes["client_partition_empty"] = "unknown"
            exit_code = EXIT_FAILED
        return WssPreflightReport(exit_code, probes, None, error.code)
    except Exception:
        return WssPreflightReport(
            EXIT_CONFIG, {"client_partition_empty": "unknown"}, None, "task_store_or_config_unavailable"
        )

    loop = asyncio.get_running_loop()
    deadline = loop.time() + PREFLIGHT_TIMEOUT_SECONDS
    probes = {"client_partition_empty": "pass"}
    runner = probe_runner or run_network_probes
    try:
        network_probes = await asyncio.wait_for(
            runner(
                url=url,
                hostname=hostname,
                expected_ip=expected_ip,
                port=port,
                ca_pem=ca_pem,
                deadline=deadline,
            ),
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
        )
        probes.update(network_probes)
        if set(network_probes) != set(NETWORK_PROBE_NAMES) or any(
            value != "pass" for value in network_probes.values()
        ):
            return WssPreflightReport(EXIT_PREFLIGHT_FAILED, probes, None, "probe_matrix_incomplete")
    except WssPreflightFailed as error:
        if error.probe:
            probes[error.probe] = "failed"
        return WssPreflightReport(EXIT_PREFLIGHT_FAILED, probes, None, error.code)
    except asyncio.TimeoutError:
        return WssPreflightReport(EXIT_PREFLIGHT_FAILED, probes, None, "preflight_deadline_exceeded")
    except Exception:
        return WssPreflightReport(EXIT_PREFLIGHT_FAILED, probes, None, "network_preflight_failed")

    smoke = smoke_runner or run_smoke
    try:
        report = await smoke(smoke_environ)
    except Exception:
        return WssPreflightReport(EXIT_PREFLIGHT_FAILED, probes, None, "acp_smoke_failed")
    return WssPreflightReport(report.exit_code, probes, report, report.error)


def _print_report(report: WssPreflightReport) -> None:
    smoke = report.smoke
    payload = {
        "exit_code": report.exit_code,
        "preflight": report.probes,
        "status": smoke.status if smoke else None,
        "task_id": smoke.task_id if smoke else None,
        "delivery_state": smoke.delivery_state if smoke else None,
        "error": report.error,
        "has_result": smoke.has_result if smoke else False,
    }
    print(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))


def main() -> None:
    try:
        report = asyncio.run(run_preflight_smoke(os.environ))
    except Exception:
        report = WssPreflightReport(EXIT_PREFLIGHT_FAILED, {}, None, "preflight_failed")
    _print_report(report)
    raise SystemExit(report.exit_code)


if __name__ == "__main__":
    main()
