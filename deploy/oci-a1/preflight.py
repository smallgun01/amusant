"""Fail-closed, offline validation for the OCI Compose inputs (no cloud calls)."""
from __future__ import annotations

import argparse
import ipaddress
from pathlib import Path
import re
import sys


IMAGE_REF = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
RFC1918_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
REQUIRED = (
    "RELAY_IMAGE", "HERMES_IMAGE", "EGRESS_PROXY_IMAGE",
    "RELAY_API_CIDR", "RELAY_BIND_IP", "HERMES_API_BIND_IP",
    "CONTROLLED_EGRESS_CIDR", "HERMES_EGRESS_IP", "RELAY_EGRESS_IP",
    "EGRESS_PROXY_BIND_IP", "RELAY_HOST_PRIVATE_IP", "RELAY_PUBLISH_PORT",
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
    for name in REQUIRED:
        if not values.get(name, "").strip():
            raise PreflightError(f"{name} is required")
    for name in ("RELAY_IMAGE", "HERMES_IMAGE", "EGRESS_PROXY_IMAGE"):
        if not IMAGE_REF.fullmatch(values[name]):
            raise PreflightError(f"{name} must be pinned by a sha256 digest")

    api_net = _network(values, "RELAY_API_CIDR")
    egress_net = _network(values, "CONTROLLED_EGRESS_CIDR")
    if api_net.overlaps(egress_net):
        raise PreflightError("Relay API and controlled egress subnets must not overlap")

    api_ips = {
        name: _literal_ip(values, name)
        for name in ("RELAY_BIND_IP", "HERMES_API_BIND_IP")
    }
    egress_ips = {
        name: _literal_ip(values, name)
        for name in ("HERMES_EGRESS_IP", "RELAY_EGRESS_IP", "EGRESS_PROXY_BIND_IP")
    }
    for name, address in api_ips.items():
        _contained_host(api_net, address, name)
    for name, address in egress_ips.items():
        _contained_host(egress_net, address, name)
    if len(set(api_ips.values())) != len(api_ips) or len(set(egress_ips.values())) != len(egress_ips):
        raise PreflightError("Static service IP addresses must be unique on each network")

    host_ip = _literal_ip(values, "RELAY_HOST_PRIVATE_IP")
    if host_ip.version != 4 or not any(
        host_ip in ipaddress.ip_network(cidr)
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ):
        raise PreflightError("RELAY_HOST_PRIVATE_IP must be an RFC1918 IPv4 address")
    try:
        port = int(values["RELAY_PUBLISH_PORT"])
    except ValueError:
        raise PreflightError("RELAY_PUBLISH_PORT must be between 1 and 65535") from None
    if not 1 <= port <= 65535:
        raise PreflightError("RELAY_PUBLISH_PORT must be between 1 and 65535")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        validate(read_env_file(args.env_file))
    except PreflightError as exc:
        print(f"OCI Compose preflight failed: {exc}", file=sys.stderr)
        return 2
    print("OCI Compose static inputs validated; this does not authorize deployment or prove live gates.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
