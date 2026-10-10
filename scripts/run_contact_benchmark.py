"""Step 18 §22: 3 contacts with contrasting Gold, same message, verify adaptation."""
import argparse
import json
import re
import sys
import tempfile
import time
import unicodedata
from statistics import median
from pathlib import Path

sys.path.insert(0, "backend")
from fastapi.testclient import TestClient
from app import config, database
from app.ai import factory
from app.learning import style
from app.routers import generation
from api_key_file import read_gemini_api_key
from benchmark_config import (
    add_active_account_argument,
    build_gemini_benchmark_config,
    isolate_benchmark_logging,
    load_gemini_benchmark_route,
    record_gemini_benchmark_success,
    successful_gemini_benchmark_route,
)
from benchmark_response import benchmark_run_state, extract_api_error_code


CONTACTS = {
    # A: short/tame/laugh-heavy
    "A": [
        ("今週ずっと忙しくて疲れた", "おつかれ笑"),
        ("昨日映画見てきた", "映画いいね笑"),
        ("カフェ行ってきたよ", "いいな笑"),
        ("コンビニで新作スイーツ見つけた", "新作気になる笑"),
        ("天気いいね", "ほんとだね笑"),
        ("週末はゆっくりできそう", "いいじゃん笑"),
    ],
    # B: polite/long/no-emoji
    "B": [
        ("今週ずっと忙しくて疲れた", "お仕事お疲れ様です！ゆっくり休んでくださいね。"),
        ("昨日映画見てきた", "映画いいですね！どんな作品を見たんですか？"),
        ("カフェ行ってきたよ", "カフェに行かれたんですね！いい時間を過ごせましたか？"),
        ("コンビニで新作スイーツ見つけた", "新作スイーツ気になりますね！美味しそうですね。"),
        ("天気いいね", "今日は過ごしやすい天気ですね！"),
        ("週末はゆっくりできそう", "ゆっくり過ごせそうでよかったです！何か予定はありますか？"),
    ],
    # C: medium length, with polite and casual replies mixed
    "C": [
        ("今週ずっと忙しくて疲れた", "それは大変だったね、今日はゆっくり休んでね"),
        ("昨日映画見てきた", "映画いいですね、楽しそう！"),
        ("カフェ行ってきたよ", "カフェいいな、ゆっくりできそう"),
        ("コンビニで新作スイーツ見つけた", "それ気になる！どんな味だろう"),
        ("天気いいね", "ほんとだね、気持ちよさそう！"),
        ("週末はゆっくりできそう", "よかったですね！ゆっくりできそう"),
    ],
}

PROBE = "仕事で疲れた"
MIN_GOLD_PAIRS_PER_CONTACT = 6
MAX_GOLD_PAIRS_PER_CONTACT = 12
MAX_GOLD_MESSAGE_CHARS = 2000
MAX_GOLD_FIXTURE_BYTES = 256_000
CONTACT_STYLE_DIAGNOSTIC_CATEGORIES = frozenset({
    "length",
    "polite_register",
    "conversational_register",
    "laugh_marker",
    "echo",
    "recommendation_overlap",
})
app = None
_HARD_VALIDATION_STAGES = ("initial", "hard_repair", "style_repair", "style_followup")
_QUALITY_GATE_STAGES = ("style_repair", "style_followup")
_QUALITY_GATE_CATEGORIES = frozenset({
    "new_style_mismatch",
    "style_scores_unavailable",
    "mean_naturalness_regression",
    "worst_reply_naturalness_regression",
    "secondary_style_score_tradeoff",
    "required_recommendation_overlap_not_reduced",
    "no_material_style_gain",
    "unclassified_quality_rejection",
})
# Exact allowlisted validator phrases only. Unmatched content is never retained.
_HARD_VALIDATION_MARKERS = (
    ("reply_count", "出力案数が"),
    ("empty_reply", "が空文字です"),
    ("invalid_reply_structure", "出力が正しいJSON形式"),
    ("ai_question_tag", "AI_QUESTIONタグ"),
    ("fragmented_reply", "1つの返信が3分割されて出力されています"),
    ("tone_conflict", "タメ口指定と矛盾する敬語"),
    ("tone_conflict", "敬語指定と矛盾するカジュアル語尾"),
    ("duplicate_candidates", "内容・表現が重複しています"),
    ("echo_or_repeat", "直近の自分のメッセージをそのまま繰り返しています"),
    ("echo_or_repeat", "相手の状態を言い換えただけ"),
    ("echo_or_repeat", "すでに伝えられた内容を聞き返しています"),
    ("excessive_questioning", "質問を重ねすぎています"),
    ("unverified_self_experience", "本人の経験を確認できる情報がありません"),
    ("unverified_self_preference", "本人の好みを確認できる情報がありません"),
    ("unverified_self_schedule", "本人の予定・生活習慣を確認できる情報がありません"),
    ("unverified_self_schedule", "確認済みの予定と逆の空き状況"),
    ("unverified_self_schedule", "相手の質問と異なる日程について答えています"),
    ("unsupported_self_fact", "本人の未確認の希望を追加しています"),
    ("unsupported_self_fact", "本人の未確認の習慣・傾向を追加しています"),
    ("unsupported_self_fact", "本人の近況を確認できる情報がありません"),
    ("unsupported_time_context", "相手の時間情報を確認できる情報がありません"),
    ("unsupported_work_context", "仕事の状況を確認できる情報がありません"),
    ("unsupported_work_context", "勤務状況を確認できる情報がありません"),
    ("unsupported_recurring_context", "相手の継続的な状況を確認できる情報がありません"),
    ("unsupported_effort_context", "相手の負荷や疲れの理由を確認できる情報がありません"),
    ("unsupported_leave_context", "休日・休暇を確認できる情報がありません"),
    ("unsupported_status", "状況を確認できる情報がありません"),
    ("unresolved_reference", "不明な参照先への確認になっていません"),
    ("unresolved_reference", "参照先を確認できる情報がありません"),
    ("unresolved_reference", "参照先が会話履歴から特定できません"),
    ("unsafe_pressure", "催促や返信を問い詰める表現"),
    ("unsafe_pressure", "催促表現"),
    ("language_quality", "形式的な報告表現で始まっています"),
    ("language_quality", "助詞が抜けた不自然な表現があります"),
    ("language_quality", "助詞が連続して重複しています"),
    ("tapple_unsafe_safety_assurance", "実際の安全性を保証する表現があります"),
    ("tapple_private_place", "自宅・ホテル・個室などで会う提案があります"),
    ("tapple_disrespectful_reply", "相手を侮辱する表現があります"),
    ("tapple_external_contact_request", "外部連絡先の交換や移動を促す表現があります"),
    ("tapple_pressure_after_hesitation", "会うことへの断り・迷い・安全面の懸念に対して考え直すよう求めています"),
    ("tapple_unpermitted_invitation", "会う誘いを返信文へ混ぜています"),
)


