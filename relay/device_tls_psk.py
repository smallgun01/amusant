"""TLS 1.2 ECDHE-PSK configuration for the local StickS3 WSS listener."""
from __future__ import annotations

import ipaddress
import re
import ssl
from collections.abc import Mapping

PSK_BYTES = 32
MAX_IDENTITIES = 16
IDENTITY_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
PSK_CIPHERS = "ECDHE-PSK-CHACHA20-POLY1305:ECDHE-PSK-AES128-CBC-SHA256"
PSK_ENV = "AMUSANT_DEVICE_PSKS"


def parse_psks(value: str | None) -> dict[str, bytes]:
    """Parse identity:64-hex entries without echoing identity/key material in errors."""
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("AMUSANT_DEVICE_PSKS must contain at least one identity and key")
    result: dict[str, bytes] = {}
    seen_keys: set[bytes] = set()
    for entry in value.split(","):
        identity, separator, hex_key = entry.strip().partition(":")
        if not separator or not IDENTITY_RE.fullmatch(identity) or len(hex_key) != PSK_BYTES * 2:
            raise ValueError("AMUSANT_DEVICE_PSKS contains an invalid entry")
        try:
            key = bytes.fromhex(hex_key)
        except ValueError:
            raise ValueError("AMUSANT_DEVICE_PSKS contains an invalid entry") from None
        if len(key) != PSK_BYTES or identity in result or key in seen_keys:
            raise ValueError("AMUSANT_DEVICE_PSKS identities and 32-byte keys must be unique")
        result[identity] = key
        seen_keys.add(key)
        if len(result) > MAX_IDENTITIES:
            raise ValueError("AMUSANT_DEVICE_PSKS exceeds the identity limit")
    return result


def validate_hardware_bind_host(host: str) -> None:
    """Hardware mode binds only an explicit, non-loopback private IP address."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("StickS3 mode requires an explicit private IP bind address") from None
    if not address.is_private or address.is_loopback or address.is_unspecified or address.is_link_local:
        raise ValueError("StickS3 mode requires an explicit private LAN address")


def psk_callbacks_supported() -> bool:
    return callable(getattr(ssl.SSLContext, "set_psk_server_callback", None))


def build_server_context(psks: Mapping[str, bytes]) -> ssl.SSLContext:
    """Build the exact TLS 1.2 PSK context; never fall back to certificates or plaintext."""
    if not psk_callbacks_supported():
        raise ValueError("StickS3 TLS-PSK requires Python 3.13+ with ssl PSK callbacks")
    if not psks or any(
        not IDENTITY_RE.fullmatch(identity) or not isinstance(key, bytes) or len(key) != PSK_BYTES
        for identity, key in psks.items()
    ):
        raise ValueError("StickS3 TLS-PSK configuration is invalid")
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.set_ciphers(PSK_CIPHERS)
        context.set_ecdh_curve("X25519")
        context.options |= ssl.OP_NO_COMPRESSION | ssl.OP_NO_RENEGOTIATION

        # Empty key rejects unknown identities during the TLS handshake, before HTTP/WebSocket.
        def key_for(identity: str | None) -> bytes:
            return psks.get(identity or "", b"")

        context.set_psk_server_callback(key_for)
        return context
    except Exception:
        raise ValueError("This Python/OpenSSL build cannot provide the required TLS-PSK suite") from None


def server_context_from_environ(environ: Mapping[str, str]) -> ssl.SSLContext:
    return build_server_context(parse_psks(environ.get(PSK_ENV)))
