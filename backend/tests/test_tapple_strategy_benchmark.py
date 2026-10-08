from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_tapple_strategy_benchmark import SCENARIOS, expectation_met


def test_live_tapple_benchmark_covers_positive_ambiguous_and_declined_invites():
    assert {scenario["id"] for scenario in SCENARIOS} == {
        "explicit_interest",
        "ambiguous_interest",
        "decline",
    }


def test_tapple_benchmark_expectations_distinguish_clear_ambiguous_and_declined():
    scenarios = {scenario["id"]: scenario for scenario in SCENARIOS}

    assert expectation_met(scenarios["explicit_interest"], "invite")
    assert not expectation_met(scenarios["explicit_interest"], "wait")
    assert expectation_met(scenarios["ambiguous_interest"], "continue")
    assert expectation_met(scenarios["ambiguous_interest"], "clarify")
    assert expectation_met(scenarios["ambiguous_interest"], "wait")
    assert not expectation_met(scenarios["ambiguous_interest"], "invite")
    assert not expectation_met(scenarios["ambiguous_interest"], "stop")
    assert expectation_met(scenarios["decline"], "stop")
    assert not expectation_met(scenarios["decline"], "invite")


def test_tapple_benchmark_scenarios_include_chat_context_and_allowed_actions():
    for scenario in SCENARIOS:
        assert [turn["sender"] for turn in scenario["messages"]][-1] == "contact"
        assert scenario["allowed_actions"]
        assert "stop" not in scenario["allowed_actions"] or scenario["id"] == "decline"
