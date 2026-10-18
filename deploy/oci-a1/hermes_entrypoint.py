"""Load the Hermes API key from a mounted file, then start the Runs gateway."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import sys


def main() -> int:
    path_value = os.environ.get("API_SERVER_KEY_FILE", "")
    try:
        if not path_value or os.environ.get("API_SERVER_KEY"):
            raise ValueError
        path = Path(path_value)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError
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
                    raise ValueError
            raw = stream.read(8193)
        if len(raw) > 8192:
            raise ValueError
        key = raw.decode("utf-8").rstrip("\r\n")
        if len(key) < 12 or "\x00" in key:
            raise ValueError
    except (OSError, UnicodeDecodeError, ValueError):
        sys.stderr.write("Hermes startup configuration is invalid; secret values were not logged.\n")
        return 78
    os.environ.pop("API_SERVER_KEY_FILE", None)
    os.environ["API_SERVER_KEY"] = key
    os.umask(0o077)
    os.execvp("hermes", ["hermes", "gateway", "run"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
