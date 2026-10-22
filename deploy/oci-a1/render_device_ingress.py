"""Render sanitized systemd socket/service files for the fixed Relay endpoint."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from preflight import PreflightError, read_env_file, validate


PACKAGE = Path(__file__).resolve().parent
TEMPLATES = {
    "amusant-device-ingress.socket": PACKAGE / "systemd" / "amusant-device-ingress.socket.in",
    "amusant-device-ingress.service": PACKAGE / "systemd" / "amusant-device-ingress.service.in",
}


def render(
    values: dict[str, str], *, max_connections: int = 32, exit_idle_seconds: int = 120
) -> dict[str, str]:
    validate(values)
    if not 2 <= max_connections <= 256:
        raise PreflightError("ingress connection limit must be between 2 and 256")
    if not 1 <= exit_idle_seconds <= 3600:
        raise PreflightError("ingress idle exit must be between 1 and 3600 seconds")
    replacements = {
        "@RELAY_HOST_PRIVATE_IP@": values["RELAY_HOST_PRIVATE_IP"],
        "@RELAY_BIND_IP@": values["RELAY_BIND_IP"],
        # systemd 255 (Ubuntu 24.04) checks set_size > arg_connections_max,
        # while newer releases check >=. Subtracting one keeps the effective
        # limit at or below the requested ceiling on both implementations.
        "@MAX_CONNECTIONS@": str(max_connections - 1),
        "@EXIT_IDLE_SECONDS@": str(exit_idle_seconds),
    }
    output: dict[str, str] = {}
    for name, template_path in TEMPLATES.items():
        content = template_path.read_text(encoding="utf-8")
        for placeholder, value in replacements.items():
            content = content.replace(placeholder, value)
        if "@RELAY_" in content:
            raise PreflightError("ingress unit template contains an unresolved endpoint")
        output[name] = content
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-connections", type=int, default=32)
    parser.add_argument("--exit-idle-seconds", type=int, default=120)
    args = parser.parse_args()
    try:
        units = render(
            read_env_file(args.env_file),
            max_connections=args.max_connections,
            exit_idle_seconds=args.exit_idle_seconds,
        )
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for name, content in units.items():
            (args.output_dir / name).write_text(content, encoding="utf-8", newline="\n")
    except (PreflightError, OSError) as exc:
        print(f"Device ingress render failed: {exc}", file=sys.stderr)
        return 2
    print("Rendered fixed-target systemd ingress units; no host or cloud changes were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

