"""対照学習モジュール（Contrast Learning v3）。

AIが生成した3案を捨ててユーザー自身が手入力した返信（manual_replaced）から、
「AIがやりがちな失敗パターンと、本人が好む自然な返信」の差分特徴を抽出してプロンプトへ教訓として提供する。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import database


@dataclass
class ContrastExample:
    """失敗生成案と手入力返信の対照ペア。"""
    batch_id: int
    contact_id: int
    context_text: str
    rejected_replies: list[str]
    chosen_reply: str
    difference_summary: str
    created_at: str


def _summarize_difference(rejected: list[str], chosen: str) -> str:
    """生成案と手入力返信の差分特徴を自動要約する。"""
    if not rejected or not chosen:
        return "手入力返信による改善"

    avg_rej_len = sum(len(r) for r in rejected) // len(rejected)
    chosen_len = len(chosen)

    traits: list[str] = []
    # 長さ比較
    if avg_rej_len - chosen_len > 30:
        traits.append("長すぎる説明を削り簡潔化")
    elif chosen_len - avg_rej_len > 30:
        traits.append("より具体的なエピソードを追加")

    # 質問比較
    rej_has_q = any("?" in r or "？" in r for r in rejected)
    chosen_has_q = "?" in chosen or "？" in chosen
    if rej_has_q and not chosen_has_q:
        traits.append("無理な質問攻めを避けリアクション・感想中心に変更")
    elif not rej_has_q and chosen_has_q:
        traits.append("自然な質問を1つ添えて会話を展開")

    # トーン・絵文字
    if "笑" in chosen and not any("笑" in r for r in rejected):
        traits.append("『笑』を交えて距離感を縮める")
    if "。" not in chosen and any("。" in r for r in rejected):
        traits.append("堅い句点『。』を排除しチャットらしい軽快さに変更")

    if not traits:
        traits.append("より自然で本人らしい口語表現への書き換え")

    return "・".join(traits)


def extract_contrast_examples(contact_id: int | None = None, limit: int = 5) -> list[ContrastExample]:
    """手入力置換されたバッチ（manual_replaced）から対照学習データを抽出する。"""
    conn = database.get_conn()
    try:
        query = (
            "SELECT gb.id, gb.contact_id, gb.trigger_message_id, gb.replacement_message_id, gb.created_at, "
            "tm.content AS trigger_content, rm.content AS replacement_content "
            "FROM generation_batches gb "
            "LEFT JOIN messages tm ON gb.trigger_message_id = tm.id "
            "LEFT JOIN messages rm ON gb.replacement_message_id = rm.id "
            "WHERE gb.outcome = 'manual_replaced' AND rm.content IS NOT NULL "
        )
        params: list[Any] = []
        if contact_id:
            query += "AND gb.contact_id = ? "
            params.append(contact_id)
        query += "ORDER BY gb.id DESC LIMIT ?"
        params.append(limit)

        batch_rows = conn.execute(query, params).fetchall()

        examples: list[ContrastExample] = []
        for brow in batch_rows:
            bid = brow["id"]
            hist_rows = conn.execute(
                "SELECT generated_text FROM generation_history WHERE batch_id = ? ORDER BY id ASC",
                (bid,),
            ).fetchall()
            rejected = [h["generated_text"].strip() for h in hist_rows if h["generated_text"]]

            chosen = (brow["replacement_content"] or "").strip()
            context = (brow["trigger_content"] or "").strip()

            if rejected and chosen:
                diff = _summarize_difference(rejected, chosen)
                examples.append(
                    ContrastExample(
                        batch_id=bid,
                        contact_id=brow["contact_id"],
                        context_text=context,
                        rejected_replies=rejected,
                        chosen_reply=chosen,
                        difference_summary=diff,
                        created_at=brow["created_at"],
                    )
                )
    finally:
        conn.close()

    return examples


def to_contrast_prompt_block(examples: list[ContrastExample], max_items: int = 2) -> str:
    """プロンプト用の対照学習ブロックを生成する。"""
    if not examples:
        return ""

    selected = examples[:max_items]
    lines = [
        "【RELEVANT CONTRAST】（過去にAI生成案が不採用となり、ユーザー自身が手入力した成功差分）",
        "AIが生成した不自然な案を避け、ユーザー本人が実際に送った正解返信の差分教訓です:",
    ]
    for i, ex in enumerate(selected, 1):
        lines.append(f"事例{i}:")
        if ex.context_text:
            lines.append(f"  相手の発言: 「{ex.context_text[:80]}」")
        lines.append(f"  避けた生成案の傾向: {ex.difference_summary}")
        lines.append(f"  実際に送った自然な返信: 「{ex.chosen_reply}」")

    return "\n".join(lines)
