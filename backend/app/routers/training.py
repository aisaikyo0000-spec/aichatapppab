"""Phase 3: AI練習・返信評価・学習データ（フィードバック）の管理API。

学習データは training_examples テーブルに構造化して保存し、
将来のFew-shot / RAG / Fine-tuningに利用できるようにする。
"""
from __future__ import annotations

import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException

from .. import database
from ..ai import factory, prompt
from ..ai.base import AIError
from ..ai.config import get_ai_config
from ..schemas import (
    TrainingEvaluateRequest,
    TrainingExampleCreate,
    TrainingMessageCreate,
    TrainingReplyRequest,
    TrainingRevisionRequest,
    TrainingSessionCreate,
    TrainingSessionUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/training", tags=["training"])

EVALUATION_LABELS = {
    "naturalness": "自然さ",
    "continuity": "会話継続性",
    "distance": "距離感",
    "fit": "相手発言への適合",
    "overall": "総合評価",
}


def _session_messages(row) -> list[dict]:
    try:
        data = json.loads(row["messages"] or "[]")
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def _save_session_messages(conn, session_id: int, messages: list[dict]) -> None:
    conn.execute(
        "UPDATE training_sessions SET messages = ?, updated_at = ? WHERE id = ?",
        (json.dumps(messages, ensure_ascii=False), database.now_iso(), session_id),
    )


def _get_session_or_404(conn, session_id: int):
    row = conn.execute(
        "SELECT * FROM training_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="練習セッションが見つかりません")
    return row


def _profile_fields(row) -> dict:
    """persona_profileカラムのJSONを項目別dictに分解する。"""
    try:
        data = json.loads(row["persona_profile"] or "{}")
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {}


def _compose_profile_text(data: dict) -> str:
    """項目別プロフィールをAI向けの1文のテキストにまとめる。"""
    parts = []
    gender = (data.get("persona_gender") or "").strip()
    age = (data.get("persona_age") or "").strip()
    if gender or age:
        parts.append("・".join(x for x in (gender, age) if x))
    for label, key in (("趣味", "persona_hobbies"), ("性格", "persona_personality"), ("話し方", "persona_style")):
        v = (data.get(key) or "").strip()
        if v:
            parts.append(f"{label}: {v}")
    free = (data.get("persona_profile") or "").strip()
    if free:
        parts.append(free)
    return "、".join(parts)


def _resolve_persona(conn, row) -> tuple[str, str]:
    """練習相手の名前とプロフィールを解決する。"""
    name = row["persona_name"] or "練習相手"
    profile = ""
    if row["contact_id"]:
        contact = conn.execute(
            "SELECT name, profile FROM contacts WHERE id = ?", (row["contact_id"],)
        ).fetchone()
        if contact:
            name = contact["name"]
            profile = contact["profile"] or ""
    else:
        profile = _compose_profile_text(_profile_fields(row))
    return name, profile


_RETRY_DELAYS: dict[str, tuple[int, ...]] = {
    "empty_response": (2, 4),  # 一時的な空レスポンス: 2回まで再試行
    "rate_limit": (8, 16),  # レート制限: トークンバケットの回復を待って2回まで再試行
}


def _call_provider(
    provider, cfg: dict, messages: list[dict], json_mode: bool = False, max_tokens: int | None = None
) -> str:
    def _once() -> str:
        return provider.generate(
            model=cfg["model"],
            messages=messages,
            temperature=cfg["temperature"],
            max_tokens=max_tokens or cfg["max_tokens"],
            json_mode=json_mode,
        )

    try:
        return _once()
    except AIError as exc:
        delays = _RETRY_DELAYS.get(exc.code)
        if delays is None:
            logger.warning("training AI call failed: code=%s", exc.code)
            raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message})
        last_exc = exc
        for i, delay in enumerate(delays, start=1):
            logger.warning("training AI call %s, retrying (%d/%d)", last_exc.code, i, len(delays))
            time.sleep(delay)
            try:
                return _once()
            except AIError as exc2:
                last_exc = exc2
        # 主プロバイダの再試行が尽きたら、フォールバックプロバイダを試す（レート制限・空レスポンス時のみ）
        if last_exc.code in ("rate_limit", "empty_response"):
            fb = factory.get_fallback(cfg)
            if fb is not None:
                fb_provider, fb_cfg = fb
                logger.warning(
                    "primary %s exhausted, trying fallback %s", last_exc.code, fb_cfg["provider"]
                )
                try:
                    return fb_provider.generate(
                        model=fb_cfg["model"],
                        messages=messages,
                        temperature=fb_cfg["temperature"],
                        max_tokens=max_tokens or fb_cfg["max_tokens"],
                        json_mode=json_mode,
                    )
                except AIError as fb_exc:
                    logger.warning("fallback failed: code=%s", fb_exc.code)
                    raise HTTPException(
                        status_code=502, detail={"code": fb_exc.code, "message": fb_exc.message}
                    )
        logger.warning("training AI call failed: code=%s", last_exc.code)
        raise HTTPException(status_code=502, detail={"code": last_exc.code, "message": last_exc.message})


