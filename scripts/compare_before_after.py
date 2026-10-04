"""Step 7-8 代表ケースの Before/After 比較を実行する。

使い方（決定論）:
    python scripts/compare_before_after.py --out PATH [--cases PATH]

使い方（実LLM）:
    python scripts/compare_before_after.py --live --provider gemini --model gemini-3.5-flash-lite --out PATH

各ケースについて Intent 分類・Prompt マーカー・バリデーション・Naturalness
ランキングを記録し、JSON に保存する。コード変更前後で実行して差分を見る。
live モードでは実プロバイダを呼び、5種の issue 指標も記録する。
APIキーは表示しない。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "tests"))

from app.ai import prompt  # noqa: E402
from app.ai.naturalness import (  # noqa: E402
    _extract_keywords,
    count_meaningful_questions,
    detect_echo,
    evaluate_candidate_naturalness,
)
from app import reply_policy  # noqa: E402
from app.routers.generation import _parse_replies_strict, validate_candidate_replies  # noqa: E402
from step7_representative_cases import load_step7_representative_cases  # noqa: E402

# Step 10: AI-like 失敗パターン分類（ベンチマーク測定用。製品の禁止ルールではない）
FORMULAIC_EMPATHY = ("そうなんですね", "わかります", "確かに", "なるほど")
EXCESS_POLITE = re.compile(r"(?:です|ます)")
OVER_EMOTION = re.compile(r"超|めっちゃ|最高")
UNNEEDED_SUMMARY = ("つまり", "要するに", "まとめると", "ということは")
UNNEEDED_CHEER = ("頑張って", "応援して", "無理しないでください")


def _key_score(nat_result: dict, key: str) -> float:
    """Naturalness 結果から項目スコアを取り出す（減点なしは 1.0）。"""
    for p in nat_result.get("penalties", []):
        if p.get("key") == key:
            return float(p.get("score", 1.0))
    return 1.0


def four_axis_scores(nat_result: dict) -> dict:
    """Step 10 §2: 自然さの4軸スコア（決定論的マッピング）。"""
    return {
        "context_fit": _key_score(nat_result, "relevance"),
        "human_chat_fit": min(
            _key_score(nat_result, "question"),
            _key_score(nat_result, "echo"),
            _key_score(nat_result, "length"),
            _key_score(nat_result, "overreact"),
        ),
        "personal_style_fit": None,  # synthetic live には Gold がないため測定不可
        "conversation_fit": min(
            _key_score(nat_result, "repetition"),
            _key_score(nat_result, "length"),
        ),
    }


def classify_ai_like(candidate: str, contact: str, counterpart_intent: str = "report") -> list[str]:
    """Step 10 §3: AIっぽさの失敗パターン分類（測定用）。"""
    t = (candidate or "").strip()
    found: list[str] = []
    if t.startswith(FORMULAIC_EMPATHY) and ("！" in t[:12] or "!" in t[:12]):
        found.append("formulaic_empathy")
    if len(t) > 80 or any(m in t for m in EXPLAIN_MARKERS):
        found.append("over_explanation")
    if count_meaningful_questions(t)["informative"] >= 2:
        found.append("too_many_questions")
    novel = _extract_keywords(t) - _extract_keywords(contact)
    if len(novel) >= 3:
        found.append("topic_drift")
    if len(EXCESS_POLITE.findall(t)) >= 4 and len(contact) <= 30:
        found.append("over_polite")
    if len(re.findall(r"[！!]", t)) >= 3 or len(OVER_EMOTION.findall(t)) >= 2:
        found.append("over_emotion")
    echo_score, _ = detect_echo(t, contact)
    if echo_score < 1.0:
        found.append("paraphrase")
    if any(m in t for m in UNNEEDED_SUMMARY):
        found.append("unneeded_summary")
    if any(m in t for m in UNNEEDED_CHEER):
        found.append("unneeded_cheer")
    # 不自然な自己開示・会話延命は signals 経由の簡易判定
    if reply_policy.question_necessity(contact, counterpart_intent) == "unnecessary":
        if count_meaningful_questions(t)["informative"] >= 1:
            found.append("forced_continuation")
    return found

# Step 8: issue 検出用の定型マーカー（比較測定用。製品コードの禁止ルールではない）
AI_LIKE_MARKERS = [
    "そうなんですね", "大変でしたね", "なるほど", "確かに", "ちなみに",
    "ということですね", "ということは", "素敵ですね", "最高ですね",
    "要するに", "つまり", "なぜなら",
]
EXPLAIN_MARKERS = [
    "ということは", "ということですね", "つまり", "要するに",
    "なぜなら", "どういうことかというと",
]


def build_case_prompt(case: dict) -> tuple[str, str, dict]:
    """ケースから (system_prompt, chat_history_text, ledger) を組み立てる。"""
    contact = case["contact"]
    ledger = prompt.build_conversation_state_ledger(
        [{"sender": "contact", "content": contact}], ""
    )
    tier = prompt.classify_message_length(contact)
    chat_history_text = f"相手: {contact}"
    sysp = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        condition="",
        chat_history_text=chat_history_text,
        conversation_ledger=ledger,
        counterpart_length_tier=tier,
        counterpart_length_chars=len(contact),
    )
    return sysp, chat_history_text, ledger


def evaluate_case(case: dict) -> dict:
    contact = case["contact"]
    intent_pred = prompt.classify_counterpart_intent(contact)
    sysp, chat_history_text, ledger = build_case_prompt(case)
    markers = {
        "has_intent_block": "【COUNTERPART INTENT】" in sysp,
        "no_hard_question": ("質問必須" not in sysp and "必ず質問" not in sysp),
        "no_3step": ("3段構成" not in sysp and "反応→自己開示→質問" not in sysp),
    }
    if len(contact) <= 20:
        markers["short_guidance"] = "短いリアクション" in sysp

    cands = case["candidates"]
    violations = validate_candidate_replies(cands, 3)
    scored = []
    for c in cands:
        res = evaluate_candidate_naturalness(c, contact, ledger, [])
        scored.append(res["score"])
    order = sorted(range(len(cands)), key=lambda i: scored[i], reverse=True)

    return {
        "id": case["id"],
        "intent_label": case["intent"],
        "intent_pred": intent_pred,
        "intent_match": intent_pred == case["intent"],
        "markers": markers,
        "markers_ok": all(markers.values()),
        "validation_pass": violations == [],
        "violations": violations,
        "naturalness_scores": scored,
        "rank_order": order,
        "expect_first": case["expect_first"],
        "expect_hit": order[0] == case["expect_first"],
    }


def detect_issues(candidate: str, contact: str, history_text: str = "") -> dict:
    """Step 8: 5種の issue 指標を決定論的に測定する（比較用。製品の禁止ルールではない）。"""
    cand_kw = _extract_keywords(candidate)
    novel = cand_kw - _extract_keywords(contact) - _extract_keywords(history_text)
    q = count_meaningful_questions(candidate)
    echo_score, _ = detect_echo(candidate, contact)
    return {
        # 相手文・履歴にない具体語の数（推測混入の代理指標）
        "unsupported_inference": len(novel),
        "echo": echo_score < 1.0,
        "too_many_questions": q["informative"] >= 2,
        "over_explanation": len(candidate) > 80 or any(m in candidate for m in EXPLAIN_MARKERS),
        "ai_like": sum(candidate.count(m) for m in AI_LIKE_MARKERS),
    }


def summarize_issues(all_issues: list[dict]) -> dict:
    """issue 指標の集計（候補単位）。"""
    n = len(all_issues) or 1
    return {
        "candidates": len(all_issues),
        "novel_keyword_rate": round(sum(1 for i in all_issues if i["unsupported_inference"] > 0) / n, 3),
        "avg_novel_keywords": round(sum(i["unsupported_inference"] for i in all_issues) / n, 3),
        "echo_rate": round(sum(1 for i in all_issues if i["echo"]) / n, 3),
        "too_many_questions_rate": round(sum(1 for i in all_issues if i["too_many_questions"]) / n, 3),
        "over_explanation_rate": round(sum(1 for i in all_issues if i["over_explanation"]) / n, 3),
        "ai_like_rate": round(sum(1 for i in all_issues if i["ai_like"] > 0) / n, 3),
    }


def _read_env_key(name: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith(name + "=") or s.startswith(name + " ="):
            return s.partition("=")[2].strip()
    return ""


def run_live(cases: list[dict], provider_name: str, model: str) -> list[dict]:
    """実プロバイダで各ケースを生成し、検証・評価・issue 指標を記録する。"""
    from app.ai.factory import get_provider

    api_key = _read_env_key(f"{provider_name.upper()}_API_KEY")
    if not api_key:
        raise RuntimeError(f"{provider_name} のAPIキーが.envにありません")
    provider = get_provider(provider_name, api_key)
    results = []
    for case in cases:
        contact = case["contact"]
        entry: dict = {"id": case["id"], "contact": contact}
        try:
            sysp, chat_history_text, ledger = build_case_prompt(case)
            msgs = prompt.build_initial_generation_messages(
                system_prompt=sysp,
                chat_history_text=chat_history_text,
                candidates=3,
                mode="normal",
            )
            raw = provider.generate(
                model=model, messages=msgs, temperature=0.8, max_tokens=1024, json_mode=True
            )
            parsed = _parse_replies_strict(raw, 3)
            entry["candidates"] = parsed
            entry["parse_ok"] = len(parsed) == 3
            entry["violations"] = validate_candidate_replies(parsed, 3) if parsed else ["parse_failed"]
            scored = []
            issues = []
            axes_list = []
            ai_like_list = []
            for c in parsed:
                res = evaluate_candidate_naturalness(c, contact, ledger, [])
                scored.append(res["score"])
                issues.append(detect_issues(c, contact))
                axes_list.append(four_axis_scores(res))
                ai_like_list.append(classify_ai_like(
                    c, contact,
                    (ledger.get("counterpart_intent") or "report"),
                ))
            entry["naturalness_scores"] = scored
            entry["issues"] = issues
            entry["four_axis"] = axes_list
            entry["ai_like_patterns"] = ai_like_list
        except Exception as exc:  # noqa: BLE001
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        results.append(entry)
        time.sleep(2)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Step 7-8 代表ケースの比較を実行")
    parser.add_argument("--out", default=None, help="結果JSONの出力先")
    parser.add_argument("--cases", default=None, help="ケースJSONのパス")
    parser.add_argument("--live", action="store_true", help="実プロバイダで生成する")
    parser.add_argument("--provider", default="gemini", help="live用プロバイダ名")
    parser.add_argument("--model", default="gemini-3.5-flash-lite", help="live用モデル名")
    args = parser.parse_args()

    if args.cases:
        cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    else:
        cases = load_step7_representative_cases()

    if args.live:
        results = run_live(cases, args.provider, args.model)
        all_issues = [iss for r in results for iss in r.get("issues", [])]
        all_patterns = [p for r in results for c in r.get("ai_like_patterns", []) for p in c]
        pattern_counts: dict[str, int] = {}
        for p in all_patterns:
            pattern_counts[p] = pattern_counts.get(p, 0) + 1
        axis_keys = ("context_fit", "human_chat_fit", "conversation_fit")
        axis_avg = {}
        for key in axis_keys:
            vals = [c[key] for r in results for c in r.get("four_axis", []) if c.get(key) is not None]
            axis_avg[key] = round(sum(vals) / len(vals), 3) if vals else None
        summary = {
            "total": len(results),
            "errors": sum(1 for r in results if "error" in r),
            "parse_ok_rate": round(
                sum(1 for r in results if r.get("parse_ok")) / (len(results) or 1), 3
            ),
            "issues": summarize_issues(all_issues),
            "four_axis_avg": axis_avg,
            "ai_like_patterns": pattern_counts,
        }
        payload = {
            "summary": summary, "provider": args.provider, "model": args.model, "cases": results,
        }
    else:
        results = [evaluate_case(c) for c in cases]
        all_fixed_issues = [
            detect_issues(c, case["contact"])
            for case in cases
            for c in case["candidates"]
        ]
        summary = {
            "total": len(results),
            "intent_accuracy": round(sum(1 for r in results if r["intent_match"]) / len(results), 3),
            "markers_ok_rate": round(sum(1 for r in results if r["markers_ok"]) / len(results), 3),
            "validation_pass_rate": round(sum(1 for r in results if r["validation_pass"]) / len(results), 3),
            "expect_hit_rate": round(sum(1 for r in results if r["expect_hit"]) / len(results), 3),
            "issues": summarize_issues(all_fixed_issues),
        }
        payload = {"summary": summary, "cases": results}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
