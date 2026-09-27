"""学習コーパス構築モジュール（Conversation Corpus v3）。

全メッセージから会話Turnを集約し、Gold/Silver/Bronze/Negativeの教師ペアを構築する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, Literal

from .. import database

# 会話フェーズ定義
ConversationPhase = Literal["opening", "getting_to_know", "ongoing", "scheduling", "light_reaction"]
# 教師ラベル定義
PairLabel = Literal["gold", "silver", "bronze", "negative"]


@dataclass
class Turn:
    """連続する同一送信者の発言を集約した会話ターン。"""
    contact_id: int
    sender: Literal["contact", "self"]
    messages: list[dict[str, Any]]
    text: str
    created_at: str
    source: str = "manual"
    generation_history_id: int | null = None


@dataclass
class ReplyPair:
    """相手のTurnに対する自分のTurn（教師ペア）。"""
    pair_id: str
    contact_id: int
    contact_turn: Turn
    self_turn: Turn
    context_turns: list[Turn]
    phase: ConversationPhase
    label: PairLabel
    source: str
    rating: str | None = None
    created_at: str = ""
    excluded: bool = False
    excluded_reason: str = ""


def clean_chat_content(content: str) -> str:
    """本文中の独立した時刻行（例: '22:58', '10:05'）のみを除去し、本文を正規化する。"""
    lines = content.splitlines()
    cleaned_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", stripped):
            continue
        cleaned_lines.append(line)
    return "\n".join(cleaned_lines).strip()


def classify_phase(
    turn_index: int,
    total_turns: int,
    contact_text: str,
    self_text: str,
) -> ConversationPhase:
    """会話ターンと発言内容から会話フェーズを判定する。"""
    combined = f"{contact_text}\n{self_text}".lower()

    # 1. 初回挨拶
    if any(g in combined for g in ["はじめまして", "初めまして", "マッチありがとうございます", "よろしくお願い"]):
        return "opening"

    # 2. scheduling: 会う約束、日程、場所
    sched_keywords = ["行こう", "行きましょ", "予定", "いつあいて", "空いてる", "都合", "何曜日", "来週", "今週末", "日程", "渋谷", "新宿", "カフェ行"]
    if any(k in combined for k in sched_keywords):
        return "scheduling"

    # 3. getting_to_know: 質問やお互いの情報交換
    if "?" in combined or "？" in combined or any(k in combined for k in ["仕事", "趣味", "休み", "普段", "何してる", "どこ住み", "好きな"]):
        return "getting_to_know"

    # 4. light_reaction: 極めて短いリアクション（合計30文字未満で質問なし）
    if len(contact_text) < 20 and len(self_text) < 20 and "?" not in self_text and "？" not in self_text:
        return "light_reaction"

    # 5. 初期ターン
    if turn_index <= 2:
        return "opening"

    # 6. ongoing: 通常の継続会話
    return "ongoing"


def build_turns_for_contact(contact_id: int) -> list[Turn]:
    """1つのcontactの全メッセージを時系列取得し、連続同一senderを結合してTurnリストを生成する。"""
    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT id, contact_id, sender, content, created_at, source, generation_history_id "
            "FROM messages WHERE contact_id = ? ORDER BY id ASC",
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return []

    turns: list[Turn] = []
    curr_sender: str | None = None
    curr_msgs: list[dict[str, Any]] = []

    for r in rows:
        c = clean_chat_content(r["content"] or "")
        if not c:
            continue
        msg_dict = dict(r)
        msg_dict["cleaned_content"] = c

        if r["sender"] == curr_sender:
            curr_msgs.append(msg_dict)
        else:
            if curr_msgs and curr_sender:
                combined_text = "\n".join(m["cleaned_content"] for m in curr_msgs).strip()
                primary_source = curr_msgs[-1]["source"] or "manual"
                gen_id = curr_msgs[-1].get("generation_history_id")
                turns.append(
                    Turn(
                        contact_id=contact_id,
                        sender=curr_sender,
                        messages=curr_msgs,
                        text=combined_text,
                        created_at=curr_msgs[0]["created_at"] or "",
                        source=primary_source,
                        generation_history_id=gen_id,
                    )
                )
            curr_sender = r["sender"]
            curr_msgs = [msg_dict]

    if curr_msgs and curr_sender:
        combined_text = "\n".join(m["cleaned_content"] for m in curr_msgs).strip()
        primary_source = curr_msgs[-1]["source"] or "manual"
        gen_id = curr_msgs[-1].get("generation_history_id")
        turns.append(
            Turn(
                contact_id=contact_id,
                sender=curr_sender,
                messages=curr_msgs,
                text=combined_text,
                created_at=curr_msgs[0]["created_at"] or "",
                source=primary_source,
                generation_history_id=gen_id,
            )
        )

    return turns


def extract_reply_pairs(contact_id: int | None = None) -> list[ReplyPair]:
    """DBからTurnを構築し、有効な教師ペア（ReplyPair）を抽出・分類する。"""
    conn = database.get_conn()
    try:
        if contact_id is not None:
            c_rows = conn.execute("SELECT id FROM contacts WHERE id = ?", (contact_id,)).fetchall()
        else:
            c_rows = conn.execute("SELECT id FROM contacts ORDER BY id ASC").fetchall()

        # bad 評価された generation_history ID 一覧を取得
        bad_hist_rows = conn.execute("SELECT id FROM generation_history WHERE rating = 'bad'").fetchall()
        bad_hist_ids = {r["id"] for r in bad_hist_rows}
    finally:
        conn.close()

    all_pairs: list[ReplyPair] = []

    for crow in c_rows:
        cid = crow["id"]
        turns = build_turns_for_contact(cid)
        total_turns = len(turns)

        for i in range(len(turns) - 1):
            t_curr = turns[i]
            t_next = turns[i + 1]

            # contact turn -> 次の self turn のみを対象
            if t_curr.sender == "contact" and t_next.sender == "self":
                contact_text = t_curr.text.strip()
                self_text = t_next.text.strip()

                if not contact_text or not self_text:
                    continue

                # 汚染チェック1: self と contact に同一テキスト（誤登録の疑い）
                if contact_text == self_text:
                    pair = ReplyPair(
                        pair_id=f"pair_{cid}_{i}",
                        contact_id=cid,
                        contact_turn=t_curr,
                        self_turn=t_next,
                        context_turns=turns[max(0, i - 2) : i],
                        phase="ongoing",
                        label="negative",
                        source=t_next.source,
                        created_at=t_next.created_at,
                        excluded=True,
                        excluded_reason="同一テキストのsender誤登録疑い",
                    )
                    all_pairs.append(pair)
                    continue

                # 汚染チェック2: JSONやAI_QUESTION等の混入
                if "{" in self_text and '"replies"' in self_text or "[AI_QUESTION]" in self_text:
                    continue

                # ラベル判定
                gen_id = t_next.generation_history_id
                if gen_id and gen_id in bad_hist_ids:
                    label: PairLabel = "negative"
                elif t_next.source == "manual":
                    label = "gold"
                elif t_next.source == "generated":
                    label = "silver"
                else:
                    label = "bronze"

                phase = classify_phase(i, total_turns, contact_text, self_text)

                pair = ReplyPair(
                    pair_id=f"pair_{cid}_{i}",
                    contact_id=cid,
                    contact_turn=t_curr,
                    self_turn=t_next,
                    context_turns=turns[max(0, i - 2) : i],
                    phase=phase,
                    label=label,
                    source=t_next.source,
                    created_at=t_next.created_at,
                    excluded=False,
                )
                all_pairs.append(pair)

    return all_pairs


def extract_same_contact_manual_gold_pairs(contact_id: int, limit: int = 10) -> list[ReplyPair]:
    """同じ相手に対するユーザーの手入力返信（Goldペア）を直近から取得する。"""
    pairs = extract_reply_pairs(contact_id)
    gold_pairs = [p for p in pairs if p.contact_id == contact_id and p.label == "gold" and not p.excluded]
    return gold_pairs[-limit:] if len(gold_pairs) > limit else gold_pairs


def get_corpus_statistics(contact_id: int | None = None) -> dict[str, Any]:
    """コーパス内のGold/Silver/Bronze/Negative件数および除外理由を集計する。"""
    pairs = extract_reply_pairs(contact_id)
    gold = [p for p in pairs if p.label == "gold" and not p.excluded]
    silver = [p for p in pairs if p.label == "silver" and not p.excluded]
    bronze = [p for p in pairs if p.label == "bronze" and not p.excluded]
    negative = [p for p in pairs if p.label == "negative" or p.excluded]
    excluded = [p for p in pairs if p.excluded]

    return {
        "total_pairs": len(pairs),
        "valid_pairs": len(pairs) - len(excluded),
        "gold_count": len(gold),
        "silver_count": len(silver),
        "bronze_count": len(bronze),
        "negative_count": len(negative),
        "excluded_count": len(excluded),
        "excluded_reasons": [p.excluded_reason for p in excluded if p.excluded_reason],
    }
