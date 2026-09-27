"""いいね用BOT: プロフィール文から初回メッセージを生成するAPI。"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import database
from ..ai import factory, prompt
from ..ai.config import get_ai_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/like-bot", tags=["like-bot"])


class LikeBotRequest(BaseModel):
    profile_text: str
    condition: str = ""


class LikeBotResponse(BaseModel):
    message: str
    candidates: list[str]


def _sanitize_like_message(text: str) -> str:
    """いいねメッセージの『〜とのこと』等の不自然なAI表現・カタカナ語を自動口語化する。"""
    if not text:
        return text
    t = text
    # 1. 「〜とのこと」「〜と拝見」等の機械的AI表現の自動除去・自然な口語化
    t = re.sub(r"([^、\n]+)好きとのことですが[、,\s]*", r"\1好きなんですね！\n", t)
    t = re.sub(r"([^、\n]+)とのことですが[、,\s]*", r"\1なんですね！\n", t)
    t = re.sub(r"([^、\n]+)とのこと[、。\s]*", r"\1なんですね！\n", t)
    t = re.sub(r"と拝見(しました|して|致しました)[、。\s]*", r"見て", t)
    t = re.sub(r"と書かれて(いて|おり|ありましたので|いたので)[、。\s]*", r"見て", t)
    t = re.sub(r"とありましたので[、。\s]*", r"見て", t)

    # 2. 形式名詞の平仮名化
    t = re.sub(r"何か", "なにか", t)
    t = re.sub(r"他に", "ほかに", t)

    # 3. 不自然なカタカナ語の置換
    t = re.sub(r"リフレッシュ(する|される|になり|できます)?", r"気分転換\1", t)
    t = re.sub(r"リフレッシュ", r"気分転換", t)

    # 4. 不自然な話題転換の除去
    t = re.sub(r"[^、\n]+もいいですけど[、,\s]*", "", t)
    t = re.sub(r"[^、\n]+も気になりますけど[、,\s]*", "", t)
    t = re.sub(r"[^、\n]+以外だと[、,\s]*", "", t)
    t = re.sub(r"[^、\n]+以外で(は)?[、,\s]*", "", t)

    return t.strip()


def _format_one_sentence_per_line(text: str) -> str:
    """1文ごとに改行されたフォーマットを保証する。"""
    return prompt.format_one_sentence_per_line(text)


@router.post("/generate", response_model=LikeBotResponse)
def generate_like_message(body: LikeBotRequest):
    """プロフィール文からいいね付きメッセージを生成する（いいねBOT専用：趣味特化・本人のリアル文体・『〜とのこと』完全禁止）。"""
    if not body.profile_text.strip():
        raise HTTPException(status_code=400, detail="プロフィール文を入力してください")

    cfg = get_ai_config()
    provider = factory.get_provider(cfg["provider"], cfg["api_key"])

    system = (
        "あなたはマッチングアプリのいいね付き初回メッセージ作成専用アシスタントです。\n"
        "ユーザー本人が普段送っているリアルなチャット言葉・文体（一人称「僕」、自然な語尾「〜ですよね！」「〜なんですね！」「〜思わずいいねしちゃいました笑」等）で、相手のプロフィールからマッチ率を高める初回メッセージを3案作成します。\n\n"
        "【絶対規約：本人のリアル文体 & 『〜とのこと』等の機械的AI表現の完全禁止】\n"
        "1. 【『〜とのこと』『〜と拝見しました』等の機械的AI表現の完全禁止】\n"
        "   - 「〇〇好きとのことですが」「〇〇とのこと」「〇〇と拝見しました」「〇〇と書いてありましたので」のような、相手のプロフィールを機械的に読み上げる他人行儀で不自然な表現は【完全禁止】。\n"
        "   - 本人が直接写真や好きなものを見て自然に話しかけているような、リアルでフレンドリーな口調（例: 『〇〇好きなんですね！』『〇〇の写真、すごく美味しそうですね！』）にすること。\n"
        "2. 【趣味・好きなこと・休日の過ごし方のみに特化】\n"
        "   - プロフィール内の「趣味・好きなこと・興味・休日の過ごし方（カフェ、旅行、映画、音楽、グルメ、ドライブ、スポーツ、アニメ、読書等）」のみを具体的に拾って褒める・共感を添えること。\n"
        "3. 【仕事・学業・考え方・価値観への言及・共感は完全禁止】\n"
        "   - プロフィールに仕事（職種、会社、働き方等）、学業、考え方・価値観が書かれていても【完全に無視】すること。\n"
        "   - 「お仕事頑張っていらっしゃるんですね」「価値観に共感しました」「考え方が素敵です」等は【一切禁止】。\n"
        "4. 【相手の名前呼びは必ず「さん」付け】\n"
        "   - 最初の挨拶は必ず「〇〇さん、はじめまして！」とし、呼び捨て・ちゃん付け・あだ名は禁止。\n"
        "5. 【本人のリアルなチャット言葉・一人称「僕」の再現】\n"
        "   - 一人称は「僕」を使用し、堅苦しいAI敬語ではなく自然なチャット言葉（『僕も〜が好きなので親近感湧きました！』『思わずいいねしちゃいました笑』等）にすること。\n"
        "6. 【1文ごとの改行の絶対遵守】\n"
        "   - 必ず1文ごとに改行すること（1行につき1文のみ）。\n"
        "7. 【形式名詞の平仮名化】\n"
        "   - 「何か」は漢字を使わず必ず平仮名で「なにか」とすること。\n"
        "8. 【質問・前のめりな誘いの禁止】\n"
        "   - 初回いいねメッセージでは質問攻めや「会いたい」「行きましょう」は避け、趣味の褒め・共感と好印象（『仲良くしてもらえると嬉しいです😊』『色々お話しできると嬉しいです！』等）で結ぶこと。\n\n"
        "【4つの基本構成（この順番で作成する）】\n"
        "1. 相手の名前呼び挨拶（例: 「〇〇さん、はじめまして！」 ※必ず「さん」付け）\n"
        "2. 相手の趣味・好きなものを褒める（写真や趣味のチョイスを具体的に褒める）\n"
        "3. 自分の共感を1文添える（一人称「僕」で共通点や惹かれた理由を述べる）\n"
        "4. 結びの挨拶（例: 「仲良くしてもらえると嬉しいです😊」「お話しできると嬉しいです！」）\n\n"
        "【褒め方・共感の良例（リアルなチャット言葉）】\n"
        "〇 「〇〇さん、はじめまして！\nカフェ巡りの写真、お店の雰囲気がすごく素敵ですね！\n僕も落ち着くカフェが好きなので親近感湧きました！\n仲良くしてもらえると嬉しいです😊」\n\n"
        "〇 「〇〇さん、はじめまして！\n邦ロック好きなんですね！\n僕もよくライブやフェスに行ったりするので思わずいいねしちゃいました笑\n音楽のお話しできると嬉しいです！」\n\n"
        "〇 「〇〇さん、はじめまして！\n美味しそうなお店の写真がたくさんあって惹かれました笑\n僕も食べ歩きが好きなので色々お話ししてみたいです！\nよろしくお願いします😊」\n\n"
        "【出力形式】\n"
        "説明や前置き・かぎかっこは付けず、3案を「---」で区切ってメッセージ本文のみを出力してください。\n"
    )

    user_prompt = (
        f"【相手のプロフィール】\n{body.profile_text.strip()}\n\n"
        "上記の相手に送るいいね付きメッセージを3案作成してください。\n"
        "【重要】『〜とのこと』『〜と拝見しました』等の機械的AI表現や仕事・学業・価値観への言及は完全禁止です。『趣味・好きなこと・休日の過ごし方』のみを本人のリアルなチャット言葉（一人称「僕」、自然な語尾、1文1行改行）で褒めて共感を添えてください。"
        + (
            f"\n\n【追加条件】{body.condition}"
            if body.condition.strip()
            else ""
        )
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_prompt},
    ]

    try:
        raw = provider.generate(
            model=cfg["model"],
            messages=messages,
            temperature=cfg["temperature"],
            max_tokens=cfg["max_tokens"],
        )
    except Exception as e:
        logger.error("Like-bot generation failed: %s", e)
        raise HTTPException(status_code=502, detail="メッセージの生成に失敗しました")

    # "---" で区切られた複数案を分割
    raw_blocks = [b.strip() for b in raw.split("---") if b.strip()]
    if not raw_blocks:
        raw_blocks = [line.strip() for line in raw.strip().splitlines() if line.strip()]

    candidates = []
    for block in raw_blocks:
        cleaned = prompt.strip_brackets(block)
        sanitized = _sanitize_like_message(cleaned)
        formatted_text = _format_one_sentence_per_line(sanitized)
        if formatted_text and len(formatted_text) > 5:
            candidates.append(formatted_text)

    if not candidates:
        raise HTTPException(status_code=502, detail="空のレスポンスが返されました")

    return LikeBotResponse(
        message=candidates[0],
        candidates=candidates[:3],
    )
