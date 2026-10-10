"""Helpers for loading local AI credentials without logging their contents."""
from __future__ import annotations

from pathlib import Path


def read_gemini_api_key(path: Path) -> str:
    """Read a Gemini key from an env file or a single-line raw-key file."""
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return ""

    meaningful = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
    for line in meaningful:
        key, separator, value = line.partition("=")
        if separator and key.strip().removeprefix("export ").strip() == "GEMINI_API_KEY":
            return value.strip().strip('"').strip("'")

    if len(meaningful) == 1 and "=" not in meaningful[0]:
        return meaningful[0].strip('"').strip("'")
    return ""
