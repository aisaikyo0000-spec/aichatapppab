"""Regression tests for missing Tapple strategy metadata during repair."""

from app.ai.prompt import tapple_strategy_contract_guidance
from app.routers.generation import (
    _build_repair_messages,
    _repair_tapple_output_instruction,
    _tapple_strategy_output_violations,
)


def test_existing_invite_repair_keeps_its_action_without_missing_schema_guidance():
    repair = _repair_tapple_output_instruction(3, "invite")

    assert "action=inviteを維持し" in repair
    assert "各候補に相手の意向を尋ねる低圧な誘い" in repair
    assert "欠落だけを理由にwaitまたはcontinueへ固定せず" not in repair
    assert "安全面の懸念や迷いがない場合" in repair


def test_missing_strategy_error_does_not_label_omission_as_weak_evidence():
    violations = _tapple_strategy_output_violations(None)

    assert len(violations) == 1
    assert "strategyの欠落は出力形式の不備" in violations[0]
    assert "欠落だけを理由にaction=wait" in violations[0]


def test_tapple_strategy_contract_separates_schema_failure_from_action_evidence():
    guidance = tapple_strategy_contract_guidance()

    assert "strategyの欠落は出力形式の不備" in guidance
    assert "欠落だけを理由にwait" in guidance
    assert "明示的な断り・迷い・安全上の懸念" in guidance
    assert "会話上の根拠が実際に足りない場合" in guidance


def test_missing_strategy_repair_reassesses_action_without_bypassing_safety_gates():
    strategy_violation = _tapple_strategy_output_violations(None)[0]
    messages = _build_repair_messages(
        [
            {"role": "system", "content": "Tapple JSON contract"},
            {"role": "user", "content": "history"},
        ],
        '{"replies":["reply"]}',
        [strategy_violation],
        candidates=1,
        strategy_mode="tapple",
    )
    repair = messages[-1]["content"]

    assert "strategyの欠落は出力形式の不備" in repair
    assert "欠落だけを理由にwaitまたはcontinueへ固定せず" in repair
    assert "会話全体を読み直してactionを選び" in repair
    assert "具体的な活動への近い時期の希望、本人の先行する関心" in repair
    assert "一緒に行く意思は不明なら、その興味に自然に反応" not in repair
    assert "明示的な断り・迷い・安全上の懸念があればinviteを選ばず" in repair
    assert "会話上の根拠が実際に足りない場合に限りwaitまたはclarify" in repair
    assert "判断根拠が弱い場合はaction=wait" not in repair