def _new_contact_style_diagnostics() -> dict:
    """Aggregate-only trace of the optional same-contact style repair path."""
    return {
        "soft_issues_detected": False,
        "issue_categories": set(),
        "repair_attempted": 0,
        "repair_accepted": 0,
        "repair_rejected": 0,
        "rejection_reasons": set(),
        "style_check_invocations": 0,
        "initial_hard_violations_existed": None,
        "checker_profile": None,
        "same_contact_gold_profile": None,
        "observed_candidate_aggregates": None,
        "sensitive_context_skip": False,
        "skip_reason_categories": set(),
        "repair_reached_parse": False,
        "repair_parse_succeeded": False,
        "repair_reached_hard_validation": False,
        "repair_hard_validation_passed": False,
        "repair_reached_quality_gate": False,
        "hard_validation_rejections": {
            stage: {
                "count": 0,
                "categories": set(),
                "category_counts": {},
                "candidate_category_counts": {},
                "last_outcome": "not_run",
            }
            for stage in _HARD_VALIDATION_STAGES
        },
        "validation_attempts": [],
        "last_hard_validation_attempt": {"stage": None, "outcome": "not_run"},
        "returned_candidates": None,
        "quality_gate_rejections": {
            stage: {"count": 0, "categories": set()} for stage in _QUALITY_GATE_STAGES
        },
    }


def _record_hard_validation_rejection(diagnostics: dict, stage: str, violations) -> None:
    """Record only fixed categories; never retain validator text."""
    if stage not in _HARD_VALIDATION_STAGES or not violations:
        return
    entry = diagnostics["hard_validation_rejections"][stage]
    entry["count"] += 1
    categories = set()
    for violation in violations:
        label = next(
            (category for category, marker in _HARD_VALIDATION_MARKERS if marker in violation),
            "other_validation_violation",
        )
        categories.add(label)
        entry["category_counts"][label] = entry["category_counts"].get(label, 0) + 1
        candidate_match = re.match(
            r"\s*案\s*([1-3])(?!\d)(?:と案\s*([1-3])(?!\d))?", violation
        )
        candidate_numbers = (
            {number for number in candidate_match.groups() if number}
            if candidate_match else set()
        )
        for candidate_number in candidate_numbers:
            candidate_counts = entry["candidate_category_counts"].setdefault(
                candidate_number, {}
            )
            candidate_counts[label] = candidate_counts.get(label, 0) + 1
    entry["categories"].update(categories)


def _record_validation_attempt(
    diagnostics: dict, stage: str, replies: list[str], violations
) -> None:
    """Record aggregate-only evidence for each hard-validation stage."""
    outcome = "rejected" if violations else "passed"
    if violations:
        _record_hard_validation_rejection(diagnostics, stage, violations)
    categories = set()
    for violation in violations or ():
        categories.add(next(
            (category for category, marker in _HARD_VALIDATION_MARKERS if marker in violation),
            "other_validation_violation",
        ))
    diagnostics["validation_attempts"].append({
        "stage": stage if stage in _HARD_VALIDATION_STAGES else "unknown",
        "candidate_aggregates": _safe_stage_candidate_aggregates(replies),
        "outcome": outcome,
        "categories": sorted(categories),
    })
    if stage in _HARD_VALIDATION_STAGES:
        diagnostics["hard_validation_rejections"][stage]["last_outcome"] = outcome
    diagnostics["last_hard_validation_attempt"] = {
        "stage": stage if stage in _HARD_VALIDATION_STAGES else None,
        "outcome": outcome,
    }


