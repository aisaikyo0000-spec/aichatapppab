"""CLI-facing import for the shared Gemini credential-file reader."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.credentials import read_gemini_api_key

__all__ = ["read_gemini_api_key"]
