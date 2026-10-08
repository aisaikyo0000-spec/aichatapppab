import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from benchmark_response import generation_response_is_valid


@pytest.mark.parametrize(
    "payload, expected",
    [
        ({"replies": ["A", "B", "C"]}, True),
        ({"replies": [], "question": "好みを教えてください。"}, True),
        ({"replies": ["A"], "question": "好みを教えてください。"}, False),
        ({"replies": ["A", "B", "C"], "question": "好みを教えてください。"}, False),
        ({"replies": [], "question": {"secret": "unreviewed"}}, False),
        ({"replies": [], "question": "  "}, False),
        ({"replies": ["A", "", "C"]}, False),
        ({"replies": "A", "question": "好みを教えてください。"}, False),
    ],
)
def test_generation_response_shape_is_exclusive_and_well_typed(payload, expected):
    assert generation_response_is_valid(payload) is expected