def _candidate_set_key(replies) -> tuple[str, ...]:
    """Build a transient, order-independent comparison key; never serialize it."""
    return tuple(sorted(reply or "" for reply in replies))


def _record_returned_candidates(diagnostics: dict, replies: list[str], validation_candidates) -> None:
    """Summarize returned replies and link only an exact in-memory validation match."""
    returned_key = _candidate_set_key(replies)
    match = next((
        (stage, "passed")
        for stage, candidate_key, passed in reversed(validation_candidates)
        if passed and candidate_key == returned_key
    ), (None, "not_linked"))
    validation = {"stage": match[0], "outcome": match[1]}
    diagnostics["returned_candidates"] = {
        "candidate_aggregates": _safe_stage_candidate_aggregates(replies),
        "validation": validation,
    }
    # Keep the legacy field, but make it refer to returned candidates rather than
    # whichever optional repair happened to be validated last.
    diagnostics["final_hard_validation"] = validation


def _record_quality_gate_rejection(diagnostics: dict, stage: str, category: str) -> None:
    if stage not in _QUALITY_GATE_STAGES:
        return
    entry = diagnostics["quality_gate_rejections"][stage]
    entry["count"] += 1
    entry["categories"].add(
        category if category in _QUALITY_GATE_CATEGORIES else "unclassified_quality_rejection"
    )


def _quality_gate_rejection_category(previous, candidate, context, issues, candidate_issues=None) -> str:
    """Explain a rejected optional style repair using fixed labels only."""
    try:
        previous_categories = generation._contact_style_issue_categories(issues)
        observed_candidate_issues = (
            candidate_issues if candidate_issues is not None
            else generation._contact_style_soft_repair_issues(candidate, context)
        )
        candidate_categories = generation._contact_style_issue_categories(observed_candidate_issues)
        if not candidate_categories.issubset(previous_categories):
            return "new_style_mismatch"
        score_pair = generation._contact_style_repair_scores(
            previous, candidate, context, previous_categories
        )
        if score_pair is None:
            return "style_scores_unavailable"
        if context.counterpart_message:
            question_rate = generation._contact_style_question_rate(
                context.profile, suppress=context.previous_self_ended_with_question
            )
            prior_mean = generation._contact_style_naturalness_score(
                previous, context.counterpart_message,
                counterpart_intent=context.counterpart_intent, gold_question_rate=question_rate,
            )
            candidate_mean = generation._contact_style_naturalness_score(
                candidate, context.counterpart_message,
                counterpart_intent=context.counterpart_intent, gold_question_rate=question_rate,
            )
            if candidate_mean < prior_mean - generation._CONTACT_STYLE_MEAN_NATURALNESS_TOLERANCE:
                return "mean_naturalness_regression"
            prior_worst = generation._contact_style_naturalness_scores(
                previous, context.counterpart_message,
                counterpart_intent=context.counterpart_intent, gold_question_rate=question_rate,
            )
            candidate_worst = generation._contact_style_naturalness_scores(
                candidate, context.counterpart_message,
                counterpart_intent=context.counterpart_intent, gold_question_rate=question_rate,
            )
            if (prior_worst and candidate_worst and min(candidate_worst) <
                    min(prior_worst) - generation._CONTACT_STYLE_WORST_NATURALNESS_TOLERANCE):
                return "worst_reply_naturalness_regression"
        previous_scores, candidate_scores = score_pair
        if any(candidate_scores[key] > score + generation._CONTACT_STYLE_SCORE_TRADEOFF_TOLERANCE
               for key, score in previous_scores.items()):
            return "secondary_style_score_tradeoff"
        if any(candidate_scores[key] >= previous_scores[key] - 1e-9
               for key in ("recommendation_overlap",) if key in previous_scores):
            return "required_recommendation_overlap_not_reduced"
        if not any(candidate_scores[key] < score - 1e-9
                   for key, score in previous_scores.items()):
            return "no_material_style_gain"
    except Exception:
        pass
    return "unclassified_quality_rejection"


def _reset_contact_repair_trace(state: dict) -> None:
    """Clear transient repair state so request/contact exits cannot contaminate later work."""
    state["pending_style_repair_parse"] = False
    state["pending_validation_stage"] = None
    state["awaiting_repair_parse"] = False
    state["validation_candidates"] = []


