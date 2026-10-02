"""Step 7 共通: 代表50ケース(JSON)の読み込みヘルパー。"""
import json
from pathlib import Path

CASES_PATH = Path(__file__).with_name("step7_representative_cases.json")


def load_step7_representative_cases() -> list[dict]:
    """代表50ケースを読み込む。各要素は id/intent/contact/candidates/expect_first/notes を持つ。"""
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))