# ---------------------------------------------------------------- sessions

@router.post("/sessions", status_code=201)
def create_session(body: TrainingSessionCreate):
    name = body.persona_name or "練習相手"
    now = database.now_iso()
    conn = database.get_conn()
    try:
        if body.contact_id is not None:
            row = conn.execute(
                "SELECT id, name FROM contacts WHERE id = ?", (body.contact_id,)
            ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="相手が見つかりません")
            name = row["name"]
        profile_data = {
            "persona_gender": body.persona_gender,
            "persona_age": body.persona_age,
            "persona_hobbies": body.persona_hobbies,
            "persona_personality": body.persona_personality,
            "persona_style": body.persona_style,
            "persona_profile": body.persona_profile,
        }
        cur = conn.execute(
            "INSERT INTO training_sessions"
            " (contact_id, messages, persona_name, persona_profile, created_at, updated_at)"
            " VALUES (?, '[]', ?, ?, ?, ?)",
            (body.contact_id, name, json.dumps(profile_data, ensure_ascii=False), now, now),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "contact_id": body.contact_id,
            "persona_name": name,
            "persona_profile": body.persona_profile or "",
            "persona_profile_display": _compose_profile_text(profile_data),
            "persona_gender": body.persona_gender or "",
            "persona_age": body.persona_age or "",
            "persona_hobbies": body.persona_hobbies or "",
            "persona_personality": body.persona_personality or "",
            "persona_style": body.persona_style or "",
            "messages": [],
            "created_at": now,
            "updated_at": now,
        }
    finally:
        conn.close()


