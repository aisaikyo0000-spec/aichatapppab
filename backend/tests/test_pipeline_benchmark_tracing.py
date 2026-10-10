import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from run_pipeline_benchmark import CaseTraceCollector  # noqa: E402


def test_prompt_trace_hash_is_deterministic_and_text_is_opt_in():
    messages = [
        {"role": "system", "content": "日本語の指示"},
        {"role": "user", "content": "今日は疲れた"},
    ]

    first = CaseTraceCollector(include_prompt_text=False)
    second = CaseTraceCollector(include_prompt_text=False)
    first.record_model_call(model="gemini-test", messages=messages)
    first.record_model_call(model="gemini-repair", messages=[{"role": "user", "content": "repair"}])
    second.record_model_call(model="gemini-test", messages=messages)

    trace = first.to_dict(input_text="今日は疲れた", intent="report")
    assert trace["input"] == {"text": "今日は疲れた", "intent": "report"}
    assert trace["model_calls"][0]["prompt_sha256"] == second.to_dict(
        input_text="今日は疲れた", intent="report"
    )["model_calls"][0]["prompt_sha256"]
    assert "messages" not in trace["model_calls"][0]
    assert trace["model_calls"][1]["call_index"] == 2
    assert trace["model_calls"][1]["model"] == "gemini-repair"
    assert "messages" not in trace["model_calls"][1]


def test_trace_records_retrieval_metadata_and_keeps_cases_isolated():
    first = CaseTraceCollector(include_prompt_text=True)
    first.record_retrieval(
        [
            {
                "pair_id": "pair-a",
                "score": 0.82,
                "label": "gold",
                "phase": "ongoing",
                "source": "manual",
                "is_same_contact": True,
                "contact_turn": "private counterpart text",
                "self_turn": "private reply text",
            }
        ]
    )
    first.record_model_call(model="gemini-test", messages=[{"role": "user", "content": "case A"}])

    second = CaseTraceCollector(include_prompt_text=False)
    second.record_model_call(model="gemini-test", messages=[{"role": "user", "content": "case B"}])

    trace_a = first.to_dict(input_text="input A", intent="invite")
    trace_b = second.to_dict(input_text="input B", intent="report")
    assert trace_a["retrieved_pairs"] == [
        {
            "pair_id": trace_a["retrieved_pairs"][0]["pair_id"],
            "score": 0.82,
            "label": "gold",
            "phase": "ongoing",
            "source": "manual",
            "same_contact": True,
        }
    ]
    assert trace_a["retrieved_pairs"][0]["pair_id"] != "pair-a"
    assert "pair-a" not in str(trace_a)
    assert "private counterpart text" not in str(trace_a)
    assert "private reply text" not in str(trace_a)
    assert trace_a["model_calls"][0]["messages"][0]["content"] == "case A"
    assert trace_b["retrieved_pairs"] == []
    assert "messages" not in trace_b["model_calls"][0]


def test_trace_records_provider_and_account_route_without_credentials():
    collector = CaseTraceCollector()
    collector.record_model_call(
        provider="gemini",
        account="secondary",
        model="gemini-3.1-flash-lite",
        messages=[{"role": "user", "content": "test"}],
    )

    call = collector.to_dict(input_text="test", intent="report")["model_calls"][0]
    assert call["provider"] == "gemini"
    assert call["account"] == "secondary"
    assert call["model"] == "gemini-3.1-flash-lite"
    assert "api_key" not in call
