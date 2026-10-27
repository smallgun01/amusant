"""Fail-closed, offline validation for the OCI Compose inputs (no cloud calls)."""
from __future__ import annotations

import argparse
import ipaddress
import math
from pathlib import Path
import re
import os
import stat
import sys
from urllib.parse import urlsplit


IMAGE_REF = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
NETWORK_NAMES = (
    "RELAY_API_CIDR", "HERMES_API_CIDR",
    "RELAY_EGRESS_CIDR", "HERMES_EGRESS_CIDR",
)
IMAGE_NAMES = ("RELAY_IMAGE", "FACADE_IMAGE", "HERMES_IMAGE", "EGRESS_PROXY_IMAGE")
RESULT_CONTRACT_VERSION_ENV = "AMUSANT_RESULT_CONTRACT_VERSION"
IP_NAMES_BY_NETWORK = {
    "RELAY_API_CIDR": ("RELAY_BIND_IP", "FACADE_RELAY_BIND_IP"),
    "HERMES_API_CIDR": ("FACADE_HERMES_BIND_IP", "HERMES_API_BIND_IP"),
    "RELAY_EGRESS_CIDR": ("RELAY_EGRESS_IP", "EGRESS_PROXY_RELAY_BIND_IP"),
    "HERMES_EGRESS_CIDR": ("HERMES_EGRESS_IP", "EGRESS_PROXY_HERMES_BIND_IP"),
}
REQUIRED = (
    *IMAGE_NAMES, *NETWORK_NAMES,
    *(name for names in IP_NAMES_BY_NETWORK.values() for name in names),
    "RELAY_HOST_PRIVATE_IP",
)


class PreflightError(ValueError):
    pass


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        raise PreflightError("Compose environment file is unavailable") from None
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        name, separator, value = entry.partition("=")
        if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            raise PreflightError("Compose environment file has an invalid entry")
        if name in values:
            raise PreflightError("Compose environment file repeats a variable")
        values[name] = value.strip()
    return values