@router.get("/sessions")
def list_sessions():
    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM training_sessions ORDER BY updated_at DESC"
        ).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        msgs = _session_messages(r)
        result.append(
            {
                "id": r["id"],
                "contact_id": r["contact_id"],
                "persona_name": r["persona_name"] or "練習相手",
                "last_message": msgs[-1]["content"] if msgs else "",
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
        )
    return result


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM training_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="練習セッションが見つかりません")
        conn.execute("DELETE FROM training_sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()


@router.patch("/sessions/{session_id}")
def update_session(session_id: int, body: TrainingSessionUpdate):
    """ペルソナ名・プロフィールの部分更新。"""
    conn = database.get_conn()
    try:
        row = _get_session_or_404(conn, session_id)
        if row["contact_id"]:
            raise HTTPException(status_code=400, detail="実在の相手から作成した練習はプロフィールを編集できません")

        data = body.model_dump(exclude_none=True)
        if not data:
            raise HTTPException(status_code=422, detail="変更する項目がありません")

        profile_data = _profile_fields(row)
        for key in (
            "persona_gender",
            "persona_age",
            "persona_hobbies",
            "persona_personality",
            "persona_style",
            "persona_profile",
        ):
            if key in data:
                profile_data[key] = data[key]

        new_name = row["persona_name"]
        if "persona_name" in data:
            new_name = data["persona_name"] or "練習相手"

        now = database.now_iso()
        conn.execute(
            "UPDATE training_sessions SET persona_name = ?, persona_profile = ?, updated_at = ? WHERE id = ?",
            (new_name, json.dumps(profile_data, ensure_ascii=False), now, session_id),
        )
        conn.commit()
        row = _get_session_or_404(conn, session_id)
        name, profile = _resolve_persona(conn, row)
        fields = _profile_fields(row)
        return {
            "id": row["id"],
            "contact_id": row["contact_id"],
            "persona_name": name,
            "persona_profile": fields.get("persona_profile") or "",
            "persona_profile_display": profile,
            "persona_gender": fields.get("persona_gender") or "",
            "persona_age": fields.get("persona_age") or "",
            "persona_hobbies": fields.get("persona_hobbies") or "",
            "persona_personality": fields.get("persona_personality") or "",
            "persona_style": fields.get("persona_style") or "",
            "messages": _session_messages(row),
            "created_at": row["created_at"],
            "updated_at": now,
        }
    finally:
        conn.close()


@router.get("/sessions/{session_id}")
def get_session(session_id: int):
    conn = database.get_conn()
    try:
        row = _get_session_or_404(conn, session_id)
        name, profile = _resolve_persona(conn, row)
        msgs = _session_messages(row)
        fields = _profile_fields(row)
        return {
            "id": row["id"],
            "contact_id": row["contact_id"],
            "persona_name": name,
            "persona_profile": fields.get("persona_profile") or "",
            "persona_profile_display": profile,
            "persona_gender": fields.get("persona_gender") or "",
            "persona_age": fields.get("persona_age") or "",
            "persona_hobbies": fields.get("persona_hobbies") or "",
            "persona_personality": fields.get("persona_personality") or "",
            "persona_style": fields.get("persona_style") or "",
            "messages": msgs,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


@router.post("/sessions/{session_id}/messages", status_code=201)
def add_session_message(session_id: int, body: TrainingMessageCreate):
    content = body.content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="メッセージを入力してください")
    conn = database.get_conn()
    try:
        row = _get_session_or_404(conn, session_id)
        msgs = _session_messages(row)
        msgs.append({"sender": "self", "content": content, "created_at": database.now_iso()})
        _save_session_messages(conn, session_id, msgs)
        conn.commit()
        return {"messages": msgs}
    finally:
        conn.close()


@router.post("/sessions/{session_id}/my-reply")
def session_my_reply(session_id: int, body: TrainingReplyRequest):
    """自分の返信を【SELF】+知識ファイル（RULES/REFERENCES/LEARNING）に基づいて作成する。"""
    cfg = get_ai_config()
    provider = factory.get_provider(cfg["provider"], cfg["api_key"])

    conn = database.get_conn()
    try:
        row = _get_session_or_404(conn, session_id)
        name, profile = _resolve_persona(conn, row)
        msgs = _session_messages(row)
    finally:
        conn.close()

    rules = prompt.load_knowledge_texts("rules")
    references = prompt.load_knowledge_texts("references")
    learning_materials = prompt.cap_learning(prompt.load_knowledge_pairs("training"))
    self_profile = database.get_user_profile()
    user_knowledge = database.get_user_knowledge_text()
    chat_text = prompt.format_chat_history(msgs, 25)
    system = prompt.build_system_prompt(
        rules=rules,
        references=references,
        learning_materials=learning_materials,
        condition=body.condition,
        contact={"name": name, "profile": profile, "images": []},
        chat_history_text=chat_text,
        training_examples=[],
        self_profile=self_profile,
        user_knowledge=user_knowledge,
        my_info=self_profile.get("my_info", ""),
    )

    system += (
        "\n\n【練習用の追加ルール】\n"
        "- これはマッチングアプリの会話練習です。会話を磨くのが目的\n"
        "- 会おうと誘わない。日程や都合も聞かない。常に自然な雑談を続ける\n"
        "- 1文ごとに改行して読みやすくする\n"
        "- 同じ話題が3往復以上続いたら、次の返信では必ず、相手の発言から自然に繋げて別の関連する話題（できれば相手の趣味に関わる話題）へ1つずらして会話に変化をつける。同じ話題を延々と続けない\n"
        "- 質問する時は必ず自分の情報を1つ添え、相手の理由・気持ち・体験を掘り下げる。「どのトッピングが好き？」のような瑣末な詳細質問はしない\n"
        "- 毎通質問で終わらない。3通に1通は質問なしの返信（感想・共感・自分の話だけで終える）か推測形（「〜っぽい」「〜そう」）を混ぜる。全部「共感→自分の話→質問」の同じ型にしない\n"
        "- 食べ物の話題が続いたら、趣味・経験・価値観・行きたい場所など食以外の領域へ話題をずらす\n"
        "- 相手が「行った場所・見た映画・ドラマ・食べたもの」の体験談を話したら、自分も体験済みの前提にしない。体験系は共感3割に抑え、残り7割は未体験を前提にする。その7割のうち半分は「そんなのあるんですね！」「知らなかったです」と体験自体を知らない反応で驚きと興味を示し、残り半分は「行ったことなくて気になってるんですけど、どんな雰囲気でしたか？」のように知ってはいるが未体験の深掘りにする\n"
    )
    messages = [{"role": "system", "content": system}]
    for m in msgs:
        role = "user" if m["sender"] == "self" else "assistant"
        messages.append({"role": role, "content": m["content"]})
    if not msgs:
        messages.append(
            {"role": "user", "content": "（相手からのメッセージがまだありません。最初のあいさつ文を作成してください）"}
        )
    else:
        # 最後をユーザー指示で終える（assistantで終わると空レスポンスになる場合がある）
        messages.append(
            {"role": "user", "content": "この会話に対するあなた（自分）の返信文章を1つ作成してください。"}
        )

    raw = _call_provider(provider, cfg, messages)
    q_match = re.search(r'\[AI_QUESTION\](.+?)\[/AI_QUESTION\]', raw, re.DOTALL)
    if q_match:
        question_text = q_match.group(1).strip()
        if question_text:
            return {"reply": "", "question": question_text}
    return {"reply": prompt.strip_brackets(raw.strip())}


@router.post("/sessions/{session_id}/revise-my-reply")
def revise_my_reply(session_id: int, body: TrainingRevisionRequest):
    """生成した自分の返信に修正指示を出して再生成する。履歴も記録する。"""
    cfg = get_ai_config()
    provider = factory.get_provider(cfg["provider"], cfg["api_key"])

    conn = database.get_conn()
    try:
        row = _get_session_or_404(conn, session_id)
        name, profile = _resolve_persona(conn, row)
        msgs = _session_messages(row)
    finally:
        conn.close()

    rules = prompt.load_knowledge_texts("rules")
    references = prompt.load_knowledge_texts("references")
    learning_materials = prompt.cap_learning(prompt.load_knowledge_pairs("training"))
    self_profile = database.get_user_profile()
    user_knowledge = database.get_user_knowledge_text()
    chat_text = prompt.format_chat_history(msgs, 25)
    system = prompt.build_system_prompt(
        rules=rules,
        references=references,
        learning_materials=learning_materials,
        condition=body.condition,
        contact={"name": name, "profile": profile, "images": []},
        chat_history_text=chat_text,
        training_examples=[],
        self_profile=self_profile,
        user_knowledge=user_knowledge,
        my_info=self_profile.get("my_info", ""),
    )
    msgs_for_ai = prompt.build_revision_messages(
        system_prompt=system,
        chat_history_text=chat_text,
        condition=body.condition,
        original_generated=body.original_generated,
        revision_instruction=body.revision_instruction,
    )

    raw = _call_provider(provider, cfg, msgs_for_ai)
    revised = prompt.strip_brackets(raw.strip())

    conn = database.get_conn()
    try:
        conn.execute(
            "INSERT INTO training_revisions"
            " (session_id, original_generated, revision_instruction, revised_text, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (session_id, body.original_generated, body.revision_instruction, revised, database.now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
    return {"reply": revised}


@router.get("/revisions")
def list_revisions(limit: int = 100):
    """修正指示の履歴を返す（学習データの確認・改善用）。"""
    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM training_revisions ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


@router.post("/sessions/{session_id}/ai-reply")
def session_ai_reply(session_id: int, body: TrainingReplyRequest):
    cfg = get_ai_config()
    provider = factory.get_provider(cfg["provider"], cfg["api_key"])

    conn = database.get_conn()
    try:
        row = _get_session_or_404(conn, session_id)
        name, profile = _resolve_persona(conn, row)
        msgs = _session_messages(row)
    finally:
        conn.close()

    rules = prompt.load_knowledge_texts("rules")
    references = prompt.load_knowledge_texts("references")
    learning_materials = prompt.cap_learning(prompt.load_knowledge_pairs("training"))
    self_profile = database.get_user_profile()
    chat_text = prompt.format_chat_history(msgs, 25)
    system = prompt.build_system_prompt(
        rules=rules,
        references=references,
        learning_materials=learning_materials,
        condition=body.condition,
        contact={"name": name, "profile": profile, "images": []},
        chat_history_text=chat_text,
        training_examples=[],
        self_profile=self_profile,
        role="partner",
        my_info=self_profile.get("my_info", ""),
    )
    system += (
        "\n\n【練習用の追加ルール】\n"
        "- これはマッチングアプリの会話練習です。会話力を磨くのが目的\n"
        "- 会おうと誘わない。日程や都合も聞かない。常に自然な雑談を続ける\n"
        "- 最初からタメ口にしない。初めは丁寧な言葉遣いから始め、やり取りを重ねるにつれて徐々に打ち解けていく\n"
        "- チャットアプリ風の短文で返す。必要以上に長くしない\n"
        "- 1文ごとに改行して読みやすくする\n"
        "- 同じ話題が3往復以上続いたら、次の返信では必ず、相手の発言から自然に繋げて別の関連する話題（できれば相手の趣味に関わる話題）へ1つずらして会話に変化をつける。同じ話題を延々と続けない\n"
        "- 質問する時は必ず自分の情報を1つ添え、相手の理由・気持ち・体験を掘り下げる。「どのトッピングが好き？」のような瑣末な詳細質問はしない\n"
        "- 毎通質問で終わらない。3通に1通は質問なしの返信（感想・共感・自分の話だけで終える）か推測形（「〜っぽい」「〜そう」）を混ぜる。全部「共感→自分の話→質問」の同じ型にしない\n"
        "- 食べ物の話題が続いたら、趣味・経験・価値観・行きたい場所など食以外の領域へ話題をずらす\n"
    )

    messages = [{"role": "system", "content": system}]
    for m in msgs:
        role = "assistant" if m["sender"] == "contact" else "user"
        messages.append({"role": role, "content": m["content"]})
    if not msgs:
        messages.append(
            {"role": "user", "content": "（会話の最初のメッセージを送ってください）"}
        )

    raw = _call_provider(provider, cfg, messages)
    reply = prompt.strip_brackets(raw.strip())
    msgs.append({"sender": "contact", "content": reply, "created_at": database.now_iso()})

    conn = database.get_conn()
    try:
        _save_session_messages(conn, session_id, msgs)
        conn.commit()
    finally:
        conn.close()
    return {"reply": reply, "messages": msgs}


# ---------------------------------------------------------------- evaluate

@router.post("/evaluate")
def evaluate_reply(body: TrainingEvaluateRequest):
    cfg = get_ai_config()
    provider = factory.get_provider(cfg["provider"], cfg["api_key"])

    conv_lines = [
        f"{'相手' if m.sender == 'contact' else '自分'}: {m.content}"
        for m in body.conversation
    ]
    conv_text = "\n".join(conv_lines) if conv_lines else "（会話なし）"

    system = (
        "あなたはマッチングアプリの返信文章を客観的に評価する添削者です。\n"
        "評価は参考情報であり、ユーザーの判断を支援する役割です。"
    )
    user = (
        "【会話】\n"
        f"{conv_text}\n\n"
        "【評価対象の返信】\n"
        f"{body.reply}\n\n"
        "以下の5項目を5段階（1〜5）で評価し、各項目にコメントを付けてください。\n"
        "コメントは1文で簡潔に（40文字程度）書いてください。\n"
        "- naturalness: 自然さ\n"
        "- continuity: 会話継続性（会話が続きそうか）\n"
        "- distance: 距離感（馴れ馴れしすぎないか）\n"
        "- fit: 相手の発言への適合\n"
        "- overall: 総合評価\n\n"
        'JSON形式で出力してください。例: {"naturalness":{"score":4,"comment":"..."},'
        ' "continuity":{"score":3,"comment":"..."},"distance":{"score":5,"comment":"..."},'
        ' "fit":{"score":4,"comment":"..."},"overall":{"score":4,"comment":"..."}}'
    )

    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    # 日本語はトークン消費が大きいため、評価用は多めのトークンを確保する
    eval_max_tokens = max(int(cfg["max_tokens"]), 1024)
    raw = _call_provider(provider, cfg, messages, json_mode=True, max_tokens=eval_max_tokens)
    data = _extract_json(raw)
    if data is None:
        # 途中で切れたJSONなどは1回だけ再試行する
        logger.warning("evaluation JSON parse failed (len=%d), retrying once", len(raw))
        raw = _call_provider(provider, cfg, messages, json_mode=True, max_tokens=eval_max_tokens)
        data = _extract_json(raw)
    if data is None:
        logger.warning(
            "evaluation JSON parse failed (len=%d): %s", len(raw), raw.replace("\n", " ")
        )
        raise HTTPException(status_code=502, detail="評価結果の解析に失敗しました。再試行してください。")

    items = []
    for key in ("naturalness", "continuity", "distance", "fit"):
        item = data.get(key) if isinstance(data, dict) else None
        items.append(
            {
                "key": key,
                "label": EVALUATION_LABELS[key],
                "score": _clamp_score(item.get("score") if isinstance(item, dict) else None),
                "comment": (item.get("comment") if isinstance(item, dict) else None) or "",
            }
        )
    overall = data.get("overall") if isinstance(data, dict) else None
    overall_item = {
        "key": "overall",
        "label": EVALUATION_LABELS["overall"],
        "score": _clamp_score(overall.get("score") if isinstance(overall, dict) else None),
        "comment": (overall.get("comment") if isinstance(overall, dict) else None) or "",
    }
    return {"items": items, "overall": overall_item}


def _extract_json(text: str):
    """コードフェンス・前置き文・後置き文・末尾カンマが付いた出力からdictを抽出する。"""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()

    for candidate in _json_candidates(t):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


def _json_candidates(text: str):
    """JSONとして解釈できる候補を順に生成する。"""
    # 末尾カンマ（",}" や ",]"）を除去した形も用意する
    variants = [text, _sanitize_json_strings(text), re.sub(r",\s*([}\]])", r"\1", text)]

    candidates: list[str] = []
    for t in variants:
        candidates.append(t)
        sub = _balanced_json(t)
        if sub is not None and sub != t:
            candidates.append(sub)
    return candidates


def _sanitize_json_strings(text: str):
    """文字列値の中に生の改行・タブが含まれる場合、JSONエスケープに変換する。"""
    out: list[str] = []
    in_str = False
    esc = False
    for ch in text:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
            elif ch == "\\":
                out.append(ch)
                esc = True
            elif ch == '"':
                out.append(ch)
                in_str = False
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_str = True
            out.append(ch)
    return "".join(out)


def _balanced_json(text: str):
    """先頭の { または [ から対応する閉じ括弧までの範囲を返す（前置き・後置き文を除去）。"""
    start = -1
    for i, ch in enumerate(text):
        if ch in "[{":
            start = i
            break
    if start < 0:
        return None
    end = -1
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end > start:
        return text[start:end]
    return None


def _clamp_score(value) -> int:
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        score = 0
    return max(1, min(5, score))


# ---------------------------------------------------------------- examples

@router.post("/examples", status_code=201)
def create_example(body: TrainingExampleCreate):
    conversation = json.dumps(
        [m.model_dump() for m in body.conversation], ensure_ascii=False
    )
    now = database.now_iso()
    conn = database.get_conn()
    try:
        cur = conn.execute(
            "INSERT INTO training_examples"
            " (conversation, ai_response, user_feedback, corrected_response, rating, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                conversation,
                body.ai_response,
                body.user_feedback,
                body.corrected_response,
                body.rating,
                now,
            ),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "rating": body.rating,
            "ai_response": body.ai_response,
            "corrected_response": body.corrected_response,
            "created_at": now,
        }
    finally:
        conn.close()


@router.get("/examples")
def list_examples(limit: int = 50):
    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM training_examples ORDER BY created_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    result = []
    for r in rows:
        try:
            conversation = json.loads(r["conversation"] or "[]")
            if not isinstance(conversation, list):
                conversation = []
        except json.JSONDecodeError:
            conversation = []
        result.append(
            {
                "id": r["id"],
                "conversation": conversation,
                "ai_response": r["ai_response"],
                "user_feedback": r["user_feedback"],
                "corrected_response": r["corrected_response"],
                "rating": r["rating"],
                "created_at": r["created_at"],
            }
        )
    return result


@router.delete("/examples/{example_id}", status_code=204)
def delete_example(example_id: int):
    conn = database.get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM training_examples WHERE id = ?", (example_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="学習データが見つかりません")
        conn.execute("DELETE FROM training_examples WHERE id = ?", (example_id,))
        conn.commit()
    finally:
        conn.close()