def _finalize_contact_style_diagnostics(diagnostics: dict) -> dict:
    """Return JSON-safe aggregate fields only; never include message text."""
    attempted = int(diagnostics["repair_attempted"])
    accepted = int(diagnostics["repair_accepted"])
    rejected = int(diagnostics["repair_rejected"])
    undecided = max(0, attempted - accepted - rejected)
    reasons = set(diagnostics["rejection_reasons"])
    if undecided:
        reasons.add("repair_did_not_reach_quality_decision")
    return {
        "soft_issues_detected": bool(diagnostics["soft_issues_detected"]),
        "issue_categories": sorted(diagnostics["issue_categories"]),
        "repair_attempted": attempted,
        "repair_accepted": accepted,
        "repair_rejected": rejected,
        "rejection_reasons": sorted(reasons),
        "style_check_invocations": int(diagnostics["style_check_invocations"]),
        "style_check_status": diagnostics.get("style_check_status", "not_run"),
        "initial_hard_violations_existed": diagnostics["initial_hard_violations_existed"],
        "checker_profile": diagnostics["checker_profile"],
        "same_contact_gold_profile": diagnostics["same_contact_gold_profile"],
        "observed_candidate_aggregates": diagnostics["observed_candidate_aggregates"],
        "validation_attempts": list(diagnostics["validation_attempts"]),
        "last_hard_validation_attempt": diagnostics["last_hard_validation_attempt"],
        "returned_candidates": diagnostics["returned_candidates"],
        "sensitive_context_skip": bool(diagnostics["sensitive_context_skip"]),
        "skip_reason_categories": sorted(diagnostics["skip_reason_categories"]),
        "repair_stages": {
            "reached_parse": bool(diagnostics["repair_reached_parse"]),
            "parse_succeeded": bool(diagnostics["repair_parse_succeeded"]),
            "reached_hard_validation": bool(diagnostics["repair_reached_hard_validation"]),
            "hard_validation_passed": bool(diagnostics["repair_hard_validation_passed"]),
            "reached_quality_gate": bool(diagnostics["repair_reached_quality_gate"]),
        },
        "final_hard_validation": diagnostics.get(
            "final_hard_validation", {"stage": None, "outcome": "not_run"}
        ),
        "hard_validation_rejections": {
            stage: {
                "count": int(diagnostics["hard_validation_rejections"][stage]["count"]),
                "categories": sorted(diagnostics["hard_validation_rejections"][stage]["categories"]),
                "category_counts": dict(
                    sorted(diagnostics["hard_validation_rejections"][stage]["category_counts"].items())
                ),
                "candidate_category_counts": {
                    candidate: dict(sorted(category_counts.items()))
                    for candidate, category_counts in sorted(
                        diagnostics["hard_validation_rejections"][stage][
                            "candidate_category_counts"
                        ].items(),
                        key=lambda item: int(item[0]),
                    )
                },
                "last_outcome": diagnostics["hard_validation_rejections"][stage]["last_outcome"],
            }
            for stage in _HARD_VALIDATION_STAGES
        },
        "quality_gate_rejections": {
            stage: {
                "count": int(diagnostics["quality_gate_rejections"][stage]["count"]),
                "categories": sorted(diagnostics["quality_gate_rejections"][stage]["categories"]),
            }
            for stage in _QUALITY_GATE_STAGES
        },
    }


def _safe_style_profile_summary(profile) -> dict | None:
    """Extract only numeric profile aggregates; do not serialize profile payloads."""
    if profile is None:
        return None
    return {
        "sample_count": int(getattr(profile, "sample_count", 0)),
        "keigo_ratio": round(float(getattr(profile, "keigo_ratio", 0.0)), 3),
        "hybrid_ratio": round(float(getattr(profile, "hybrid_ratio", 0.0)), 3),
        "tame_ratio": round(float(getattr(profile, "tame_ratio", 0.0)), 3),
        "laugh_ratio": round(float(getattr(profile, "laugh_ratio", 0.0)), 3),
        "char_median": int(getattr(profile, "char_median", 0)),
        "char_p25": int(getattr(profile, "char_p25", 0)),
        "char_p75": int(getattr(profile, "char_p75", 0)),
    }


def _safe_candidate_aggregates(replies: list[str]) -> dict:
    """Summarize tone, laugh-marker, and length without retaining candidate text."""
    values = [reply or "" for reply in replies]
    tones = [style._classify_tone_exclusive(reply) for reply in values]
    count = len(values)
    lengths = [len(reply) for reply in values]
    laugh_pattern = re.compile(r"(?:笑+|(?<![A-Za-z])[wW]+(?![A-Za-z]))")
    return {
        "candidate_count": count,
        "tone_ratios": {
            tone: round(sum(value == tone for value in tones) / count, 3) if count else 0.0
            for tone in ("keigo", "hybrid", "tame")
        },
        "laugh_ratio": round(sum(bool(laugh_pattern.search(value)) for value in values) / count, 3)
        if count else 0.0,
        "length": {
            "mean_chars": round(sum(lengths) / count, 1) if count else 0.0,
            "median_chars": float(median(lengths)) if count else 0.0,
        },
    }


def _safe_stage_candidate_aggregates(replies: list[str]) -> dict:
    """Add text-free length distribution and quartiles to the legacy summary."""
    summary = _safe_candidate_aggregates(replies)
    lengths = sorted(len(reply or "") for reply in replies)

    def percentile(fraction: float) -> int:
        if not lengths:
            return 0
        position = (len(lengths) - 1) * fraction
        lower = int(position)
        upper = min(lower + 1, len(lengths) - 1)
        value = lengths[lower] * (upper - position) + lengths[upper] * (position - lower)
        return int(round(value))

    summary["length_distribution_chars"] = lengths
    summary["length"]["p25_chars"] = percentile(0.25)
    summary["length"]["p75_chars"] = percentile(0.75)
    return summary


def contact_quality_status(*, generation_complete: bool, expected_replies: int) -> dict:
    """Keep generation completion separate from the required human quality judgment."""
    return {
        "status": "manual_review_required" if generation_complete else "generation_incomplete",
        "adaptation_pass": False,
        "expected_replies": expected_replies,
    }


