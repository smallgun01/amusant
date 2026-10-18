"""Display-only Simplified -> Taiwan Traditional conversion for FAST captions.

Keep the provider transcript, audio, tools, and persisted task data untouched.
OpenCC is initialized once at handler creation and cached across handlers.
Each caption.delta is converted independently to preserve live-stream latency.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from functools import lru_cache

CAPTION_SCRIPT_ENV = "AMUSANT_CAPTION_SCRIPT"
DEFAULT_CAPTION_SCRIPT = "zh-TW"
_VALID_SCRIPTS = frozenset({DEFAULT_CAPTION_SCRIPT, "raw"})


def read_caption_script(environ: Mapping[str, str] | None = None) -> str:
    """Read and validate the server-owned display preference, failing at startup."""
    config = os.environ if environ is None else environ
    script = config.get(CAPTION_SCRIPT_ENV, DEFAULT_CAPTION_SCRIPT)
    if script not in _VALID_SCRIPTS:
        raise ValueError(f"{CAPTION_SCRIPT_ENV} must be 'zh-TW' or 'raw'")
    return script


@lru_cache(maxsize=1)
def _taiwan_converter():
    # Native OpenCC dictionaries are loaded once per Relay process, not per delta.
    from opencc import OpenCC

    return OpenCC("s2twp.json")


def make_caption_normalizer(script: str | None = None) -> Callable[[str], str]:
    """Build a per-handler normalizer without affecting non-caption payloads."""
    selected = read_caption_script() if script is None else script
    if selected not in _VALID_SCRIPTS:
        raise ValueError(f"{CAPTION_SCRIPT_ENV} must be 'zh-TW' or 'raw'")
    if selected == "raw":
        return lambda text: text
    converter = _taiwan_converter()
    return lambda text: converter.convert(text) if text else text
