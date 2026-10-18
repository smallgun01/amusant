"""Load mounted secrets without logging them, then exec the locked Relay venv."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


SECRET_FILES = {
    "GEMINI_API_KEY": "GEMINI_API_KEY_FILE",
    "AMUSANT_DEVICE_TOKEN": "AMUSANT_DEVICE_TOKEN_FILE",
    "AMUSANT_DEVICE_PSKS": "AMUSANT_DEVICE_PSKS_FILE",
    "AMUSANT_FACADE_API_KEY": "AMUSANT_FACADE_API_KEY_FILE",
}


class SecretConfigurationError(ValueError):
    """A mounted secret is missing or unsafe; messages never contain its value."""


def _read_secret(path_value: str) -> str:
    path = Path(path_value)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise SecretConfigurationError("secret must be a regular file")
            if os.name == "posix":
                mode = stat.S_IMODE(metadata.st_mode)
                owner_only = metadata.st_uid == os.geteuid() and mode == 0o400
                shared_group = (
                    metadata.st_uid == 0
                    and metadata.st_gid == 20000
                    and 20000 in os.getgroups()
                    and mode == 0o440
                )
                if not (owner_only or shared_group):
                    raise SecretConfigurationError("secret file ownership or mode is unsafe")
            value = stream.read(8193)
    except SecretConfigurationError:
        raise
    except (OSError, ValueError):
        raise SecretConfigurationError("required secret file is unavailable") from None
    if len(value) > 8192:
        raise SecretConfigurationError("secret file exceeds the size limit")
    try:
        decoded = value.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        raise SecretConfigurationError("secret file is not UTF-8") from None
    if not decoded or "\x00" in decoded:
        raise SecretConfigurationError("secret file is empty or invalid")
    return decoded


def load_secrets(environ: dict[str, str]) -> None:
    """Replace *_FILE pointers with the environment strings expected by Relay."""
    for target, source in SECRET_FILES.items():
        if environ.get(target):
            raise SecretConfigurationError("direct secret environment values are not allowed")
        path = environ.get(source)
        if not path:
            raise SecretConfigurationError("required secret file is not configured")
        environ[target] = _read_secret(path)
        environ.pop(source, None)


def main() -> int:
    try:
        load_secrets(os.environ)
        bind_ip = os.environ.get("RELAY_BIND_IP", "")
        port = os.environ.get("RELAY_LISTEN_PORT", "8443")
        mode = os.environ.get("RELAY_DEVICE_MODE", "sticks3")
        if not bind_ip or not port.isdigit() or mode not in {"sticks3", "pcm"}:
            raise SecretConfigurationError("listener configuration is incomplete")
        os.umask(0o077)
        args = [
            "/app/.venv/bin/python", "-m", "relay.live_bridge",
            "--device-mode", mode, "--host", bind_ip, "--port", port,
        ]
        os.execvpe(args[0], args, os.environ)
    except SecretConfigurationError:
        sys.stderr.write("Relay startup configuration is invalid; secret values were not logged.\n")
        return 78
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
