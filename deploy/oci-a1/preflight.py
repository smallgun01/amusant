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
NETWORK_NAMES = (
    "RELAY_API_CIDR", "HERMES_API_CIDR",
    "RELAY_EGRESS_CIDR", "HERMES_EGRESS_CIDR",
)
IMAGE_NAMES = ("RELAY_IMAGE", "FACADE_IMAGE", "HERMES_IMAGE", "EGRESS_PROXY_IMAGE")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        validate(read_env_file(args.env_file))
    except PreflightError as exc:
        print(f"OCI Compose preflight failed: {exc}", file=sys.stderr)
        return 2
    print("OCI Compose static inputs validated; no cloud or provider calls were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
