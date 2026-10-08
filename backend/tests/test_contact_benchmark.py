"""Offline behavior tests for the Contact Bench's style signature helper."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from run_contact_benchmark import style_sig


def test_style_signature_detects_casual_laugh_and_length():
    assert style_sig("おつかれ笑") == {"len": 5, "tame": 1, "keigo": 0, "laugh": 1}


def test_style_signature_detects_polite_reply_without_laugh():
    assert style_sig("お疲れ様でした") == {"len": 7, "tame": 0, "keigo": 1, "laugh": 0}
