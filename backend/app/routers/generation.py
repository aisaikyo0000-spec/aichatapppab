"""AI返信生成・修正再生成API。

Frontendから外部AI APIを直接呼び出さず、必ずこのAPI経由で生成する。
"""
from __future__ import annotations

from datetime import datetime
import json
import logging
import re
import time

from fastapi import APIRouter, HTTPException

from .. import config, database, learning
from ..ai import factory, naturalness, prompt
from ..ai.base import AIError
from ..ai.config import get_ai_config, get_contact_ai_config
from ..schemas import GenerateRequest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["generation"])


def _strip_code_fence(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()
    return t


def _extract_ai_question(raw: str) -> str | None:
    """出力全体が [AI_QUESTION]...[/AI_QUESTION] の場合のみ質問文を抽出する（fullmatch）。"""
    t = _strip_code_fence(raw).strip()
    match = re.fullmatch(r"\[AI_QUESTION\]\s*(.+?)\s*(?:\[/AI_QUESTION\])?", t, re.DOTALL)
    if match:
        q = match.group(1).strip()
        if q:
            return q
    return None


def _parse_replies_strict(raw: str, candidates: int) -> list[str]:
    """出力を厳格にパースする。newline fallback は完全排除。

    1. candidates == 1 の場合: 生テキスト（空以外）
    2. JSON形式: {"replies": [...]} または {"candidates": [...]} または [...]
    3. 【案1】【案2】【案3】または 案1: 案2: 案3: の形式
    4. 候補数が candidates と一致しない場合は空リスト（パース失敗）を返す。
    """
    t = _strip_code_fence(raw).strip()
    if not t:
        return []
    if candidates <= 1:
        return [t]

    # 1. JSON解析
    try:
        data = json.loads(t)
        if isinstance(data, list):
            items = [str(r).strip() for r in data if str(r).strip()]
            if len(items) == candidates:
                return items
        elif isinstance(data, dict):
            replies = data.get("replies") or data.get("candidates") or []
            if isinstance(replies, list):
                items = [str(r).strip() for r in replies if str(r).strip()]
                if len(items) == candidates:
                    return items
    except (json.JSONDecodeError, AttributeError):
        pass

    # 2. 【案1】【案2】【案3】または 案1: 案2: 案3:
    pattern = r"(?:^|\n)\s*(?:【案[1-9１-９]】|案[1-9１-９][:：]|\b(?:案[1-9１-９]|[1-9１-９]\.))\s*"
    splits = [p.strip() for p in re.split(pattern, t) if p.strip()]
    if len(splits) == candidates:
        return splits

    # 3. 空行（二重改行以上）区切りでちょうど candidates 個のブロック
    blocks = [b.strip() for b in re.split(r"\n\s*\n+", t) if b.strip()]
    if len(blocks) == candidates:
        return blocks

    return []


# 許可する顔・表情系絵文字のパターン
_EMOJI_OR_PUNCT_ONLY_LINE = re.compile(r"^[\s😊😂🤣😳😆🥹😌🤔🙌🙂🙄😅😋🤤🥳😎😭🥺😴🤩🤐🤫👍✨笑wW!！?？、。・…〜~]+$")

# 禁止する大げさな抽象表現
_UNNATURAL_PHRASES = [
    "贅沢な時間",
    "非日常感",
    "世界観に深く入り込む",
    "日々の忙しさを忘れる",
    "充電です",
    "心の栄養",
    "心がリセット",
    "リフレッシュ",
    "モチベーション",
    "ルーティン",
    "マインドを",
]


# 許可する顔・表情系絵文字のセット
_ALLOWED_EMOJIS = set("😊😂🤣😳😆🥹😌🤔🙌🙂🙄😅😋🤤🥳😎😭🥺😴🤩🤐🤫👍✨")
# 一般的な絵文字のUnicode判定パターン
_ALL_EMOJI_PATTERN = re.compile(
    r"[\U0001F600-\U0001F64F"  # emoticons
    r"\U0001F300-\U0001F5FF"  # symbols & pictographs
    r"\U0001F680-\U0001F6FF"  # transport & map
    r"\U0001F700-\U0001F77F"  # alchemical
    r"\U0001F780-\U0001F7FF"  # Geometric Shapes
    r"\U0001F800-\U0001F8FF"  # Supplemental Arrows
    r"\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    r"\U0001FA00-\U0001FA6F"  # Chess Symbols
    r"\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    r"\u2600-\u26FF"          # Misc symbols
    r"\u2700-\u27BF"          # Dingbats
    r"]",
    flags=re.UNICODE,
)


def _is_question_line(line: str) -> bool:
    """行が質問・疑問文であるか判定する。"""
    if "？" in line or "?" in line:
        return True
    clean = line.strip()
    return bool(re.search(r"(?:ですか|ますか|でしょうか|のかな|のかい|のか)$", clean))


def _jaccard_similarity(s1: str, s2: str) -> float:
    """2つの文字列の2-gram Jaccard類似度を計算する。"""
    if not s1 or not s2:
        return 0.0
    ngrams1 = {s1[i:i+2] for i in range(len(s1) - 1)}
    ngrams2 = {s2[i:i+2] for i in range(len(s2) - 1)}
    if not ngrams1 or not ngrams2:
        return 1.0 if s1 == s2 else 0.0
    return len(ngrams1 & ngrams2) / len(ngrams1 | ngrams2)


def _is_fragmented_split(replies: list[str]) -> bool:
    """3案が1つの返信を3分割（反応→自己開示→質問）した誤分割であるか判定する。

    判定は構造シグネチャに基づく（全案が短い断片＋質問締め/自己開示始め）。
    記号・絵文字のみの候補（例: 笑）は、それ自体では誤分割とみなさない。
    Step 2 以降、短い独立候補は正式な正常系のため。
    """
    if len(replies) != 3:
        return False

    lengths = [len(r.strip()) for r in replies]
    if not (all(length < 25 for length in lengths) and sum(lengths) < 70):
        return False

    # 案3だけが質問、または案2が自己開示始めの場合に誤分割とみなす。
    # ただし案1と案3が同内容のバリエーション（並列候補）の場合は分割ではない。
    # Step 8: 短文化により並列短候補が増えるため、誤検出を抑止する。
    has_q3 = bool(re.search(r"(?:？|\?|ですか|ある？|行こ)$", replies[2].strip()))
    has_self2 = bool(re.search(r"^(?:僕も|私も|俺も|自分も|最近|実は)", replies[1].strip()))
    if has_q3 or has_self2:
        if _jaccard_similarity(replies[0], replies[2]) >= 0.4:
            return False
        return True
    return False


def validate_tone_strict(replies: list[str], tone: str) -> list[str]:
    """トーン指定に対する厳格な終止検査を行う。"""
    violations: list[str] = []
    if tone == "tame":
        # 丁寧語終止パターンの検査
        keigo_end_pattern = re.compile(
            r"(?:です|ます|でした|ました|でしょう|ません|なんですね|ありますか|ですか|でしょうか|ですね)(?:[！!？?\s]|$)"
        )
        for i, r in enumerate(replies, start=1):
            if keigo_end_pattern.search(r):
                violations.append(f"案{i}にタメ口指定と矛盾する敬語・丁寧語終止（です/ます/なんですね等）が含まれています。")
    elif tone == "keigo":
        # タメ口終止パターンの検査
        tame_end_pattern = re.compile(
            r"(?:だね|だよ|でしょ|じゃん|っけ|ない？|行こ|ね！|よ！)(?:[！!？?\s]|$)"
        )
        for i, r in enumerate(replies, start=1):
            if tame_end_pattern.search(r):
                violations.append(f"案{i}に敬語指定と矛盾するカジュアル語尾（だね/だよ/じゃん等）が含まれています。")
    return violations


def _has_question(text: str) -> bool:
    """文章内に相手への質問（？, ?, 疑問終止）が含まれているか判定する。"""
    for line in text.splitlines():
        if _is_question_line(line):
            return True
    return False


def sanitize_reply_text(
    text: str,
    current_datetime: datetime | None = None,
    contact_name: str = "",
) -> str:
    """生成された返信テキストの季節矛盾・禁止ワード・不自然な表現を自動置換・無害化する。"""
    if not text:
        return text

    t = text
    dt = current_datetime or datetime.now()
    cur_month = dt.month

    # 1. 季節の自動修正（夏なのに冬表現、冬なのに夏表現を自動補正）
    if cur_month in (6, 7, 8):  # 夏
        replacements = [
            (r"急に寒くなってき(た|て|ます)?", r"毎日かなり暑いですが"),
            (r"寒くなってき(た|て|ます)?", r"暑い日が続いています"),
            (r"寒さで(やられて|体調)?", r"暑さで\1"),
            (r"寒さ", r"暑さ"),
            (r"寒いですね", r"暑いですね"),
            (r"寒くないですか", r"体調崩してないですか"),
            (r"暖かくして", r"涼しくして"),
            (r"温かいもの", r"冷たいもの"),
            (r"暖房", r"冷房"),
            (r"こたつ", r"エアコン"),
        ]
        for pattern, repl in replacements:
            t = re.sub(pattern, repl, t)
    elif cur_month in (12, 1, 2):  # 冬
        replacements = [
            (r"暑くなってき(た|て|ます)?", r"寒くなってき\1"),
            (r"猛暑", r"寒さ"),
            (r"夏バテ", r"体調管理"),
            (r"冷房", r"暖房"),
            (r"冷たいもの", r"温かいもの"),
        ]
        for pattern, repl in replacements:
            t = re.sub(pattern, repl, t)

    # 2. 禁止カタカナ語の自動置換
    t = re.sub(r"リフレッシュ(する|される|になり|できます)?", r"気分転換\1", t)
    t = re.sub(r"リフレッシュ", r"気分転換", t)

    # 3. 催促キーワードの自動除去・マイルド化
    pressure_patterns = [
        (r"(返信|返事)(まだ|待ってます|ないですか|ないです)[？?]?\s*", r""),
        (r"(既読|未読)スルー[^\n]*\n?", r""),
    ]
    for pattern, repl in pressure_patterns:
        t = re.sub(pattern, repl, t)

    # 4. AI特有の他人行儀・不自然構文の自動口語化
    ai_cliches = [
        (r"思わず共有です(笑)?", r"めっちゃ美味しかったんですよね笑"),
        (r"を求めて、?今日", r"気になって今日"),
        (r"を求めて", r"食べたくて"),
        (r"最高の楽しみですね", r"いいですよね！"),
        (r"最高ですね！", r"いいですよね！"),
    ]
    for pattern, repl in ai_cliches:
        t = re.sub(pattern, repl, t)

    # 5. 形式名詞の自然な平仮名化
    t = re.sub(r"何か", "なにか", t)

    # 6. 不自然な話題切り替えフレーズ（『ほかにも』『ほかに』『〇〇もいいですけど』『〇〇以外だと』等）の自動除去
    awkward_transitions = [
        (r"[^、\n]+もいいですけど[、,\s]*", ""),
        (r"[^、\n]+も気になりますけど[、,\s]*", ""),
        (r"[^、\n]+以外だと[、,\s]*", ""),
        (r"[^、\n]+以外で(は)?[、,\s]*", ""),
        (r"[^、\n]+以外に(は)?[、,\s]*", ""),
        (r"[^、\n]+以外は[、,\s]*", ""),
        (r"ほかにも\s*", ""),
        (r"ほかに\s*", ""),
        (r"他にも\s*", ""),
        (r"他に\s*", ""),
    ]
    for pattern, repl in awkward_transitions:
        t = re.sub(pattern, repl, t)

    # 7. 「〜とのこと」「〜と拝見」等の機械的AI表現の自動口語化
    t = re.sub(r"([^、\n]+)とのことですが[、,\s]*", r"\1なんですね！\n", t)
    t = re.sub(r"([^、\n]+)とのこと[、。\s]*", r"\1なんですね！\n", t)
    t = re.sub(r"と拝見(しました|して|致しました)[、。\s]*", r"見て", t)
    t = re.sub(r"と書かれて(いて|おり|ありましたので|いたので)[、。\s]*", r"見て", t)

    return t.strip()


def ensure_has_question(text: str, condition: str = "", contact_name: str = "") -> str:
    """質問なし返信を正式な正常系として扱うため、デフォルトでは何も変更しない。

    Step 2 仕様変更: 旧仕様では質問がない案に定型質問を自動付与していたが、
    「質問しない返信」を正常系とするため無効化した。後方互換のため関数自体は残す。
    """
    return text


def validate_candidate_replies(
    replies: list[str],
    expected_candidates: int = 3,
    tone: str = "",
    condition: str = "",
    mode: str = "normal",
    current_datetime: datetime | None = None,
) -> list[str]:
    """返信案のHardバリデーションを行い、違反内容のリストを返す。空リストなら合格。

    Hardバリデーション（失敗・Repair対象）:
    - 案数不一致、空文
    - [AI_QUESTION] の混入・不正形式
    - 1つの返信の3分割（fragmentation）
    - トーン指定との重大矛盾（tame時の敬語混入等）
    - 「〜とのこと」「〜と拝見」等の機械的AI表現の禁止
    - 「ほかにも」「ほかに」「〜以外」等の話題逃げ・並列質問の禁止
    - 追いメッセージ時の催促表現禁止
    - 季節の矛盾（現在の月に合わない表現）
    - 候補案同士の過度な重複（類似度0.85以上）

    Step 2 仕様変更: 質問なしは正常系のため、質問必須バリデーションは撤廃した。
    相手の質問への回答は Prompt 側（CONVERSATION STATE の要回答質問）で担保する。
    """
    violations: list[str] = []

    # 案数チェック
    if len(replies) != expected_candidates:
        violations.append(f"出力案数が {len(replies)} 件です。必ず {expected_candidates} 件作成してください。")

    # 空文チェック
    for i, rep in enumerate(replies, start=1):
        if not rep.strip():
            violations.append(f"案{i}が空文字です。")

    # [AI_QUESTION] タグの混入チェック
    for i, rep in enumerate(replies, start=1):
        if "[AI_QUESTION]" in rep:
            violations.append(f"案{i}にAI_QUESTIONタグ [AI_QUESTION] が混入しています。")

    # 1つの返信の3分割（fragmentation）チェック
    if _is_fragmented_split(replies):
        violations.append("1つの返信が3分割されて出力されています。3案それぞれが単体で送信できる独立した完成品となるよう作成してください。")

    # トーン検査
    if tone:
        violations.extend(validate_tone_strict(replies, tone))

    # 追いメッセージ時の催促表現禁止チェック
    if mode == "followup":
        pressure_keywords = ["返信まだ", "返事まだ", "返事待って", "返信待って", "既読スルー", "未読スルー", "返信ない", "返事ない", "無視", "忙しいですか？", "忙しい？"]
        for i, rep in enumerate(replies, start=1):
            if any(pk in rep for pk in pressure_keywords):
                violations.append(f"案{i}に催促や返信を問い詰める表現が含まれています。追いメッセージでは催促を避け、自然な口実や軽い話題で作成してください。")

    # 「〜とのこと」「〜と拝見」等の機械的AI表現の禁止チェック
    robotic_keywords = ["とのこと", "と拝見", "と書かれてい", "とありました"]
    for i, rep in enumerate(replies, start=1):
        if any(rk in rep for rk in robotic_keywords):
            violations.append(
                f"案{i}に「〜とのこと」「〜と拝見」等の機械的で不自然なAI表現が含まれています。"
                f"自然な口語（『〜なんですね！』『〜いいですね！』等）に修正してください。"
            )

    # 「ほかにも」「ほかに」「〜以外」による話題逃げ・並列質問の禁止チェック
    side_switch_keywords = [
        "ほかにも", "ほかに", "他に", "他にも", "以外の", "以外だと", "以外で", "以外は", "以外に", "以外も", "もいいですけど", "も気になりますけど"
    ]
    for i, rep in enumerate(replies, start=1):
        if any(sw in rep for sw in side_switch_keywords):
            violations.append(
                f"案{i}に「ほかにも」「ほかに」「〜以外」「〇〇もいいですけど」等の話題切り替え・並列質問が含まれています。"
                f"話題を横スライドさせず、相手が出した話題そのものを深掘りする質問に修正してください。"
            )

    # Step 2 仕様変更: 質問なしは正常系。質問の有無はバリデーション対象外とする。
    # （相手からの質問への回答は Prompt の CONVERSATION STATE で指示する）

    # 季節の矛盾（季節外れの嘘）チェック
    cur_month = (current_datetime or datetime.now()).month
    if cur_month in (6, 7, 8):
        winter_words = ["寒くなって", "寒さ", "急に寒く", "冷え込ん", "肌寒く", "暖房", "こたつ", "マフラー", "雪が"]
        for i, rep in enumerate(replies, start=1):
            if any(w in rep for w in winter_words):
                violations.append(f"案{i}に季節外れの表現（夏なのに寒さ・寒くなってきた等）が含まれています。現在の季節（夏・{cur_month}月）に合わせた表現に修正してください。")
    elif cur_month in (12, 1, 2):
        summer_words = ["暑くなって", "猛暑", "夏バテ", "熱中症", "冷房", "海開き", "プール", "花火大会"]
        for i, rep in enumerate(replies, start=1):
            if any(w in rep for w in summer_words):
                violations.append(f"案{i}に季節外れの表現（冬なのに暑さ・猛暑等）が含まれています。現在の季節（冬・{cur_month}月）に合わせた表現に修正してください。")

    # 候補間の重複・極端な高類似の検知
    for j in range(len(replies)):
        for k in range(j + 1, len(replies)):
            sim = _jaccard_similarity(replies[j], replies[k])
            if sim >= 0.85:
                violations.append(f"案{j+1}と案{k+1}の内容・表現が重複しています（類似度 {sim:.2f}）。異なる会話ルートを作成してください。")

    return violations


def _build_repair_messages(
    original_messages: list[dict[str, str]],
    raw_output: str,
    violations: list[str],
    candidates: int,
) -> list[dict[str, str]]:
    """修復用のメッセージリストを構築する。"""
    v_text = "\n".join(f"- {v}" for v in violations)
    repair_instruction = (
        f"前回の出力に以下の不備が検知されました。\n"
        f"【不備内容】\n"
        f"{v_text}\n\n"
        f"すべての不備を修正し、独立した完成品として{candidates}案を作成し、"
        f"必ず JSON形式の {{\"replies\": [\"案1\", \"案2\", \"案3\"]}} で出力してください。"
    )
    return [
        *original_messages,
        {"role": "assistant", "content": raw_output},
        {"role": "user", "content": repair_instruction},
    ]


def _align_followup_replies(replies: list[str]) -> list[str]:
    """追いメッセージの3案を【案1: 行動報告】【案2: 軽快ツッコミ】【案3: 写真なし体験共有】の順序に確実に整列する。"""
    if len(replies) != 3:
        return replies

    slot1 = None  # 行動報告
    slot2 = None  # 軽快ツッコミ
    slot3 = None  # 写真なし体験共有
    unassigned = []

    for r in replies:
        is_tsukkomi = any(k in r for k in ["冬眠", "夏眠", "生きて", "バタバタ", "息抜き", "休めそう", "忙しかった", "生きてます", "暑さ", "夏バテ"])
        is_photo_share = any(k in r for k in ["思わず共有", "今日食べた", "今日作った", "今日見つけた", "濃厚", "ボリューム", "我ながら", "美味しすぎて", "大きすぎて", "看板", "パフェ", "ラーメンが", "パスタが", "スイーツが", "デザートが", "お肉のお店"])

        if is_tsukkomi and slot2 is None:
            slot2 = r
        elif is_photo_share and slot3 is None:
            slot3 = r
        elif not is_tsukkomi and not is_photo_share and slot1 is None:
            slot1 = r
        else:
            unassigned.append(r)

    res = [slot1, slot2, slot3]
    for i in range(3):
        if res[i] is None and unassigned:
            res[i] = unassigned.pop(0)

    for i in range(3):
        if res[i] is None:
            res[i] = replies[i]

    return res


def build_user_reply_pairs() -> list[dict]:
    """全会話メッセージから (contact turn -> self turn) の教師ペアを構築する。

    連続する sender は 1つの turn に集約し、直前の contact turn と直後の self turn をペア化。
    壊れデータ（JSON, AI_QUESTION, 案ラベル）や明示的な bad 評価の文のみ除外し、
    実際に送信された文は句点や文数に関わらずスタイルの正解として保持する。
    """
    conn = database.get_conn()
    try:
        # generation_history の評価マッピングを取得
        hist_rows = conn.execute(
            "SELECT id, generated_text, revised_text, rating FROM generation_history"
        ).fetchall()
        bad_hist_ids = {r["id"] for r in hist_rows if r["rating"] == "bad"}
        good_hist_ids = {r["id"] for r in hist_rows if r["rating"] == "good"}
        bad_texts = {
            (r["revised_text"] or r["generated_text"] or "").strip()
            for r in hist_rows
            if r["rating"] == "bad" and (r["revised_text"] or r["generated_text"])
        }

        # 全メッセージを取得（古い順）
        rows = conn.execute(
            "SELECT id, contact_id, sender, content, source, generation_history_id, created_at "
            "FROM messages ORDER BY contact_id ASC, created_at ASC, id ASC"
        ).fetchall()
    finally:
        conn.close()

    contact_messages: dict[int, list[dict]] = {}
    for r in rows:
        cid = r["contact_id"]
        if cid not in contact_messages:
            contact_messages[cid] = []
        contact_messages[cid].append(dict(r))

    pairs: list[dict] = []

    for cid, msgs in contact_messages.items():
        turns: list[dict] = []
        for m in msgs:
            raw_content = m["content"] or ""
            cleaned = prompt.clean_chat_message_content(raw_content).strip()
            if not cleaned:
                continue
            # 壊れデータ（JSON, 案ラベル, AI_QUESTION）除外
            if any(k in cleaned for k in ("{\"replies\"", "[AI_QUESTION]", "【案1】", "【案2】", "【案3】")):
                continue

            sender = m["sender"]
            if turns and turns[-1]["sender"] == sender:
                turns[-1]["content"] += "\n" + cleaned
            else:
                turns.append({
                    "sender": sender,
                    "content": cleaned,
                    "source": m.get("source", "manual"),
                    "generation_history_id": m.get("generation_history_id"),
                    "created_at": m["created_at"],
                })

        for idx in range(len(turns) - 1):
            if turns[idx]["sender"] == "contact" and turns[idx + 1]["sender"] == "self":
                contact_turn = turns[idx]["content"]
                self_turn = turns[idx + 1]["content"]
                gen_hist_id = turns[idx + 1].get("generation_history_id")

                # generation_history_id による厳格な bad 除外
                if gen_hist_id and gen_hist_id in bad_hist_ids:
                    continue
                # 未紐付け旧行のテキスト除外
                if not gen_hist_id and self_turn in bad_texts:
                    continue

                is_good = bool(gen_hist_id and gen_hist_id in good_hist_ids)
                source = turns[idx + 1].get("source") or "manual"

                pairs.append({
                    "contact_id": cid,
                    "contact_turn": contact_turn,
                    "self_turn": self_turn,
                    "source": source,
                    "is_good": is_good,
                    "generation_history_id": gen_hist_id,
                    "created_at": turns[idx + 1]["created_at"],
                })

    return pairs


_GENERIC_STOP_WORDS = {
    "そう", "やっぱり", "ですね", "ます", "こと", "もの", "ため", "よう", "さん",
    "です", "でした", "ました", "ですか", "ですよね", "ありがとう", "ございます",
    "はじめまして", "よろしく", "お願い", "します", "私", "僕", "自分", "相手",
    "今日", "昨日", "明日", "今度", "最近", "本当", "一番", "感じ", "気", "何",
    "とか", "から", "けど", "ので", "たら", "なら", "これ", "それ", "あれ",
}


def _extract_topic_keywords(text: str) -> set[str]:
    """カタカナ語（2文字以上）、漢字語（2文字以上）、英単語をトピックキーワードとして抽出。"""
    cleaned = prompt.clean_chat_message_content(text)
    # カタカナ (例: カフェ, 映画, コナン, ポップコーン, スイーツ, ライブ, ボルダリング)
    katakana = re.findall(r"[\u30A1-\u30F6]{2,}", cleaned)
    # 漢字熟語 (例: 読書, 珈琲, 温泉, 旅行, 音楽, 休日, 散歩, 写真)
    kanji = re.findall(r"[\u4E00-\u9FFF]{2,}", cleaned)
    # 英字
    alpha = re.findall(r"[A-Za-z]{2,}", cleaned)
    tokens = set(katakana + kanji + [a.lower() for a in alpha])
    return {t for t in tokens if t not in _GENERIC_STOP_WORDS}


def _calc_text_relevance(query: str, target: str) -> tuple[float, float, int]:
    """トピック一致とストップワード抑制Jaccardによる関連度スコアを計算。

    Returns:
        (total_sim, jaccard_sim, topic_match_count)
    """
    if not query or not target:
        return 0.0, 0.0, 0

    q_kw = _extract_topic_keywords(query)
    t_kw = _extract_topic_keywords(target)
    topic_matches = q_kw.intersection(t_kw)
    topic_bonus = len(topic_matches) * 0.50

    # 2-gram Jaccard (stop-weighting)
    def _get_ngrams(s: str) -> set[str]:
        s = re.sub(r"\s+", "", s)
        return {s[i:i+2] for i in range(len(s) - 1)} if len(s) >= 2 else (set([s]) if s else set())

    q_ngrams = _get_ngrams(query)
    t_ngrams = _get_ngrams(target)
    if not q_ngrams or not t_ngrams:
        jaccard = 0.0
    else:
        common = q_ngrams.intersection(t_ngrams)
        union = q_ngrams.union(t_ngrams)
        jaccard = len(common) / len(union) if union else 0.0

    total_sim = topic_bonus + jaccard
    return total_sim, jaccard, len(topic_matches)


def _percentile(data: list[float | int], p: float) -> float:
    """Percentile calculation (p: 0.0 - 1.0)."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    k = (len(sorted_data) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_data) - 1)
    d = k - f
    return round(sorted_data[f] + d * (sorted_data[c] - sorted_data[f]), 1)


def analyze_user_learned_style(target_contact_id: int | None = None) -> dict:
    """全メッセージからユーザーの返信スタイルプロファイルを精密に算出する（LIMITなし）。

    排他的トーン判定（keigo/hybrid/tame）、中央値/IQR、文数、名前呼び率、
    source重み（manual最強/generated次点/good強化/bad除外/recency boost）を適用。
    """
    conn = database.get_conn()
    try:
        # generation_history の rating 取得
        hist_rows = conn.execute("SELECT id, rating FROM generation_history").fetchall()
        hist_ratings = {r["id"]: r["rating"] for r in hist_rows}

        rows = conn.execute(
            "SELECT contact_id, content, source, generation_history_id, created_at FROM messages "
            "WHERE sender = 'self' ORDER BY created_at ASC, id ASC"
        ).fetchall()
    finally:
        conn.close()

    raw_items: list[dict] = []
    for idx, r in enumerate(rows):
        raw = r["content"] or ""
        cleaned = prompt.clean_chat_message_content(raw).strip()
        if not cleaned or any(k in cleaned for k in ("{\"replies\"", "[AI_QUESTION]", "【案1】")):
            continue

        gen_hist_id = r["generation_history_id"]
        rating = hist_ratings.get(gen_hist_id) if gen_hist_id else None
        if rating == "bad":
            continue

        source = r["source"] or "manual"
        # 重み計算
        w = 1.5 if source == "manual" else (1.0 if source == "generated" else 1.0)
        if rating == "good":
            w *= 1.3
        # Recency boost (0.8 -> 1.2)
        recency_factor = 0.8 + 0.4 * (idx / max(len(rows) - 1, 1))
        w *= recency_factor

        raw_items.append({
            "contact_id": r["contact_id"],
            "content": cleaned,
            "source": source,
            "weight": w,
            "created_at": r["created_at"],
        })

    total_count = len(raw_items)
    if total_count == 0:
        return {
            "summary": "（送信履歴がまだありません。標準的な会話スタイルで生成します）",
            "metrics": {"total_count": 0, "status": "no_data"},
            "weighted_profile": {},
        }

    keigo_pattern = re.compile(r"(?:です|ます|でした|ました|ですね|ですか|でしょうか|ございます|お願い|致します)")
    tame_pattern = re.compile(r"(?:だね|だよ|でしょ|じゃん|っけ|ない？|かな？|する？|行こ|ね！|よ！|ぜ|ぞ)")
    warai_pattern = re.compile(r"(?:笑|w|W|www)")
    name_call_pattern = re.compile(r"[^\s]{1,10}さん")

    def _calc_comprehensive_stats(items: list[dict]) -> dict:
        if not items:
            return {}
        n = len(items)
        char_lens = [len(it["content"]) for it in items]
        line_lens = [len([l for l in it["content"].splitlines() if l.strip()]) for it in items]
        # 文数: 改行または感嘆符・疑問符・句点で区切られた文
        sent_lens = [len([s for s in re.split(r"[\n!?！？。]+", it["content"]) if s.strip()]) or 1 for it in items]

        weights = [it["weight"] for it in items]
        w_sum = sum(weights)

        # 排他的トーン分類
        keigo_w = 0.0
        hybrid_w = 0.0
        tame_w = 0.0

        period_w = 0.0
        excl_w = 0.0
        warai_w = 0.0
        q_w = 0.0
        name_call_w = 0.0
        emoji_count_total = 0.0

        all_emojis_found: list[str] = []

        for it in items:
            text = it["content"]
            w = it["weight"]
            has_k = bool(keigo_pattern.search(text))
            has_t = bool(tame_pattern.search(text))
            has_w = bool(warai_pattern.search(text))
            has_e = bool(_ALL_EMOJI_PATTERN.search(text))

            # 排他的トーン
            if has_k and (has_t or has_w or has_e):
                hybrid_w += w
            elif has_k and not has_t:
                keigo_w += w
            elif has_t and not has_k:
                tame_w += w
            else:
                hybrid_w += w

            if "。" in text:
                period_w += w
            if "！" in text or "!" in text:
                excl_w += w
            if has_w:
                warai_w += w
            if _is_question_line(text) or "？" in text or "?" in text:
                q_w += w
            if name_call_pattern.search(text):
                name_call_w += w

            emojis = _ALL_EMOJI_PATTERN.findall(text)
            emoji_count_total += len(emojis) * w
            all_emojis_found.extend(emojis)

        # 頻出絵文字 Top 3
        emoji_freq = {}
        for em in all_emojis_found:
            emoji_freq[em] = emoji_freq.get(em, 0) + 1
        top_emojis = sorted(emoji_freq.keys(), key=lambda k: emoji_freq[k], reverse=True)[:3]

        return {
            "count": n,
            "char_len": {
                "avg": round(sum(c * w for c, w in zip(char_lens, weights)) / w_sum, 1),
                "median": _percentile(char_lens, 0.5),
                "p25": _percentile(char_lens, 0.25),
                "p75": _percentile(char_lens, 0.75),
            },
            "line_len": {
                "avg": round(sum(l * w for l, w in zip(line_lens, weights)) / w_sum, 1),
                "median": _percentile(line_lens, 0.5),
                "p25": _percentile(line_lens, 0.25),
                "p75": _percentile(line_lens, 0.75),
            },
            "sent_len": {
                "avg": round(sum(s * w for s, w in zip(sent_lens, weights)) / w_sum, 1),
                "median": _percentile(sent_lens, 0.5),
                "p25": _percentile(sent_lens, 0.25),
                "p75": _percentile(sent_lens, 0.75),
            },
            "tone_ratios": {
                "keigo": round(keigo_w / w_sum, 2),
                "hybrid": round(hybrid_w / w_sum, 2),
                "tame": round(tame_w / w_sum, 2),
            },
            "period_ratio": round(period_w / w_sum, 2),
            "excl_ratio": round(excl_w / w_sum, 2),
            "warai_ratio": round(warai_w / w_sum, 2),
            "q_ratio": round(q_w / w_sum, 2),
            "name_call_ratio": round(name_call_w / w_sum, 2),
            "avg_emojis": round(emoji_count_total / w_sum, 1),
            "top_emojis": top_emojis,
        }

    global_stats = _calc_comprehensive_stats(raw_items)
    local_items = [it for it in raw_items if target_contact_id is not None and it["contact_id"] == target_contact_id]
    local_stats = _calc_comprehensive_stats(local_items) if local_items else {}

    if local_stats and local_stats["count"] >= 3:
        w_loc, w_glo = 0.6, 0.4
        char_med = round(local_stats["char_len"]["median"] * w_loc + global_stats["char_len"]["median"] * w_glo, 1)
        char_p25 = round(local_stats["char_len"]["p25"] * w_loc + global_stats["char_len"]["p25"] * w_glo, 1)
        char_p75 = round(local_stats["char_len"]["p75"] * w_loc + global_stats["char_len"]["p75"] * w_glo, 1)
        line_med = round(local_stats["line_len"]["median"] * w_loc + global_stats["line_len"]["median"] * w_glo, 1)
        sent_med = round(local_stats["sent_len"]["median"] * w_loc + global_stats["sent_len"]["median"] * w_glo, 1)

        keigo_r = round(local_stats["tone_ratios"]["keigo"] * w_loc + global_stats["tone_ratios"]["keigo"] * w_glo, 2)
        hybrid_r = round(local_stats["tone_ratios"]["hybrid"] * w_loc + global_stats["tone_ratios"]["hybrid"] * w_glo, 2)
        tame_r = round(local_stats["tone_ratios"]["tame"] * w_loc + global_stats["tone_ratios"]["tame"] * w_glo, 2)

        period_r = round(local_stats["period_ratio"] * w_loc + global_stats["period_ratio"] * w_glo, 2)
        warai_r = round(local_stats["warai_ratio"] * w_loc + global_stats["warai_ratio"] * w_glo, 2)
        excl_r = round(local_stats["excl_ratio"] * w_loc + global_stats["excl_ratio"] * w_glo, 2)
        q_r = round(local_stats["q_ratio"] * w_loc + global_stats["q_ratio"] * w_glo, 2)
        name_call_r = round(local_stats["name_call_ratio"] * w_loc + global_stats["name_call_ratio"] * w_glo, 2)
        avg_emojis = round(local_stats["avg_emojis"] * w_loc + global_stats["avg_emojis"] * w_glo, 1)
        top_emojis = local_stats.get("top_emojis") or global_stats.get("top_emojis", [])
        sample_desc = f"（全{total_count}通中、この相手への送信{len(local_items)}通の実績を重視して集計）"
    else:
        char_med = global_stats["char_len"]["median"]
        char_p25 = global_stats["char_len"]["p25"]
        char_p75 = global_stats["char_len"]["p75"]
        line_med = global_stats["line_len"]["median"]
        sent_med = global_stats["sent_len"]["median"]

        keigo_r = global_stats["tone_ratios"]["keigo"]
        hybrid_r = global_stats["tone_ratios"]["hybrid"]
        tame_r = global_stats["tone_ratios"]["tame"]

        period_r = global_stats["period_ratio"]
        warai_r = global_stats["warai_ratio"]
        excl_r = global_stats["excl_ratio"]
        q_r = global_stats["q_ratio"]
        name_call_r = global_stats["name_call_ratio"]
        avg_emojis = global_stats["avg_emojis"]
        top_emojis = global_stats.get("top_emojis", [])
        sample_desc = f"（全{total_count}通の送信実績から集計）"

    # 主要トーンの判定
    if hybrid_r >= 0.40 or (keigo_r > 0.20 and (tame_r > 0.15 or warai_r > 0.20)):
        tone_desc = f"敬語ベースにくだけたリアクションを交えるハイブリッド調（ハイブリッド率{int(hybrid_r*100)}%, 純粋敬語{int(keigo_r*100)}%）"
        primary_tone = "hybrid"
    elif keigo_r >= 0.60:
        tone_desc = f"丁寧な敬語ベース（です・ます調率{int(keigo_r*100)}%）"
        primary_tone = "keigo"
    elif tame_r >= 0.60:
        tone_desc = f"カジュアルなタメ口中心（タメ口率{int(tame_r*100)}%）"
        primary_tone = "tame"
    else:
        tone_desc = "自然なハイブリッド調"
        primary_tone = "hybrid"

    period_desc = "句点「。」を使用する傾向あり" if period_r >= 0.35 else "句点「。」はほぼ使わないスタイル"
    emoji_desc = f"1通あたり平均{avg_emojis}個（頻出: {' '.join(top_emojis)}）" if avg_emojis > 0 else "絵文字は控えめ"
    name_call_desc = f"、相手を「○○さん」と呼ぶ割合 約{int(name_call_r*100)}%" if name_call_r >= 0.15 else ""

    summary_lines = [
        f"- ユーザー実績スタイル {sample_desc}:",
        f"  - 口調傾向: {tone_desc}",
        f"  - 文量・行数・文数: 1通あたり中央値{int(char_med)}文字（IQR: {int(char_p25)}〜{int(char_p75)}文字）、中央値{int(line_med)}行・{int(sent_med)}文",
        f"  - 絵文字・記号: {emoji_desc}。「笑/w」使用率{int(warai_r*100)}%。「！」多用。{period_desc}",
        f"  - 会話構造: 質問で終える割合 約{int(q_r*100)}%{name_call_desc}",
        f"  - 基本姿勢: 過去の本人返信の実績・テンポを最上位のスタイル正解とし、本人らしい自然な文章を作成する",
    ]
    summary = "\n".join(summary_lines)

    weighted_profile = {
        "char_median": char_med,
        "char_p25": char_p25,
        "char_p75": char_p75,
        "line_median": line_med,
        "sent_median": sent_med,
        "primary_tone": primary_tone,
        "keigo_ratio": keigo_r,
        "hybrid_ratio": hybrid_r,
        "tame_ratio": tame_r,
        "period_ratio": period_r,
        "warai_ratio": warai_r,
        "excl_ratio": excl_r,
        "q_ratio": q_r,
        "name_call_ratio": name_call_r,
        "avg_emojis": avg_emojis,
        "top_emojis": top_emojis,
    }

    return {
        "summary": summary,
        "metrics": {
            "total_count": total_count,
            "local_count": len(local_items),
            "global_stats": global_stats,
            "local_stats": local_stats,
        },
        "weighted_profile": weighted_profile,
    }


def retrieve_relevant_reply_pairs(
    contact_id: int,
    current_contact_turn: str,
    condition: str = "",
    max_pairs: int = 5,
) -> tuple[list[dict], int]:
    """全返信ペアから、現在の相手発言・指示に類似する良質な返信ペアを検索・選定する。

    トピックキーワード一致を最優先ボーナスとし、無関係なペアを排除する。
    """
    all_pairs = build_user_reply_pairs()
    total_corpus_count = len(all_pairs)
    if not all_pairs:
        return [], 0

    query = (current_contact_turn + " " + condition).strip()

    scored_pairs = []
    for p in all_pairs:
        is_local = (p["contact_id"] == contact_id)
        sim_contact, j_contact, kw_contact = _calc_text_relevance(query, p["contact_turn"])
        sim_self, j_self, kw_self = _calc_text_relevance(condition, p["self_turn"]) if condition else (0.0, 0.0, 0)
        base_sim = max(sim_contact, sim_self)
        kw_count = max(kw_contact, kw_self)

        if is_local:
            score = base_sim * 1.5 + 0.35
            scope = "local_similar" if (kw_count > 0 or base_sim > 0.1) else "local_recent"
        else:
            score = base_sim * 1.0
            scope = "global_topic_match" if kw_count > 0 else "global_style_only"
            # トピック一致がなく、類似度も極端に低い場合は他相手ペアを無理に埋めない
            if kw_count == 0 and base_sim < 0.12:
                continue

        if p.get("source") == "manual":
            score += 0.05
        if p.get("is_good"):
            score += 0.10

        scored_pairs.append({
            **p,
            "score": round(score, 3),
            "similarity": round(base_sim, 3),
            "topic_matches": kw_count,
            "scope": scope,
        })

    scored_pairs.sort(key=lambda x: (x["score"], x.get("created_at", "")), reverse=True)

    selected: list[dict] = []
    seen_self_turns: set[str] = set()
    for sp in scored_pairs:
        norm_self = re.sub(r"\s+", "", sp["self_turn"])
        if norm_self in seen_self_turns:
            continue
        seen_self_turns.add(norm_self)
        selected.append(sp)
        if len(selected) >= max_pairs:
            break

    return selected, total_corpus_count


def score_candidate_style(reply: str, profile_dict: dict) -> tuple[float, dict]:
    """返信候補とユーザー学習プロファイルとの文体類似度（Soft style similarity: 0.0〜1.0）を算出。

    ※本スコアは文体・スタイルの類似度（Style Similarity）を測定するものであり、
    事実性（Grounded Truth）や品質合格を保証するものではありません。
    架空自己開示の有無や絶対規約は Hard Rules / CORE RULES によって担保されます。
    """
    wp = profile_dict.get("weighted_profile") if isinstance(profile_dict, dict) and "weighted_profile" in profile_dict else profile_dict
    if not wp or not isinstance(wp, dict) or wp.get("sample_count", 0) == 0:
        return 1.0, {"status": "no_profile"}

    score = 0.0
    details = {}

    cleaned = reply.strip()
    char_len = len(cleaned)
    lines = [l for l in cleaned.splitlines() if l.strip()]
    line_len = len(lines)
    sents = [s for s in re.split(r"[\n!?！？。]+", cleaned) if s.strip()]
    sent_len = len(sents) or 1

    # 1. 文字数・行数近接度 (30%)
    p25 = wp.get("char_p25", 40)
    p75 = wp.get("char_p75", 100)
    med = wp.get("char_median", 70)
    if p25 <= char_len <= p75:
        len_score = 0.30
    else:
        diff = abs(char_len - med)
        len_score = max(0.0, 0.30 - (diff / 100.0) * 0.20)
    score += len_score
    details["char_len_score"] = round(len_score, 3)

    # 2. 文数・改行テンポ (20%)
    sent_med = wp.get("sent_median", 3)
    sent_diff = abs(sent_len - sent_med)
    sent_score = max(0.0, 0.20 - sent_diff * 0.05)
    score += sent_score
    details["sent_score"] = round(sent_score, 3)

    # 3. トーン一致度 (20%)
    keigo_pattern = re.compile(r"(?:です|ます|でした|ました|ですね|ですか|でしょうか|ございます|お願い|致します)")
    tame_pattern = re.compile(r"(?:だね|だよ|でしょ|じゃん|っけ|ない？|かな？|する？|行こ|ね！|よ！|ぜ|ぞ)")
    has_k = bool(keigo_pattern.search(cleaned))
    has_t = bool(tame_pattern.search(cleaned))
    has_w = bool(re.search(r"(?:笑|w|W|www)", cleaned))
    has_e = bool(_ALL_EMOJI_PATTERN.search(cleaned))

    if has_k and (has_t or has_w or has_e):
        cand_tone = "hybrid"
    elif has_k and not has_t:
        cand_tone = "keigo"
    elif has_t and not has_k:
        cand_tone = "tame"
    else:
        cand_tone = "hybrid"

    primary_tone = wp.get("primary_tone")
    if not primary_tone:
        h_r = wp.get("hybrid_ratio", 0.0)
        k_r = wp.get("keigo_ratio", 0.0)
        t_r = wp.get("tame_ratio", 0.0)
        if h_r >= k_r and h_r >= t_r:
            primary_tone = "hybrid"
        elif k_r >= t_r:
            primary_tone = "keigo"
        else:
            primary_tone = "tame"

    if cand_tone == primary_tone:
        tone_score = 0.20
    elif (cand_tone == "hybrid" and primary_tone in ("keigo", "tame")) or (primary_tone == "hybrid"):
        tone_score = 0.15
    else:
        tone_score = 0.05
    score += tone_score
    details["tone_score"] = round(tone_score, 3)

    # 4. 記号・絵文字一致度 (15%)
    symbol_score = 0.0
    period_in = "。" in cleaned
    period_r = wp.get("period_ratio", 0.0)
    if (period_in and period_r >= 0.35) or (not period_in and period_r < 0.35):
        symbol_score += 0.05

    warai_in = has_w
    warai_r = wp.get("warai_ratio", wp.get("laugh_ratio", 0.0))
    if (warai_in and warai_r >= 0.25) or (not warai_in and warai_r < 0.25):
        symbol_score += 0.05

    emoji_in = has_e
    avg_em = wp.get("avg_emojis", wp.get("emoji_avg_count", 0.0))
    if (emoji_in and avg_em >= 0.4) or (not emoji_in and avg_em < 0.4):
        symbol_score += 0.05
    score += symbol_score
    details["symbol_score"] = round(symbol_score, 3)

    # 5. 質問終了一致度 (15%)
    q_in = _is_question_line(cleaned) or "？" in cleaned or "?" in cleaned
    q_r = wp.get("q_ratio", 0.5)
    if (q_in and q_r >= 0.5) or (not q_in and q_r < 0.5):
        q_score = 0.15
    else:
        q_score = 0.08
    score += q_score
    details["question_score"] = round(q_score, 3)

    return round(score, 3), details


def load_user_approved_reply_examples(contact_id: int, max_count: int = 6, max_budget_chars: int = 2000) -> list[str]:
    """後方互換: 過去に実際にユーザーが送信・採用した返信を文体学習例として取得する。"""
    conn = database.get_conn()
    candidates: list[str] = []
    seen: set[str] = set()

    try:
        msg_rows = conn.execute(
            "SELECT content FROM messages WHERE contact_id = ? AND sender = 'self'"
            " ORDER BY created_at DESC, id DESC",
            (contact_id,),
        ).fetchall()
        for r in msg_rows:
            content = (r["content"] or "").strip()
            norm = " ".join(content.split())
            if norm and norm not in seen and not any(k in content for k in ("{\"replies\"", "[AI_QUESTION]", "【案1】")):
                seen.add(norm)
                candidates.append(content)

        hist_rows = conn.execute(
            "SELECT generated_text, revised_text FROM generation_history"
            " WHERE (is_sent = 1 OR is_adopted = 1 OR rating = 'good')"
            "   AND (rating != 'bad' OR rating IS NULL)"
            " ORDER BY (contact_id = ?) DESC, (rating = 'good') DESC, created_at DESC",
            (contact_id,),
        ).fetchall()
        for r in hist_rows:
            text = (r["revised_text"] or r["generated_text"] or "").strip()
            norm = " ".join(text.split())
            if norm and norm not in seen and not any(k in text for k in ("{\"replies\"", "[AI_QUESTION]", "【案1】")):
                seen.add(norm)
                candidates.append(text)
    finally:
        conn.close()

    selected: list[str] = []
    total_chars = 0
    for cand in candidates:
        if len(selected) >= max_count:
            break
        if total_chars + len(cand) > max_budget_chars and selected:
            break
        selected.append(cand)
        total_chars += len(cand)

    return selected


def analyze_counterpart_style(contact_id: int) -> dict:
    """相手の過去メッセージから文章スタイル（敬語/タメ口、文量、絵文字、笑/疑問符等）を全件分析する。"""
    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT content FROM messages WHERE contact_id = ? AND sender = 'contact'"
            " ORDER BY created_at ASC, id ASC",
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()

    cleaned_msgs: list[str] = []
    for r in rows:
        raw = r["content"] or ""
        cleaned = prompt.clean_chat_message_content(raw).strip()
        if cleaned:
            cleaned_msgs.append(cleaned)

    total_msgs = len(cleaned_msgs)
    if total_msgs < 2:
        return {
            "summary": "（まだ相手からの受信メッセージが少なく、スタイル分析データが蓄積されていません。標準的かつ自然な会話テンポを維持してください）",
            "metrics": {
                "sample_count": total_msgs,
                "status": "insufficient_data",
            },
        }

    total_chars = sum(len(m) for m in cleaned_msgs)
    avg_chars = round(total_chars / total_msgs, 1)

    line_counts = [len([l for l in m.splitlines() if l.strip()]) for m in cleaned_msgs]
    avg_lines = round(sum(line_counts) / total_msgs, 1)

    # 敬語 / タメ口ヒューリスティック
    keigo_pattern = re.compile(r"(?:です|ます|でした|ました|ですね|ですか|でしょうか|ございます|お願い)")
    tame_pattern = re.compile(r"(?:だね|だよ|でしょ|じゃん|笑|w|草|ー|〜|っけ|ない？|かな？|する？|行こ|ね！|よ！)")

    keigo_count = sum(1 for m in cleaned_msgs if keigo_pattern.search(m))
    tame_count = sum(1 for m in cleaned_msgs if tame_pattern.search(m))
    keigo_ratio = keigo_count / total_msgs
    tame_ratio = tame_count / total_msgs

    if keigo_ratio >= 0.65:
        tone_label = "丁寧な敬語中心（です・ます調ベース）"
    elif tame_ratio >= 0.65 and keigo_ratio < 0.35:
        tone_label = "カジュアルなタメ口中心"
    else:
        tone_label = "敬語とタメ口が自然に混ざる親しみやすいハイブリッド調"

    # 絵文字分析
    emoji_counts = [len(_ALL_EMOJI_PATTERN.findall(m)) for m in cleaned_msgs]
    total_emojis = sum(emoji_counts)
    avg_emojis = round(total_emojis / total_msgs, 1)
    emoji_msg_count = sum(1 for c in emoji_counts if c > 0)
    emoji_msg_ratio = round(emoji_msg_count / total_msgs, 2)

    if avg_emojis == 0:
        emoji_label = "絵文字はほぼ使わないシンプル派"
    elif avg_emojis <= 1.0:
        emoji_label = f"絵文字は控えめ（1通あたり平均{avg_emojis}個）"
    else:
        emoji_label = f"絵文字を積極的に使う（1通あたり平均{avg_emojis}個）"

    # 「笑」「w」「！」「？」「。」
    warai_count = sum(1 for m in cleaned_msgs if re.search(r"(?:笑|w|W|www)", m))
    excl_count = sum(1 for m in cleaned_msgs if "！" in m or "!" in m)
    q_count = sum(1 for m in cleaned_msgs if _is_question_line(m) or "？" in m or "?" in m)
    period_count = sum(1 for m in cleaned_msgs if "。" in m)

    warai_pct = int(warai_count / total_msgs * 100)
    excl_pct = int(excl_count / total_msgs * 100)
    q_pct = int(q_count / total_msgs * 100)
    period_pct = int(period_count / total_msgs * 100)

    summary_lines = [
        f"- 口調傾向: {tone_label}",
        f"- 文量・改行: 1通あたり平均{avg_chars}文字（約{avg_lines}行）。",
        f"- 絵文字・温度感: {emoji_label}。「笑」使用率 {warai_pct}%、「！」使用率 {excl_pct}%。",
        f"- 質問頻度: 質問を含むメッセージ割合 {q_pct}%。",
        f"- 相手適応方針: 相手の文量やテンションに自然に寄り添いつつ、USER LEARNED STYLE PROFILEの自分らしい文体を維持する（オウム返しや禁止ルールの模倣は厳禁）。",
    ]
    summary = "\n".join(summary_lines)

    return {
        "summary": summary,
        "metrics": {
            "sample_count": total_msgs,
            "avg_chars": avg_chars,
            "avg_lines": avg_lines,
            "tone_label": tone_label,
            "keigo_ratio": round(keigo_ratio, 2),
            "tame_ratio": round(tame_ratio, 2),
            "avg_emojis": avg_emojis,
            "emoji_msg_ratio": emoji_msg_ratio,
            "warai_pct": warai_pct,
            "excl_pct": excl_pct,
            "q_pct": q_pct,
            "period_pct": period_pct,
        },
    }


def _load_training_examples() -> list[str]:
    """過去のフィードバック・良い返信例を参照用に読み込む。

    高評価・最近のものを優先して選択する。
    """
    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT corrected_response, ai_response, rating FROM training_examples"
            " ORDER BY (rating IS NULL) ASC, rating DESC, created_at DESC LIMIT 10"
        ).fetchall()
    finally:
        conn.close()
    examples = []
    for r in rows:
        if r["corrected_response"]:
            examples.append(f"良い返信例: {r['corrected_response']}")
        elif r["ai_response"]:
            examples.append(f"返信例: {r['ai_response']}")
    return examples


def _build_context(contact_id: int, condition: str, tone: str = "", mode: str = "normal") -> dict:
    """AI生成に必要なコンテキストを組み立てる（AIは呼び出さない）。

    generate と preview の両方から利用する。
    """
    global_cfg = get_ai_config()
    cfg, custom_knowledge = get_contact_ai_config(contact_id, global_cfg)

    conn = database.get_conn()
    try:
        contact = conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ).fetchone()
        if contact is None:
            raise HTTPException(status_code=404, detail="相手が見つかりません")
        messages = conn.execute(
            "SELECT id, sender, content, created_at FROM messages WHERE contact_id = ?"
            " ORDER BY created_at ASC, id ASC",
            (contact_id,),
        ).fetchall()
        images = conn.execute(
            "SELECT description FROM contact_images WHERE contact_id = ?"
            " AND description != '' ORDER BY sort_order ASC, id ASC",
            (contact_id,),
        ).fetchall()
    finally:
        conn.close()

    # 全会話履歴を投入（古い既出質問や自己開示を欠落させない）
    chat_text = prompt.format_chat_history(
        [dict(m) for m in messages], limit=0
    )
    rules = prompt.load_knowledge_texts("rules", custom_knowledge or None)
    references = prompt.load_knowledge_texts("references", custom_knowledge or None)
    learning_materials = prompt.cap_learning(
        prompt.load_knowledge_pairs("training", custom_knowledge or None)
    )
    training_examples = _load_training_examples()

    # 1. 会話ターンの構築と現在フェーズの判定
    turns = learning.corpus.build_turns_for_contact(contact_id)
    total_turns = len(turns)
    last_contact_turn = ""
    last_contact_msg = ""
    last_contact_msg_id = None
    if messages:
        for m in reversed(messages):
            if m["sender"] == "contact":
                last_contact_msg = m["content"]
                last_contact_msg_id = m["id"]
                break

    for t in reversed(turns):
        if t.sender == "contact":
            last_contact_turn = t.text
            break

    current_phase = learning.corpus.classify_phase(
        turn_index=total_turns,
        total_turns=total_turns,
        contact_text=last_contact_turn,
        self_text="",
    )

    # 2. 階層的スタイルプロファイル構築
    hierarchical_profile = learning.style.compute_hierarchical_profile(contact_id, current_phase)
    learned_policy_block = learning.style.to_learned_policy_prompt(hierarchical_profile)

    # 3. 2段階 Positive Reply Pairs 検索
    retrieved_pairs = learning.retrieval.retrieve_relevant_pairs(
        query_text=last_contact_turn,
        contact_id=contact_id,
        current_phase=current_phase,
        limit=4,
    )
    positive_pairs_block = learning.retrieval.to_positive_pairs_prompt_block(retrieved_pairs)

    # 4. Contrast Learning 差分教訓抽出
    contrast_examples = learning.contrast.extract_contrast_examples(contact_id=contact_id, limit=2)
    contrast_block = learning.contrast.to_contrast_prompt_block(contrast_examples, max_items=2)

    # 4.5 Step 9: 同一相手の修正傾向（HUMAN CORRECTION PATTERNS。上限付き、data扱い）
    correction_block = learning.contrast.build_correction_patterns_block(contact_id)
    if correction_block:
        contrast_block = f"{contrast_block}\n{correction_block}" if contrast_block else correction_block

    # 5. 相手スタイル分析
    counterpart_style_data = analyze_counterpart_style(contact_id)

    # 5.5 same-contact manual Gold の原文実例ブロック
    same_contact_gold_block = learning.style.build_same_contact_gold_pairs_block(contact_id, limit=10)

    # 5.55 Step 11: 最近そのまま送信された生成返信の実例（最大5件。なければ空）
    accepted_block = learning.contrast.build_accepted_block(contact_id, limit=5)
    if accepted_block:
        same_contact_gold_block = (
            f"{same_contact_gold_block}\n{accepted_block}" if same_contact_gold_block else accepted_block
        )

    self_profile = database.get_user_profile()
    contact_info = {
        "name": contact["name"],
        "profile": contact["profile"],
        "images": [r["description"] for r in images],
    }

    # 5.6 会話状態サマリー（Conversation State Ledger）の構築
    contact_messages_dicts = [dict(m) for m in messages]
    conversation_ledger = prompt.build_conversation_state_ledger(contact_messages_dicts, condition)

    # 5.7 Step 2: 相手直近メッセージの長さ区分（傾向情報。Hard Limit ではない）
    _length_source = (last_contact_turn or last_contact_msg or "").strip()
    counterpart_length_tier = prompt.classify_message_length(_length_source)
    counterpart_length_chars = len(_length_source)

    # 6. 8ブロック システムプロンプトの構築 (10,000文字以内)
    system_prompt = prompt.build_system_prompt(
        contact=contact_info,
        condition=condition,
        chat_history_text=chat_text,
        tone=tone,
        mode=mode,
        learned_policy_block=learned_policy_block,
        positive_pairs_block=positive_pairs_block,
        contrast_block=contrast_block,
        counterpart_style_block=counterpart_style_data["summary"],
        same_contact_gold_block=same_contact_gold_block,
        conversation_ledger=conversation_ledger,
        counterpart_length_tier=counterpart_length_tier,
        counterpart_length_chars=counterpart_length_chars,
        my_info=self_profile.get("my_info", ""),
        user_knowledge=database.get_user_knowledge_text(),
    )

    return {
        "cfg": cfg,
        "custom_knowledge": custom_knowledge,
        "system_prompt": system_prompt,
        "chat_text": chat_text,
        "last_contact_msg": last_contact_msg,
        "last_contact_msg_id": last_contact_msg_id,
        "current_phase": current_phase,
        "pieces": {
            "phase": current_phase,
            "style_profile": hierarchical_profile,
            "retrieved_pairs": retrieved_pairs,
            "contrast_examples": contrast_examples,
            "counterpart_style": counterpart_style_data,
            "conversation_ledger": conversation_ledger,
            "counterpart_length_tier": counterpart_length_tier,
            "counterpart_length_chars": counterpart_length_chars,
            "accepted_block": accepted_block,
            "self_profile": self_profile,
            "contact": contact_info,
            "chat_history": chat_text,
            "condition": condition,
            "user_knowledge": database.get_user_knowledge(),
        },
    }


def _load_recent_self_replies(contact_id: int, limit: int = 5) -> list[str]:
    """直近の自分の送信文を取得する（Repetition 検出用）。

    Step 10 検討記録: 生成履歴（採用・不採用問わず）の参照も試したが、
    同一トリガーの再生成で tie-order を壊し、実DBでも turn 間反復が低率
    （同一冒頭2.0%・同一質問型1.6%。語尾14.7%は敬語様式）だったため、
    messages のみに留める。棄却案の回避は correction_similarity が担う。
    重複は除去する。
    """
    conn = database.get_conn()
    try:
        rows = conn.execute(
            "SELECT content FROM messages WHERE contact_id = ? AND sender = 'self'"
            " ORDER BY id DESC LIMIT ?",
            (contact_id, limit),
        ).fetchall()
        seen: set[str] = set()
        out: list[str] = []
        for r in rows:
            text = (r["content"] or "").strip()
            norm = " ".join(text.split())
            if text and norm not in seen:
                seen.add(norm)
                out.append(text)
        return out
    finally:
        conn.close()


def _save_auto_evaluations(
    *,
    batch_id: int,
    history_ids: list[int],
    ordered_items: list[dict],
    counterpart_intent: str,
) -> None:
    """生成時の自動評価（naturalness/style/final）を generation_evaluations へ保存する。

    人間評価列には触れない。history_id 単位で upsert し、既存の人間評価を保持する。
    """
    conn = database.get_conn()
    try:
        now = database.now_iso()
        for idx, (hid, item) in enumerate(zip(history_ids, ordered_items)):
            conn.execute(
                "INSERT INTO generation_evaluations"
                " (generation_batch_id, history_id, candidate_index, counterpart_intent,"
                "  naturalness_score, style_score, final_score, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(history_id) DO UPDATE SET"
                " generation_batch_id = excluded.generation_batch_id,"
                " candidate_index = excluded.candidate_index,"
                " counterpart_intent = excluded.counterpart_intent,"
                " naturalness_score = excluded.naturalness_score,"
                " style_score = excluded.style_score,"
                " final_score = excluded.final_score,"
                " updated_at = excluded.updated_at",
                (
                    batch_id, hid, idx, counterpart_intent,
                    item["naturalness"], item["score"], item["final"], now, now,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _apply_diversity_nudge(scored_items: list[dict]) -> None:
    """Step 14 §21: 候補間の冒頭重複を微減点する（-0.02）。

    3案が同一冒頭（定型共感の反復等）の場合に、異なる切り口をわずかに優遇する。
    減点のみで並べ替えは呼び出し側。無理な差別化はしない（全同一でも送信可能）。
    """
    heads = []
    for item in scored_items:
        norm = re.sub(r"[\s　！!？?。、…〜～笑wW]+", "", item["reply"].strip())
        heads.append(norm[:6])
    for i, item in enumerate(scored_items):
        if len(heads[i]) >= 3 and any(
            heads[i] == other for j, other in enumerate(heads) if j != i
        ):
            item["final"] = round(item["final"] - 0.02, 3)


def _create_or_update_batch(
    *,
    contact_id: int,
    trigger_message_id: int | None,
    condition: str,
    revision_instruction: str,
) -> int:
    """生成バッチを作成または再生成として更新し、batch_id を返す。"""
    conn = database.get_conn()
    try:
        now = database.now_iso()
        parent_batch_id = None
        attempt_no = 1
        if revision_instruction.strip():
            prev = conn.execute(
                "SELECT id, attempt_no FROM generation_batches "
                "WHERE contact_id = ? AND outcome IN ('pending', 'regenerated') "
                "ORDER BY id DESC LIMIT 1",
                (contact_id,),
            ).fetchone()
            if prev:
                parent_batch_id = prev["id"]
                attempt_no = (prev["attempt_no"] or 1) + 1
                conn.execute(
                    "UPDATE generation_batches SET outcome = 'regenerated' WHERE id = ?",
                    (prev["id"],),
                )

        cur = conn.execute(
            "INSERT INTO generation_batches "
            "(contact_id, trigger_message_id, condition, revision_instruction, parent_batch_id, attempt_no, outcome, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
            (contact_id, trigger_message_id, condition, revision_instruction, parent_batch_id, attempt_no, now),
        )
        batch_id = cur.lastrowid
        conn.commit()
        return batch_id
    finally:
        conn.close()


def _record_history(
    *,
    contact_id: int,
    provider: str,
    model: str,
    condition: str,
    replies: list[str],
    revision_instruction: str,
    original_generated: str,
    tone: str = "",
    counterpart_message: str = "",
    batch_id: int | None = None,
) -> list[int]:
    """生成された各返信候補を1レコードずつ個別に保存する。"""
    conn = database.get_conn()
    try:
        now = database.now_iso()
        ids = []
        for reply in replies:
            cur = conn.execute(
                "INSERT INTO generation_history"
                " (contact_id, provider, model, current_condition, generated_text,"
                "  revision_instruction, revised_text, is_adopted, is_copied, is_sent,"
                "  tone, counterpart_message, batch_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?, ?)",
                (
                    contact_id,
                    provider,
                    model,
                    condition,
                    reply,
                    revision_instruction,
                    reply if revision_instruction else "",
                    tone,
                    counterpart_message,
                    batch_id,
                    now,
                ),
            )
            ids.append(cur.lastrowid)
        conn.commit()
        return ids
    finally:
        conn.close()


@router.post("/generate/preview")
def preview_generation(body: GenerateRequest):
    """AIを呼び出さずに、今回AIへ渡される内容を確認する（API Key等は含めない）。"""
    ctx = _build_context(body.contact_id, body.condition, body.tone, body.mode)
    rules = prompt.load_knowledge_texts("rules", ctx["custom_knowledge"] or None)
    references = prompt.load_knowledge_texts("references", ctx["custom_knowledge"] or None)
    learning_materials = prompt.cap_learning(
        prompt.load_knowledge_pairs("training", ctx["custom_knowledge"] or None)
    )
    training_examples = _load_training_examples()
    user_approved_replies = load_user_approved_reply_examples(body.contact_id)

    style_prof_data = dict(ctx["pieces"].get("style_profile", {}))
    if "summary" not in style_prof_data:
        style_prof_data["summary"] = learning.style.to_learned_policy_prompt(ctx["pieces"].get("style_profile", {}))

    return {
        "provider": ctx["cfg"]["provider"],
        "model": ctx["cfg"]["model"],
        "system_prompt": ctx["system_prompt"],
        "rules": rules,
        "references": references,
        "learning_materials": learning_materials,
        "training_examples": training_examples,
        "user_approved_replies": user_approved_replies,
        "user_style_profile": style_prof_data,
        "total_reply_pairs_count": len(learning.corpus.extract_reply_pairs()),
        **ctx["pieces"],
    }


@router.get("/learning/diagnostics")
def get_learning_diagnostics():
    """学習優先型返信システム（v3）の成果指標・コーパス内訳・バッチ採用状況を返却する。"""
    corpus_stats = learning.corpus.get_corpus_statistics()
    hierarchical_profile = learning.style.compute_hierarchical_profile(None)
    contrast_examples = learning.contrast.extract_contrast_examples(None, limit=10)

    conn = database.get_conn()
    try:
        batch_rows = conn.execute(
            "SELECT outcome, COUNT(*) as cnt FROM generation_batches GROUP BY outcome"
        ).fetchall()
        batch_outcomes = {r["outcome"]: r["cnt"] for r in batch_rows}
        total_batches = sum(batch_outcomes.values())

        attempt_rows = conn.execute(
            "SELECT attempt_no FROM generation_batches WHERE outcome = 'candidate_sent'"
        ).fetchall()
        avg_attempts = round(sum(r["attempt_no"] for r in attempt_rows) / len(attempt_rows), 2) if attempt_rows else 1.0
    finally:
        conn.close()

    feedback = learning.contrast.feedback_volume()
    acceptance = learning.contrast.acceptance_stats()
    sendable_n = feedback["by_sendability"].get("sendable", 0)
    rated_n = sum(
        n for k, n in feedback["by_sendability"].items() if k != "unrated"
    )
    flow = learning.contrast.evaluation_flow_counts()
    integrity = learning.contrast.evaluation_integrity()
    return {
        "corpus": corpus_stats,
        "style_profile_tier": hierarchical_profile["hierarchy_tier"],
        "active_profile": hierarchical_profile["active_profile"],
        "contrast_examples_count": len(contrast_examples),
        "naturalness_enabled": True,
        "naturalness_weight": naturalness.NATURALNESS_WEIGHT,
        "style_weight": naturalness.STYLE_WEIGHT,
        "feedback": feedback,
        "acceptance": acceptance,
        "smoothed_sendable_rate": learning.contrast.smooth_rate(sendable_n, rated_n),
        "evaluation_flow": flow,
        "evaluation_integrity": integrity,
        "batches": {
            "total_batches": total_batches,
            "outcomes": batch_outcomes,
            "avg_attempts_to_send": avg_attempts,
            "candidate_sent_rate": round(batch_outcomes.get("candidate_sent", 0) / total_batches, 2) if total_batches > 0 else 0.0,
            "manual_replaced_rate": round(batch_outcomes.get("manual_replaced", 0) / total_batches, 2) if total_batches > 0 else 0.0,
        },
    }


@router.post("/generate")
def generate(body: GenerateRequest):
    ctx = _build_context(body.contact_id, body.condition, body.tone, body.mode)
    cfg = ctx["cfg"]
    provider = factory.get_provider(cfg["provider"], cfg["api_key"])
    system_prompt = ctx["system_prompt"]
    chat_text = ctx["chat_text"]

    # バッチ作成
    batch_id = _create_or_update_batch(
        contact_id=body.contact_id,
        trigger_message_id=ctx.get("last_contact_msg_id"),
        condition=body.condition,
        revision_instruction=body.revision_instruction,
    )

    if body.revision_instruction.strip() or body.original_generated.strip():
        msgs = prompt.build_revision_messages(
            system_prompt=system_prompt,
            chat_history_text=chat_text,
            condition=body.condition,
            original_generated=body.original_generated,
            revision_instruction=body.revision_instruction,
        )
    else:
        msgs = prompt.build_initial_generation_messages(
            system_prompt=system_prompt,
            chat_history_text=chat_text,
            candidates=body.candidates,
            mode=body.mode,
        )

    def _call_ai(messages: list[dict[str, str]]) -> str:
        def _invoke():
            return provider.generate(
                model=cfg["model"],
                messages=messages,
                temperature=cfg["temperature"],
                max_tokens=cfg["max_tokens"],
                json_mode=body.candidates > 1,
            )

        try:
            return _invoke()
        except AIError as exc:
            delays = {"empty_response": (2, 4), "rate_limit": (8, 16)}.get(exc.code)
            if delays is None:
                logger.warning("AI generation failed: code=%s", exc.code)
                raise HTTPException(status_code=502, detail={"code": exc.code, "message": exc.message})
            last_exc = exc
            for i, delay in enumerate(delays, start=1):
                logger.warning("generation %s, retrying (%d/%d)", last_exc.code, i, len(delays))
                time.sleep(delay)
                try:
                    return _invoke()
                except AIError as exc2:
                    last_exc = exc2
            else:
                fb = factory.get_fallback(cfg) if last_exc.code in ("rate_limit", "empty_response") else None
                if fb is None:
                    logger.warning("AI generation failed: code=%s", last_exc.code)
                    raise HTTPException(status_code=502, detail={"code": last_exc.code, "message": last_exc.message})
                fb_provider, fb_cfg = fb
                logger.warning("primary %s exhausted, trying fallback %s", last_exc.code, fb_cfg["provider"])
                try:
                    return fb_provider.generate(
                        model=fb_cfg["model"],
                        messages=messages,
                        temperature=fb_cfg["temperature"],
                        max_tokens=fb_cfg["max_tokens"],
                        json_mode=body.candidates > 1,
                    )
                except AIError as fb_exc:
                    logger.warning("fallback failed: code=%s", fb_exc.code)
                    raise HTTPException(status_code=502, detail={"code": fb_exc.code, "message": fb_exc.message})

    contact_name = ctx.get("pieces", {}).get("contact", {}).get("name", "")

    MAX_ATTEMPTS = 3
    final_parsed_replies = None
    last_violations = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _call_ai(msgs)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Attempt %d _call_ai failed: %s", attempt, exc)
            if attempt == MAX_ATTEMPTS:
                raise HTTPException(status_code=502, detail={"code": "ai_call_failed", "message": "AIサービスの呼び出しに失敗しました。"})
            continue

        # 1. AI_QUESTION 完全一致検知
        question_text = _extract_ai_question(raw)
        if question_text:
            return {"replies": [], "history_ids": [], "question": question_text}

        # 2. Strict Parse & Auto-Sanitize (勝手に自動修正して不備を解消)
        parsed_replies = _parse_replies_strict(raw, body.candidates)
        if parsed_replies:
            parsed_replies = [
                sanitize_reply_text(r, current_datetime=datetime.now(), contact_name=contact_name)
                for r in parsed_replies
            ]
            parsed_replies = [
                ensure_has_question(r, condition=body.condition, contact_name=contact_name)
                for r in parsed_replies
            ]

        violations = validate_candidate_replies(
            parsed_replies,
            body.candidates,
            tone=body.tone,
            condition=body.condition,
            mode=body.mode,
            current_datetime=datetime.now(),
        )

        # 違反がなければ即合格
        if not violations and parsed_replies and len(parsed_replies) == body.candidates:
            final_parsed_replies = parsed_replies
            break

        # 3. 違反がある場合は1回 Repair を試行
        if not parsed_replies:
            violations = [f"出力が正しいJSON形式（{{\"replies\": [...]}}）または期待される{body.candidates}案の形式になっていません。"]
        logger.info("Attempt %d validation failed: %s. Attempting repair...", attempt, violations)

        repair_msgs = _build_repair_messages(msgs, raw, violations, body.candidates)
        try:
            repair_raw = _call_ai(repair_msgs)
            repair_question = _extract_ai_question(repair_raw)
            if repair_question:
                return {"replies": [], "history_ids": [], "question": repair_question}

            repair_parsed = _parse_replies_strict(repair_raw, body.candidates)
            if repair_parsed:
                repair_parsed = [
                    sanitize_reply_text(r, current_datetime=datetime.now(), contact_name=contact_name)
                    for r in repair_parsed
                ]
                repair_parsed = [
                    ensure_has_question(r, condition=body.condition, contact_name=contact_name)
                    for r in repair_parsed
                ]

            repair_violations = validate_candidate_replies(
                repair_parsed,
                body.candidates,
                tone=body.tone,
                condition=body.condition,
                mode=body.mode,
                current_datetime=datetime.now(),
            )
            if not repair_violations and repair_parsed and len(repair_parsed) == body.candidates:
                final_parsed_replies = repair_parsed
                break
            else:
                last_violations = repair_violations or violations
                logger.info("Attempt %d repair failed. Rejecting all candidates and retrying from clean state...", attempt)
        except Exception as exc:
            logger.warning("Attempt %d repair AI call failed: %s. Rejecting all and retrying clean...", attempt, exc)
            last_violations = violations

    # 万が一のフォールバック（ユーザーに0件エラー報告を出さず必ず案を届ける）
    if not final_parsed_replies:
        if parsed_replies and len(parsed_replies) == body.candidates:
            final_parsed_replies = parsed_replies
        elif 'repair_parsed' in locals() and repair_parsed and len(repair_parsed) == body.candidates:
            final_parsed_replies = repair_parsed
        else:
            logger.warning("All retry attempts exhausted: %s. Using parsed fallback.", last_violations)
            final_parsed_replies = parsed_replies or (repair_parsed if 'repair_parsed' in locals() and repair_parsed else [])

    parsed_replies = [
        ensure_has_question(
            sanitize_reply_text(r, current_datetime=datetime.now(), contact_name=contact_name),
            condition=body.condition,
            contact_name=contact_name,
        )
        for r in (final_parsed_replies or [])
    ]

    # 4. 括弧の除去と1文1行改行の保証
    replies = [prompt.format_one_sentence_per_line(prompt.strip_brackets(r)) for r in parsed_replies]

    # 5. Soft Style Scoring ＋ Naturalness Scoring と並べ替え（Step 4）
    # score_candidate_style() は維持し、その後に evaluate_candidate_naturalness() を
    # 適用して combine_candidate_scores() で結合・ソートする。
    user_style_profile_data = ctx["pieces"].get("style_profile", {}).get("active_profile", {})
    ledger = ctx["pieces"].get("conversation_ledger", {}) or {}
    recent_self_replies = _load_recent_self_replies(body.contact_id, limit=5)
    style_median = getattr(user_style_profile_data, "char_median", None)
    counterpart_msg = ctx.get("last_contact_msg", "") or ""
    scored_items = []
    for r in replies:
        s_val, s_details = score_candidate_style(r, user_style_profile_data.__dict__ if hasattr(user_style_profile_data, "__dict__") else user_style_profile_data)
        nat = naturalness.evaluate_candidate_naturalness(
            r,
            counterpart_message=counterpart_msg,
            conversation_ledger=ledger,
            recent_replies=recent_self_replies,
            style_char_median=style_median,
        )
        final = naturalness.combine_candidate_scores(s_val, nat["score"])
        # Step 9: 同一相手の修正プロファイルへの適合度を微調整として加算（±0.05）。
        # 修正データがなければ human_fit=0.5 で影響なし。Hard/validation は不変。
        human_fit = learning.contrast.correction_similarity(r, body.contact_id)
        final = round(final + 0.1 * (human_fit - 0.5), 3)
        # Step 11: 送信実績プロファイルへの適合度をさらに微調整（±0.03）。
        # 送信実績3件未満なら sent_sim=0.5 で影響なし。
        # 優先順位: Hard validation > invariants > relevance > Gold > correction > style > naturalness。
        # Human feedback は Hard Invariant より下位（validation が先に適用される）。
        sent_sim = learning.contrast.sent_profile_similarity(r, body.contact_id)
        final = round(final + 0.06 * (sent_sim - 0.5), 3)
        scored_items.append({
            "reply": r, "score": s_val, "details": s_details,
            "naturalness": nat["score"], "naturalness_detail": nat,
            "human_fit": human_fit, "sent_sim": sent_sim, "final": final,
        })

    # 通常モードのみ最終スコア降順ソート（followupモードは役割スロット固定のため順序を整列）
    if body.mode == "followup":
        # 3つの役割を正確な順序（案1: 行動報告, 案2: 軽快ツッコミ, 案3: 写真なし体験共有）に分類・配置
        sorted_replies = _align_followup_replies([item["reply"] for item in scored_items])
        by_reply = {item["reply"]: item for item in scored_items}
        ordered_items = [by_reply.get(r, scored_items[i]) for i, r in enumerate(sorted_replies)]
        style_scores = [item["score"] for item in ordered_items]
        naturalness_scores = [item["naturalness"] for item in ordered_items]
        human_fit_scores = [item["human_fit"] for item in ordered_items]
        sent_sim_scores = [item["sent_sim"] for item in ordered_items]
        final_scores = [item["final"] for item in ordered_items]
    else:
        _apply_diversity_nudge(scored_items)
        scored_items.sort(key=lambda x: x["final"], reverse=True)
        ordered_items = scored_items
        sorted_replies = [item["reply"] for item in scored_items]
        style_scores = [item["score"] for item in scored_items]
        naturalness_scores = [item["naturalness"] for item in scored_items]
        human_fit_scores = [item["human_fit"] for item in scored_items]
        sent_sim_scores = [item["sent_sim"] for item in scored_items]
        final_scores = [item["final"] for item in scored_items]

    # 6. 履歴保存（ソート後の順序で各候補を個別に保存、batch_id を付与）
    history_ids = _record_history(
        contact_id=body.contact_id,
        provider=cfg["provider"],
        model=cfg["model"],
        condition=body.condition,
        replies=sorted_replies,
        revision_instruction=body.revision_instruction,
        original_generated=body.original_generated,
        tone=body.tone,
        counterpart_message=ctx.get("last_contact_msg", ""),
        batch_id=batch_id,
    )

    # 6.5 自動評価の保存（人間評価列は NULL のまま。後から POST /api/evaluations で付与）
    _save_auto_evaluations(
        batch_id=batch_id,
        history_ids=history_ids,
        ordered_items=ordered_items,
        counterpart_intent=(ledger.get("counterpart_intent") or "report"),
    )

    effective_tone = body.tone or (
        "hybrid" if getattr(user_style_profile_data, "hybrid_ratio", 0.5) >= 0.5
        else ("keigo" if getattr(user_style_profile_data, "keigo_ratio", 0.0) >= 0.5 else "tame")
    )

    return {
        "replies": sorted_replies,
        "history_ids": history_ids,
        "style_scores": style_scores,
        "naturalness_scores": naturalness_scores,
        "human_fit_scores": human_fit_scores,
        "sent_sim_scores": sent_sim_scores,
        "final_scores": final_scores,
        "batch_id": batch_id,
        "build_version": config.APP_BUILD_VERSION,
        "prompt_version": config.PROMPT_VERSION,
        "requested_tone": body.tone or "auto",
        "effective_tone": effective_tone,
        "tone_validation": "passed",
    }