def load_contact_fixture(path: Path) -> dict[str, list[tuple[str, str]]]:
    """Load an external, anonymous A/B/C gold fixture without copying it into artifacts."""
    try:
        fixture_size = path.stat().st_size
    except OSError as exc:
        raise ValueError("fixture file could not be read as JSON") from exc
    if fixture_size > MAX_GOLD_FIXTURE_BYTES:
        raise ValueError("fixture file exceeds 256000 bytes")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("fixture file could not be read as JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("fixture must be a JSON object")
    raw_contacts = payload.get("contacts")
    if not isinstance(raw_contacts, dict) or set(raw_contacts) != {"A", "B", "C"}:
        raise ValueError("fixture contacts must use anonymous labels A, B, C")

    contacts: dict[str, list[tuple[str, str]]] = {}
    for label in ("A", "B", "C"):
        raw_pairs = raw_contacts[label]
        if not isinstance(raw_pairs, list) or len(raw_pairs) < MIN_GOLD_PAIRS_PER_CONTACT:
            raise ValueError(f"contact {label} needs at least six gold pairs")
        if len(raw_pairs) > MAX_GOLD_PAIRS_PER_CONTACT:
            raise ValueError(f"contact {label} accepts at most 12 gold pairs")
        pairs: list[tuple[str, str]] = []
        for pair in raw_pairs:
            if not isinstance(pair, dict):
                raise ValueError(f"contact {label} pairs must be objects")
            incoming = pair.get("incoming")
            gold = pair.get("gold")
            if not isinstance(incoming, str) or not incoming.strip():
                raise ValueError(f"contact {label} has an empty incoming message")
            if not isinstance(gold, str) or not gold.strip():
                raise ValueError(f"contact {label} has an empty gold reply")
            if len(incoming) > MAX_GOLD_MESSAGE_CHARS or len(gold) > MAX_GOLD_MESSAGE_CHARS:
                raise ValueError(f"contact {label} contains a message over 2000 characters")
            pairs.append((incoming.strip(), gold.strip()))
        contacts[label] = pairs
    return contacts


def seed_and_generate(
    client,
    key,
    model,
    delay_seconds,
    probe,
    secondary_key="",
    route_state_path=None,
    active_account="primary",
    contacts=None,
):
    contact_fixtures = CONTACTS if contacts is None else contacts
    ai_config = build_gemini_benchmark_config(
        primary_key=key,
        secondary_key=secondary_key,
        model=model,
        active_account=active_account,
    )
    if route_state_path is not None:
        load_gemini_benchmark_route(ai_config, route_state_path)
    original_get_ai_config = generation.get_ai_config
    original_factory_get_provider = factory.get_provider
    original_generation_factory_get_provider = generation.factory.get_provider
    generation.get_ai_config = lambda: ai_config
    successful_attempts = []
    real_get_provider = original_factory_get_provider

    def tracking_get_provider(provider_name, api_key):
        provider = real_get_provider(provider_name, api_key)
        original_generate = provider.generate

        def tracking_generate(**kwargs):
            response = original_generate(**kwargs)
            successful_attempts.append((api_key, kwargs["model"]))
            return response

        provider.generate = tracking_generate
        return provider

    factory.get_provider = tracking_get_provider
    generation.factory.get_provider = tracking_get_provider
    out = {}
    active_diagnostics = None
    original_soft_issues = generation._contact_style_soft_repair_issues
    original_repair_messages = generation._build_repair_messages
    original_repair_improves = generation._contact_style_repair_improves
    original_parse_replies = generation._parse_replies_strict
    original_validate_candidates = generation.validate_candidate_replies
    repair_trace = {
        "pending_style_repair_parse": False,
        "pending_validation_stage": None,
        "pending_quality_gate_stage": None,
        "style_repair_calls": 0,
        "awaiting_repair_parse": False,
        # Exact candidates stay in memory only long enough to associate the returned
        # candidate set with a validation attempt. This field is never serialized.
        "validation_candidates": [],
    }

    sensitive_context_pattern = re.compile(
        r"(?:入院|手術|危篤|葬儀|亡くな|亡く|悲し|不安|つら|辛い|自傷|消えたい|病気|事故)"
    )

    def tracking_soft_issues(replies, context):
        skip_reason = None
        if active_diagnostics is not None:
            active_diagnostics["style_check_invocations"] += 1
            profile = context.profile
            active_diagnostics["checker_profile"] = _safe_style_profile_summary(profile)
            if active_diagnostics["same_contact_gold_profile"] is None and profile is not None:
                active_diagnostics["same_contact_gold_profile"] = _safe_style_profile_summary(profile)
            if active_diagnostics["observed_candidate_aggregates"] is None:
                active_diagnostics["observed_candidate_aggregates"] = _safe_candidate_aggregates(replies)
            if len(replies) < 2:
                skip_reason = "fewer_than_two_candidates"
                active_diagnostics["skip_reason_categories"].add(skip_reason)
            elif profile is None or getattr(profile, "sample_count", 0) < 5:
                skip_reason = "insufficient_same_contact_gold"
                active_diagnostics["skip_reason_categories"].add(skip_reason)
            else:
                context_text = unicodedata.normalize(
                    "NFKC",
                    f"{context.counterpart_message}\n{context.conversation_context}",
                )
                if sensitive_context_pattern.search(context_text):
                    active_diagnostics["sensitive_context_skip"] = True
                    skip_reason = "sensitive_context"
                    active_diagnostics["skip_reason_categories"].add(skip_reason)
        issues = original_soft_issues(replies, context)
        if active_diagnostics is not None and issues:
            categories = generation._contact_style_issue_categories(issues)
            recognized = categories & CONTACT_STYLE_DIAGNOSTIC_CATEGORIES
            if recognized:
                active_diagnostics["soft_issues_detected"] = True
                active_diagnostics["issue_categories"].update(recognized)
        if active_diagnostics is not None:
            active_diagnostics["style_check_status"] = (
                f"skipped_{skip_reason}" if skip_reason else
                "mismatch_detected" if issues else "no_mismatch"
            )
        return issues

    def tracking_parse_replies(raw_output, *args, **kwargs):
        if not repair_trace["awaiting_repair_parse"]:
            # A new parsed response starts a fresh validation stage. Any repeated
            # validation of the previous repair (for safe clarification replacement)
            # happens without another parse and keeps its repair stage intact.
            repair_trace["pending_validation_stage"] = None
            repair_trace["pending_style_repair_parse"] = False
            repair_trace["pending_quality_gate_stage"] = None
            return original_parse_replies(raw_output, *args, **kwargs)
        repair_trace["awaiting_repair_parse"] = False
        try:
            parsed = original_parse_replies(raw_output, *args, **kwargs)
        except Exception:
            if active_diagnostics is not None:
                active_diagnostics["repair_reached_parse"] = True
                active_diagnostics["repair_parse_succeeded"] = False
                active_diagnostics["rejection_reasons"].add("repair_parse_failed")
            _reset_contact_repair_trace(repair_trace)
            repair_trace["pending_quality_gate_stage"] = None
            raise
        if active_diagnostics is not None:
            active_diagnostics["repair_reached_parse"] = True
            active_diagnostics["repair_parse_succeeded"] = bool(parsed)
            if not parsed:
                active_diagnostics["rejection_reasons"].add("repair_parse_failed")
        if not parsed:
            _reset_contact_repair_trace(repair_trace)
            repair_trace["pending_quality_gate_stage"] = None
        return parsed

    def tracking_validate_candidates(replies, *args, **kwargs):
        stage = repair_trace.get("pending_validation_stage") or "initial"
        try:
            violations = original_validate_candidates(replies, *args, **kwargs)
        except Exception:
            if active_diagnostics is not None and repair_trace["pending_style_repair_parse"]:
                active_diagnostics["repair_reached_hard_validation"] = True
                active_diagnostics["rejection_reasons"].add("repair_hard_validation_failed")
            _reset_contact_repair_trace(repair_trace)
            raise
        if active_diagnostics is not None:
            outcome = "rejected" if violations else "passed"
            _record_validation_attempt(active_diagnostics, stage, replies, violations)
            repair_trace["validation_candidates"].append(
                (stage, _candidate_set_key(replies), not bool(violations))
            )
        if active_diagnostics is not None and active_diagnostics["initial_hard_violations_existed"] is None:
            active_diagnostics["initial_hard_violations_existed"] = bool(violations)
        if active_diagnostics is not None and repair_trace["pending_style_repair_parse"]:
            active_diagnostics["repair_reached_hard_validation"] = True
            active_diagnostics["repair_hard_validation_passed"] = not bool(violations)
            if violations:
                active_diagnostics["rejection_reasons"].add("repair_hard_validation_rejected")
        return violations

    def tracking_repair_messages(messages, raw_output, violations, candidates, strategy_mode="none"):
        stage = None
        if active_diagnostics is not None:
            categories = generation._contact_style_issue_categories(violations)
            recognized = categories & CONTACT_STYLE_DIAGNOSTIC_CATEGORIES
            if recognized:
                active_diagnostics["soft_issues_detected"] = True
                active_diagnostics["issue_categories"].update(recognized)
                active_diagnostics["repair_attempted"] += 1
                active_diagnostics["style_check_status"] = "mismatch_detected"
                repair_trace["pending_style_repair_parse"] = True
                repair_trace["style_repair_calls"] += 1
                stage = "style_repair" if repair_trace["style_repair_calls"] == 1 else "style_followup"
                repair_trace["pending_quality_gate_stage"] = stage
            else:
                stage = "hard_repair"
        else:
            stage = "hard_repair"
        repair_trace["pending_validation_stage"] = stage
        repair_trace["awaiting_repair_parse"] = True
        return original_repair_messages(
            messages, raw_output, violations, candidates, strategy_mode=strategy_mode
        )

    def tracking_repair_improves(
        previous, candidate, context, issues, *, candidate_issues=None
    ):
        if active_diagnostics is not None:
            categories = generation._contact_style_issue_categories(issues)
            if categories & CONTACT_STYLE_DIAGNOSTIC_CATEGORIES:
                active_diagnostics["repair_reached_quality_gate"] = True
        improved = original_repair_improves(
            previous,
            candidate,
            context,
            issues,
            candidate_issues=candidate_issues,
        )
        categories = generation._contact_style_issue_categories(issues)
        if active_diagnostics is not None and categories & CONTACT_STYLE_DIAGNOSTIC_CATEGORIES:
            if improved:
                active_diagnostics["repair_accepted"] += 1
            else:
                active_diagnostics["repair_rejected"] += 1
                active_diagnostics["rejection_reasons"].add("quality_gate_rejected")
                stage = repair_trace.get("pending_quality_gate_stage") or "style_repair"
                category = _quality_gate_rejection_category(
                    previous, candidate, context, issues, candidate_issues
                )
                _record_quality_gate_rejection(active_diagnostics, stage, category)
        repair_trace["pending_quality_gate_stage"] = None
        return improved

    generation._contact_style_soft_repair_issues = tracking_soft_issues
    generation._build_repair_messages = tracking_repair_messages
    generation._contact_style_repair_improves = tracking_repair_improves
    generation._parse_replies_strict = tracking_parse_replies
    generation.validate_candidate_replies = tracking_validate_candidates
    try:
        contact_ids = {}
        for name, pairs in contact_fixtures.items():
            cid = client.post("/api/contacts", json={"name": f"{name}さん", "profile": ""}).json()["id"]
            for contact_message, self_message in pairs:
                client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": contact_message})
                client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": self_message})
            contact_ids[name] = cid

        for name in contact_fixtures:
            _reset_contact_repair_trace(repair_trace)
            repair_trace["pending_quality_gate_stage"] = None
            repair_trace["style_repair_calls"] = 0
            active_diagnostics = _new_contact_style_diagnostics()
            cid = contact_ids[name]
            client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": probe})
            successful_route = None
            successful_before = len(successful_attempts)
            try:
                r = client.post("/api/generate", json={"contact_id": cid, "condition": "", "candidates": 3})
                route_data = r.json() if r.status_code == 200 else None
                if route_data is not None:
                    _record_returned_candidates(
                        active_diagnostics,
                        route_data.get("replies", []),
                        repair_trace["validation_candidates"],
                    )
            finally:
                # A response may exit through HTTP errors, parse failures, or repair fallback.
                # No pending repair state may leak into the next anonymous contact.
                _reset_contact_repair_trace(repair_trace)
            if r.status_code != 200:
                out[name] = {
                    "error": f"HTTP {r.status_code}",
                    "error_code": extract_api_error_code(r),
                    "contact_style_diagnostics": _finalize_contact_style_diagnostics(active_diagnostics),
                }
                break
            if len(successful_attempts) > successful_before:
                successful_key, successful_model = successful_attempts[-1]
                successful_route = successful_gemini_benchmark_route(
                    ai_config,
                    api_key=successful_key,
                    model=successful_model,
                )
                record_gemini_benchmark_success(
                    ai_config,
                    api_key=successful_key,
                    model=successful_model,
                    route_state_path=route_state_path,
                )
            data = route_data
            history_ids = data.get("history_ids", [])
            conn = database.get_conn()
            try:
                models = [row["model"] for row in conn.execute(
                    f"SELECT DISTINCT model FROM generation_history WHERE id IN ({','.join('?' for _ in history_ids)})",
                    history_ids,
                ).fetchall()] if history_ids else []
            finally:
                conn.close()
            profile = style.compute_hierarchical_profile(cid)
            learned = profile["active_profile"]
            out[name] = {
                "replies": data["replies"],
                "models_used": models,
                "successful_route": successful_route,
                "style_profile": {
                    "tier": profile["hierarchy_tier"],
                    "gold_samples": profile["same_contact_gold_samples"],
                    "adaptation_weight": profile["contact_adaptation_weight"],
                    "length_median": learned.char_median,
                    "keigo_ratio": learned.keigo_ratio,
                    "hybrid_ratio": learned.hybrid_ratio,
                    "tame_ratio": learned.tame_ratio,
                    "laugh_ratio": learned.laugh_ratio,
                },
                "contact_style_diagnostics": _finalize_contact_style_diagnostics(active_diagnostics),
            }
            time.sleep(max(0.0, delay_seconds))
    finally:
        generation._contact_style_soft_repair_issues = original_soft_issues
        generation._build_repair_messages = original_repair_messages
        generation._contact_style_repair_improves = original_repair_improves
        generation._parse_replies_strict = original_parse_replies
        generation.validate_candidate_replies = original_validate_candidates
        generation.get_ai_config = original_get_ai_config
        factory.get_provider = original_factory_get_provider
        generation.factory.get_provider = original_generation_factory_get_provider
    return out


def style_sig(text):
    tame = 1 if re.search(r"(笑|w|だね|だよ|よね|じゃん)", text) else 0
    keigo = 1 if re.search(r"(です|ます|でした|ました)", text) else 0
    return {"len": len(text), "tame": tame, "keigo": keigo,
            "laugh": 1 if ("笑" in text or "w" in text) else 0}


def build_contact_report_entry(result: dict, gold_pairs: list[tuple[str, str]]) -> dict:
    """Build a credential-free entry with enough evidence to review its route and style."""
    if "error" in result:
        return result
    replies = result.get("replies", [])
    signatures = [style_sig(reply) for reply in replies]
    own_gold = [style_sig(gold) for _, gold in gold_pairs]
    diagnostics = dict(result.get(
        "contact_style_diagnostics", _finalize_contact_style_diagnostics(_new_contact_style_diagnostics())
    ))
    diagnostics["observed_candidate_aggregates"] = _safe_candidate_aggregates(replies)
    entry = {
        "replies": replies,
        "sigs": signatures,
        "style_profile": result["style_profile"],
        "successful_route": result.get("successful_route"),
        "models_used": result.get("models_used", []),
        "contact_style_diagnostics": diagnostics,
    }
    if own_gold and signatures:
        entry["own_gold"] = {
            "laugh": round(sum(sig["laugh"] for sig in own_gold) / len(own_gold), 2),
            "len": round(sum(sig["len"] for sig in own_gold) / len(own_gold), 1),
        }
        entry["reply_avg"] = {
            "laugh": round(sum(sig["laugh"] for sig in signatures) / len(signatures), 2),
            "len": round(sum(sig["len"] for sig in signatures) / len(signatures), 1),
        }
    return entry


def _run_benchmark_with_database(args, contact_fixtures, fixture_source, key, secondary_key, db_path):
    database.init_db()
    client = TestClient(app)
    out = seed_and_generate(
        client,
        key,
        args.model,
        args.delay_seconds,
        args.probe,
        secondary_key,
        args.quota_route_state,
        args.active_account,
        contact_fixtures,
    )
    report = {}
    for name, res in out.items():
        report[name] = build_contact_report_entry(res, contact_fixtures[name])
    report["probe_char_count"] = len(args.probe)
    case_results = [report[name] for name in contact_fixtures if name in report]
    run_state = benchmark_run_state(case_results, len(contact_fixtures))
    report["run_status"] = {
        **run_state,
        "completed_contacts": len(case_results),
    }
    report["quality_review"] = contact_quality_status(
        generation_complete=run_state["complete"],
        expected_replies=len(contact_fixtures) * 3,
    )
    report["fixture_source"] = fixture_source
    report["gold_pairs_per_contact"] = {
        name: len(pairs) for name, pairs in contact_fixtures.items()
    }
    Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Wrote {args.out}")
    print("Contact adaptation quality is not auto-scored; inspect every reply in the artifact.")
    print(json.dumps({n: {"own": r.get("own_gold"), "rep": r.get("reply_avg"),
                          "replies": r.get("replies")} for n, r in report.items()
                      if n in contact_fixtures and isinstance(r, dict)},
                     ensure_ascii=False, indent=1)[:2000])
    return 0 if run_state["complete"] else 2


def main():
    global app
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="gemini-3.5-flash-lite")
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parents[1] / ".env"),
                    help="API key file (value is never printed)")
    ap.add_argument("--secondary-env-file", default="",
                    help="Optional second-account key file (value is never printed)")
    add_active_account_argument(ap)
    ap.add_argument("--quota-route-state", type=Path,
                    help="Optional run-local state shared across benchmark stages")
    ap.add_argument("--db", help="Optional new/empty database path; existing files are never removed")
    ap.add_argument("--delay-seconds", type=float, default=6.0)
    ap.add_argument("--probe", default=PROBE, help="Shared incoming message used for A/B/C")
    ap.add_argument(
        "--gold-fixture",
        type=Path,
        help="Optional local JSON fixture with anonymous A/B/C gold pairs; input text is not copied to the artifact",
    )
    args = ap.parse_args()
    contact_fixtures = CONTACTS
    fixture_source = "synthetic_control"
    if args.gold_fixture:
        try:
            contact_fixtures = load_contact_fixture(args.gold_fixture)
        except ValueError as exc:
            print(f"Invalid --gold-fixture: {exc}")
            return 2
        fixture_source = "user_gold_fixture"
    if not args.probe.strip():
        print("Invalid --probe: message must not be empty")
        return 2
    if len(args.probe) > MAX_GOLD_MESSAGE_CHARS:
        print("Invalid --probe: message exceeds 2000 characters")
        return 2
    key = read_gemini_api_key(Path(args.env_file))
    if not key:
        print("GEMINI_API_KEY missing")
        return 1
    secondary_key = (
        read_gemini_api_key(Path(args.secondary_env_file))
        if args.secondary_env_file
        else ""
    )
    db_path = Path(args.db) if args.db else Path(tempfile.mkdtemp(prefix="contactbench_")) / "contactbench.db"
    if db_path.exists() and db_path.stat().st_size:
        print(f"Refusing to overwrite non-empty benchmark database: {db_path}")
        return 2
    db_path.parent.mkdir(parents=True, exist_ok=True)
    original_db_path = config.DB_PATH
    config.DB_PATH = db_path
    isolate_benchmark_logging(config, db_path.parent / "benchmark-logs")
    from app.main import app as test_app
    app = test_app

    try:
        return _run_benchmark_with_database(
            args, contact_fixtures, fixture_source, key, secondary_key, db_path
        )
    finally:
        config.DB_PATH = original_db_path


if __name__ == "__main__":
    raise SystemExit(main())