def _literal_ip(values: dict[str, str], name: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        return ipaddress.ip_address(values[name])
    except (KeyError, ValueError):
        raise PreflightError(f"{name} must be a literal IP address") from None


def _network(values: dict[str, str], name: str) -> ipaddress.IPv4Network:
    try:
        network = ipaddress.ip_network(values[name], strict=True)
    except (KeyError, ValueError):
        raise PreflightError(f"{name} must be a valid CIDR") from None
    if (
        network.version != 4
        or network.prefixlen > 29
        or not any(network.subnet_of(private) for private in RFC1918_NETWORKS)
    ):
        raise PreflightError(
            f"{name} must be an RFC1918 IPv4 subnet with room for the Docker gateway and services"
        )
    return network


def _contained_host(
    network: ipaddress.IPv4Network,
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    name: str,
) -> None:
    if (
        not isinstance(address, ipaddress.IPv4Address)
        or address not in network
        or address in {network.network_address, network.broadcast_address, network.network_address + 1}
    ):
        raise PreflightError(f"{name} must be a usable host address inside its configured network")


def validate(values: dict[str, str]) -> None:
    # The old selector is not part of the v2-only runtime. Reject a stale v1
    # value in the operator's Compose .env even though current Compose does not
    # pass this variable to Relay. Blank and 2 are harmless during cleanup.
    result_contract_version = values.get(RESULT_CONTRACT_VERSION_ENV, "").strip()
    if result_contract_version not in {"", "2"}:
        raise PreflightError(
            f"{RESULT_CONTRACT_VERSION_ENV} is obsolete; remove it or leave it blank/set it to 2"
        )

    for name in REQUIRED:
        if not values.get(name, "").strip():
            raise PreflightError(f"{name} is required")
    for name in IMAGE_NAMES:
        if not IMAGE_REF.fullmatch(values[name]):
            raise PreflightError(f"{name} must be pinned by a sha256 digest")

    networks = {name: _network(values, name) for name in NETWORK_NAMES}
    network_items = list(networks.items())
    for index, (name, network) in enumerate(network_items):
        for other_name, other in network_items[index + 1:]:
            if network.overlaps(other):
                raise PreflightError(f"{name} and {other_name} subnets must not overlap")

    for network_name, ip_names in IP_NAMES_BY_NETWORK.items():
        network = networks[network_name]
        addresses = {name: _literal_ip(values, name) for name in ip_names}
        for name, address in addresses.items():
            _contained_host(network, address, name)
        if len(set(addresses.values())) != len(addresses):
            raise PreflightError(f"Static service IP addresses on {network_name} must be unique")

    host_ip = _literal_ip(values, "RELAY_HOST_PRIVATE_IP")
    if host_ip.version != 4 or not any(host_ip in private for private in RFC1918_NETWORKS):
        raise PreflightError("RELAY_HOST_PRIVATE_IP must be an RFC1918 IPv4 address")
    # This Compose variable is retained only so older operator .env files remain
    # valid. Compose no longer publishes it, and G1 ingress is fixed at TCP/443
    # by the systemd socket template, independently of this legacy value.
    legacy_publish_port = values.get("RELAY_PUBLISH_PORT", "").strip()
    if legacy_publish_port:
        try:
            port = int(legacy_publish_port)
        except ValueError:
            raise PreflightError("RELAY_PUBLISH_PORT must be a valid legacy TCP port when supplied") from None
        if not 1 <= port <= 65535:
            raise PreflightError("RELAY_PUBLISH_PORT must be a valid legacy TCP port when supplied")


def validate_grok(values: dict[str, str]) -> None:
    """Validate the two-service Grok profile without touching a live host or provider."""
    required = (
        "RELAY_IMAGE", "EGRESS_PROXY_IMAGE", "RELAY_API_CIDR", "CALLBACK_API_CIDR",
        "RELAY_EGRESS_CIDR", "RELAY_BIND_IP", "RELAY_CALLBACK_BIND_IP", "RELAY_EGRESS_IP",
        "EGRESS_PROXY_RELAY_BIND_IP", "RELAY_HOST_PRIVATE_IP", "AMUSANT_CLIENT_ID",
        "AMUSANT_GROK_ROUTINE_URL", "AMUSANT_GROK_CALLBACK_URL",
        "AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS", "OCI_DATA_DIR", "RELAY_STOP_GRACE_PERIOD",
        "AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS", "RELAY_CPU_LIMIT", "RELAY_MEMORY_LIMIT",
        "PROXY_CPU_LIMIT", "PROXY_MEMORY_LIMIT", "GEMINI_API_KEY_FILE", "DEVICE_TOKEN_FILE",
        "DEVICE_PSKS_FILE", "GROK_ROUTINE_TOKEN_FILE",
    )
    for name in required:
        if not values.get(name, "").strip():
            raise PreflightError(f"{name} is required")
    for name in ("RELAY_IMAGE", "EGRESS_PROXY_IMAGE"):
        if not IMAGE_REF.fullmatch(values[name]):
            raise PreflightError(f"{name} must be pinned by a sha256 digest")
    for name in ("GEMINI_API_KEY", "AMUSANT_DEVICE_TOKEN", "AMUSANT_DEVICE_PSKS", "AMUSANT_GROK_ROUTINE_TOKEN", "GROK_ROUTINE_TOKEN"):
        if values.get(name):
            raise PreflightError("direct secret values are not permitted in Compose inputs")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", values["AMUSANT_CLIENT_ID"]):
        raise PreflightError("AMUSANT_CLIENT_ID must be a bounded Owner identity")
    names = ("RELAY_API_CIDR", "CALLBACK_API_CIDR", "RELAY_EGRESS_CIDR")
    networks = {name: _network(values, name) for name in names}
    for index, name in enumerate(names):
        for other in names[index + 1:]:
            if networks[name].overlaps(networks[other]):
                raise PreflightError("Grok private subnets must not overlap")
    pairs = {
        "RELAY_API_CIDR": ("RELAY_BIND_IP",),
        "CALLBACK_API_CIDR": ("RELAY_CALLBACK_BIND_IP",),
        "RELAY_EGRESS_CIDR": ("RELAY_EGRESS_IP", "EGRESS_PROXY_RELAY_BIND_IP"),
    }
    for network_name, ip_names in pairs.items():
        addresses = [_literal_ip(values, name) for name in ip_names]
        for name, address in zip(ip_names, addresses):
            _contained_host(networks[network_name], address, name)
        if len(set(addresses)) != len(addresses):
            raise PreflightError("Grok static service IPs must be distinct")
    host_ip = _literal_ip(values, "RELAY_HOST_PRIVATE_IP")
    if not isinstance(host_ip, ipaddress.IPv4Address) or not any(host_ip in net for net in RFC1918_NETWORKS) or any(host_ip in net for net in networks.values()):
        raise PreflightError("RELAY_HOST_PRIVATE_IP must be private and outside the container subnets")
    for name in ("AMUSANT_GROK_ROUTINE_URL", "AMUSANT_GROK_CALLBACK_URL"):
        try:
            parts = urlsplit(values[name])
            port = parts.port if parts.port is not None else 443
        except ValueError:
            raise PreflightError(f"{name} must be an HTTPS URL") from None
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.query or parts.fragment or port != 443 or len(values[name]) > 512):
            raise PreflightError(f"{name} must be a nonsecret HTTPS URL on port 443")
        labels = parts.hostname.split(".")
        if len(parts.hostname) > 253 or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
            raise PreflightError(f"{name} must use a canonical hostname")
        if parts.hostname == "localhost" or parts.hostname.endswith(".localhost"):
            raise PreflightError(f"{name} must not use a local hostname")
        try:
            literal = ipaddress.ip_address(parts.hostname)
        except ValueError:
            pass
        else:
            if not literal.is_global:
                raise PreflightError(f"{name} must not use a nonpublic IP address")
        if name == "AMUSANT_GROK_CALLBACK_URL":
            if parts.path != "/callback/results" or parts.hostname != values["AMUSANT_GROK_CALLBACK_TRUSTED_HOSTS"].lower():
                raise PreflightError("Callback URL and canonical trusted Host must match")
    if values.get("AMUSANT_GROK_CALLBACK_SERVICE_PORT", "443") != "443":
        raise PreflightError("Callback external HTTPS service port must be 443")
    try:
        shutdown_grace = float(values["AMUSANT_RELAY_SHUTDOWN_GRACE_SECONDS"])
    except ValueError:
        raise PreflightError("Relay shutdown grace must be finite and positive") from None
    stop_grace = re.fullmatch(r"([1-9][0-9]*)s", values["RELAY_STOP_GRACE_PERIOD"])
    if (not math.isfinite(shutdown_grace) or not 0 < shutdown_grace <= 120 or not stop_grace
            or int(stop_grace.group(1)) < shutdown_grace + 10):
        raise PreflightError("Relay stop grace must cover the bounded listener and dispatcher shutdown")
    for name in ("GEMINI_API_KEY_FILE", "DEVICE_TOKEN_FILE", "DEVICE_PSKS_FILE", "GROK_ROUTINE_TOKEN_FILE"):
        path = Path(values[name])
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise PreflightError(f"{name} must be an existing absolute regular secret file")
        if os.name == "posix" and stat.S_IMODE(path.stat().st_mode) not in {0o400, 0o440}:
            raise PreflightError(f"{name} must have a restricted secret file mode")
    data_dir = Path(values["OCI_DATA_DIR"])
    if not data_dir.is_absolute() or data_dir.is_symlink() or (data_dir / "relay").is_symlink() or not (data_dir / "relay").is_dir():
        raise PreflightError("OCI_DATA_DIR/relay must be an existing absolute directory")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--profile", choices=("legacy", "grok"), default="legacy")
    args = parser.parse_args()
    try:
        (validate_grok if args.profile == "grok" else validate)(read_env_file(args.env_file))
    except PreflightError as exc:
        print(f"OCI Compose preflight failed: {exc}", file=sys.stderr)
        return 2
    print("OCI Compose static inputs validated; no cloud or provider calls were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
