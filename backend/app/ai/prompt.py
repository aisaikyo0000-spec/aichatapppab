"""AIへ渡すプロンプトの組み立て（Conversation-Learned Reply System v3.1）。

【ゼロベース再構築 & 会話状態Ledger + 直近会話最終配置】
1. ROLE & CURRENT DIRECTIVE / REPLY DIRECTIVE（最優先命令・トーンHard Lock）
2. HARD INVARIANTS / CORE RULES（事実整合・話者分離・架空自己開示禁止・さん付け・カタカナ語禁止・MULTI-TOPIC・本人基本情報）
3. CONVERSATION STATE LEDGER（既出質問・回答済み話題・既知事実の集約）
4. LEARNED USER RESPONSE POLICY & SAME-CONTACT GOLD EXAMPLES（同じ相手の直近手入力Gold原文最優先）
5. POSITIVE REPLY PAIRS / RETRIEVED USER REPLY PAIRS（検索されたGold/Silver実例）
6. RELEVANT CONTRAST（失敗案→手入力の差分教訓）
7. COUNTERPART STYLE ADAPTATION（相手直近発言・温度感適応）
8. RECENT CHAT TURNS & OUTPUT CONTRACT（直近6〜10ターン原文＋3案JSON契約）
"""
from __future__ import annotations

from datetime import datetime
import logging
from pathlib import Path
import re
from typing import Any

from ..database import get_conn

logger = logging.getLogger(__name__)


def _load_knowledge_rows(kind: str, file_ids: list[int] | None = None):
    """knowledgeファイルのDB行を返す（有効なファイルのみ）。"""
    conn = get_conn()
    try:
        if file_ids:
            ids = [i for i in file_ids if i]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"SELECT file_path FROM knowledge_files WHERE id IN ({placeholders})"
                " AND type = ?",
                (*ids, kind),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT file_path FROM knowledge_files WHERE type = ? AND enabled = 1",
                (kind,),
            ).fetchall()
    finally:
        conn.close()
    return rows


def _read_file(row) -> tuple[str, str] | None:
    """DB行から(ファイル名, 内容)を読む。失敗・空ならNone。"""
    path = Path(row["file_path"])
    try:
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("knowledge file read failed: %s (%s)", path.name, exc)
        return None
    if not text:
        return None
    return path.name, text


def load_knowledge_texts(kind: str, file_ids: list[int] | None = None) -> list[str]:
    """knowledgeファイルの内容を読み込む（練習モード/参考用）。"""
    texts: list[str] = []
    for row in _load_knowledge_rows(kind, file_ids):
        pair = _read_file(row)
        if pair:
            texts.append(pair[1])
    return texts


def load_knowledge_pairs(kind: str, file_ids: list[int] | None = None) -> list[tuple[str, str]]:
    """knowledgeファイルの(ファイル名, 内容)ペアを読み込む。"""
    pairs: list[tuple[str, str]] = []
    for row in _load_knowledge_rows(kind, file_ids):
        pair = _read_file(row)
        if pair:
            pairs.append(pair)
    return pairs


_LEARNING_PRIORITY = ("01", "02", "03", "04", "05", "06", "07", "08", "09", "10")
_LEARNING_BUDGET = 6000


def cap_learning(pairs: list[tuple[str, str]], budget: int = _LEARNING_BUDGET) -> list[str]:
    def order(pair: tuple[str, str]) -> int:
        name, _ = pair
        for i, prefix in enumerate(_LEARNING_PRIORITY):
            if name.startswith(prefix):
                return i
        return len(_LEARNING_PRIORITY)

    ordered = sorted(pairs, key=order)
    result: list[str] = []
    total = 0
    for _, text in ordered:
        if total + len(text) > budget:
            break
        result.append(text)
        total += len(text)
    return result


def strip_brackets(text: str) -> str:
    """生成文からかぎかっこ（「」『』【】）を強制除去する。"""
    text = re.sub(r"[「」『』【】]", "", text)
    return text.strip()


def clean_contact_profile(profile_text: str) -> str:
    """相手のプロフィールからアプリ画面UIゴミ行やOCRノイズを除去し、有効な自己紹介のみを抽出する。"""
    if not profile_text:
        return ""

    ignore_exact = {
        "さがす", "いいね", "トーク", "マイページ", "好みカード", "好みカードベスト",
        "メッセージを送る", "すべて見る", "詳細を見る", "基本情報", "自己紹介文",
        "登録済みの好みカード", "心理テスト", "あなたの登録傾向", "トークの傾向",
        "文字量", "返信ペース", "主な", "返信時間", "あなたとの共通点", "生活", "音楽",
        "趣味趣向", "旅行", "グルメ", "その他", "相性ぴったりのお相手です",
    }

    cleaned_lines: list[str] = []
    for raw_line in profile_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 数字のみ、時刻のみ、単一記号、パーセント行
        if re.fullmatch(r"\d+|\d+[:：]\d+|[%％]|[-ー—]+", line):
            continue
        if line in ignore_exact:
            continue
        if any(w in line for w in ["VIPオプション", "ロイヤルVIP", "登録傾向", "診断で相性", "デートタイプ診断"]):
            continue
        cleaned_lines.append(line)

    return "\n".join(cleaned_lines[:20]).strip()


def format_one_sentence_per_line(text: str) -> str:
    """1文ごとに自然に改行されたフォーマットを保証する。疑問符連続や笑、引用助詞等で文法を破壊しない。"""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # 文末記号の後で分割。ただし直後に記号連続（！？笑等）や助詞が続く場合は分割しない
        pattern = r"(?<=[。！？!?])(?!(?:[。！？!?笑wW～〜ー]|と|って|なんて|とか|そう|など|よ|ね|[」』）\)]))\s*"
        sub_sentences = [s.strip() for s in re.split(pattern, line) if s.strip()]

        # 記号破片（「笑」「？」「！」など）や短い助詞破片を前の行にマージ
        merged: list[str] = []
        for s in sub_sentences:
            if merged and (
                (len(s) <= 3 and s.startswith(("と", "って", "な", "よ", "ね", "笑", "？", "?", "！", "!")))
                or re.fullmatch(r"[笑wW！？!?～〜ー]+", s)
            ):
                merged[-1] += s
            else:
                merged.append(s)
        lines.extend(merged)
    return "\n".join(lines)


def clean_chat_message_content(content: str) -> str:
    """メッセージ本文から独立した時刻行（例: 22:58）のみを除去する。"""
    lines = content.splitlines()
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", stripped):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def format_chat_history(messages: list[dict[str, Any]], limit: int = 0) -> str:
    """メッセージ履歴をプロンプト用に整形する。"""
    if limit > 0:
        messages = messages[-limit:]
    if not messages:
        return ""

    span_prefix = ""
    first_time = messages[0].get("created_at") or ""
    last_time = messages[-1].get("created_at") or ""
    if first_time and last_time:
        try:
            d1 = datetime.fromisoformat(first_time.replace("Z", "+00:00"))
            d2 = datetime.fromisoformat(last_time.replace("Z", "+00:00"))
            days = (d2 - d1).days
            if days > 0:
                span_prefix = f"（この会話は{days}日間にわたる計{len(messages)}通のやり取り）\n"
        except Exception:
            pass

    lines = []
    for m in messages:
        sender = "相手" if m["sender"] == "contact" else "自分"
        content = clean_chat_message_content(m.get("content") or "")
        if content:
            lines.append(f"{sender}: {content}")
    return span_prefix + "\n\n".join(lines)


def format_chat_history_span(messages: list[dict[str, Any]]) -> str:
    """会話履歴全体の期間・件数サマリーを付与して整形する。"""
    return format_chat_history(messages)


def _format_profile(profile: dict[str, Any] | None) -> str:
    """プロフィール辞書をテキストに整形する。"""
    if not profile:
        return "(未設定)"
    lines = []
    labels = {
        "name": "名前",
        "gender": "性別",
        "age": "年齢",
        "occupation": "職業",
        "hobbies": "趣味",
        "personality": "性格",
        "speaking_style": "話し方",
        "profile": "自己紹介文",
        "my_info": "基本情報・事実メモ",
    }
    for k, label in labels.items():
        v = str(profile.get(k) or "").strip()
        if v:
            lines.append(f"- {label}: {v}")
    return "\n".join(lines) if lines else "(未設定)"


def build_conversation_state_ledger(messages: list[dict[str, Any]], condition: str = "") -> dict[str, Any]:
    """会話履歴から決定論的な状態サマリー（Conversation State Ledger）を生成する。"""
    if not messages:
        return {
            "known_contact_facts": [],
            "known_self_facts": [],
            "already_asked_questions": [],
            "already_answered_topics": [],
            "current_topic": "会話開始",
            "counterpart_intent": "report",
            "prev_self_ended_with_question": False,
            "unresolved_question": None,
            "last_contact_message": "",
            "latest_user_intent": condition.strip() or "自然な返信",
        }

    contact_msgs = [clean_chat_message_content(m.get("content") or "") for m in messages if m.get("sender") == "contact"]
    self_msgs = [clean_chat_message_content(m.get("content") or "") for m in messages if m.get("sender") == "self"]

    last_contact = contact_msgs[-1] if contact_msgs else ""

    # 相手の直近発言から未解決質問を抽出
    unresolved_q = None
    if contact_msgs:
        for line in last_contact.splitlines():
            line_s = line.strip()
            if "？" in line_s or "?" in line_s or line_s.endswith(("ですか", "ますか", "でしょうか")):
                unresolved_q = line_s
                break

    # 直近の話題（最新の相手メッセージを最優先）
    current_topic = last_contact[:60] if last_contact else (clean_chat_message_content(messages[-1].get("content") or "")[:60] if messages else "通常会話")

    # 過去に自分が相手に聞いた質問（重複排除）
    asked_qs: list[str] = []
    seen_qs: set[str] = set()
    for sm in self_msgs:
        for line in sm.splitlines():
            line_s = line.strip()
            if "？" in line_s or "?" in line_s or line_s.endswith(("ですか", "ますか", "でしょうか")):
                norm = re.sub(r"[\s\?？!！]+", "", line_s)
                if norm and norm not in seen_qs:
                    seen_qs.add(norm)
                    asked_qs.append(line_s)

    # Step 7: 直近の自分の返信が質問で終わっているか（連続質問の抑制用参考情報）
    prev_self_ended_with_question = False
    if self_msgs:
        last_lines = [ln.strip() for ln in self_msgs[-1].splitlines() if ln.strip()]
        if last_lines:
            last_line = last_lines[-1]
            prev_self_ended_with_question = bool(
                "？" in last_line or "?" in last_line
                or re.search(r"(?:ですか|ますか|でしょうか|だろうか|のかな|かな|かい|だっけ|っけ)\s*$", last_line)
            )

    return {
        "known_contact_facts": contact_msgs[-10:] if contact_msgs else [],
        "known_self_facts": self_msgs[-10:] if self_msgs else [],
        "already_asked_questions": asked_qs[-10:],
        "current_topic": current_topic,
        "counterpart_intent": classify_counterpart_intent(last_contact),
        "prev_self_ended_with_question": prev_self_ended_with_question,
        "unresolved_question": unresolved_q,
        "last_contact_message": last_contact,
        "latest_user_intent": condition.strip() or "自然な返信",
    }


def get_current_season_info(dt: datetime | None = None) -> tuple[str, str]:
    """現在日時から（月名・季節名, 季節の制約説明）を返す。"""
    if dt is None:
        dt = datetime.now()
    month = dt.month
    if month in (3, 4, 5):
        season_name = f"{month}月（春）"
        restriction = "（※『寒くなってきた』等の冬表現や『猛暑』等の夏表現の嘘は禁止）"
    elif month in (6, 7, 8):
        season_name = f"{month}月（夏）"
        restriction = "（※『寒くなってきた』等の冬表現の嘘は絶対禁止）"
    elif month in (9, 10, 11):
        season_name = f"{month}月（秋）"
        restriction = "（※『猛暑』等の真夏表現や『雪が降って』等の真冬表現の嘘は禁止）"
    else:
        season_name = f"{month}月（冬）"
        restriction = "（※『暑くなってきた』等の夏表現の嘘は禁止）"
    return season_name, restriction


def classify_message_length(text: str) -> str:
    """相手メッセージの長さ区分を返す（Step 2: 簡易的な長さ適応用）。

    - short: 0〜20文字（短い相づち・報告には短い返信を優先）
    - medium: 21〜80文字（2〜3行程度の自然な返信が基本）
    - long: 81文字以上（内容に合わせた丁寧な返信をしてよい）
    ※傾向の目安であり、Hard Limit ではない。
    """
    n = len((text or "").strip())
    if n <= 20:
        return "short"
    if n <= 80:
        return "medium"
    return "long"


# --- Step 3: 相手意図の決定論的分類（LLM 不使用の軽量ルール） ---
# 優先順位: question > invitation > answer_required > emotional_share > reaction > report
_INTENT_QUESTION_KEYWORDS = (
    "いつ", "なに", "何時", "何時", "誰", "どれ", "どっち", "どちら", "なぜ", "どうして",
)
# 「どこ」は「どこも」（anywhere・否定文）を除外して判定
_INTENT_QUESTION_DOKO = re.compile(r"どこ(?!も)")
_INTENT_QUESTION_ENDINGS = re.compile(
    r"(?:ですか|ますか|でしょうか|だろうか|のかな|かな|かい|だっけ|っけ|？|\?)\s*$"
)
_INTENT_QUESTION_PARTICLES = re.compile(r"(?:なに|何[がをにへ])")
_INTENT_INVITATION = re.compile(
    r"行こう|来なよ|来てよ|しようよ|しよう|一緒に|しませんか|行きませんか|来ませんか"
    r"|食べに行|飲みに行|遊びに行|観に行|見に行|今度.{0,10}(?:行|会|食|飲|遊)"
)
_INTENT_ANSWER_REQUIRED = re.compile(
    r"集合|待ち合わせ|待ってる|待ってます|空いてる|空いてます|来れる|来られます"
    r"|大丈夫そう|了解|教えて|おしえて"
)
_INTENT_EMOTIONAL = re.compile(
    r"しんど|つら|辛い|悲し|嫌な|嫌だっ|落ち込|最悪|凹|へこ|悩|怒られ|泣"
    r"|(?:最近|なんか|ずっと|毎日).{0,8}疲れ"
)
_INTENT_REACTION = re.compile(
    r"眠|疲れた|やば|楽しみ|嬉し|うれし|最高|笑|よかった|びっくり|すご|まじ|ウケる|草|おつかれ|お疲れ"
    r"|おかえり|ただいま|いってらっしゃい"
)


# Step 3: Intent ごとの返信方針（強い参考情報。Hard Rule ではない）
_INTENT_POLICIES = {
    "question": (
        "相手の質問への回答を最優先すること。回答を無視して別の話題へ移らないこと。"
        "回答だけで自然に成立するなら、無理に追加質問しないこと。"
        "回答の後に逆質問・確認質問を付け足さないこと（回答だけで終える）。"
    ),
    "invitation": (
        "相手の提案・誘いに対する自分の意思を優先して返信すること。"
        "架空の予定や意思を作らないこと。"
    ),
    "answer_required": (
        "相手が求めている回答・確認を優先すること。"
    ),
    "report": (
        "相手は単純に情報共有している。無理に質問して会話を延長しないこと。"
        "短いリアクションだけでもよい（質問することも許可する）。"
    ),
    "reaction": (
        "相手の感情・リアクションに自然に反応すること。質問は必要な場合だけにすること。"
    ),
    "emotional_share": (
        "まず相手の感情共有に自然に反応すること。質問攻めにしないこと。"
        "質問は相手が明確に助言・意見を求めている場合に限ること。"
        "相づち・共感・労いだけで終えることを優先すること。"
    ),
}


def classify_counterpart_intent(text: str) -> str:
    """相手の直近メッセージの意図を決定論的に分類する（Step 3）。

    返り値: question / invitation / answer_required / emotional_share / reaction / report。
    完璧な自然言語理解ではなく、返信方針を決めるための強い参考情報。
    医学的・心理学的診断は行わない（emotional_share は会話上の分類のみ）。
    """
    t = (text or "").strip()
    if not t:
        return "report"

    # 1. question: 疑問符・疑問語尾・疑問詞
    if "？" in t or "?" in t:
        return "question"
    if _INTENT_QUESTION_ENDINGS.search(t):
        return "question"
    if any(k in t for k in _INTENT_QUESTION_KEYWORDS):
        return "question"
    if _INTENT_QUESTION_DOKO.search(t):
        return "question"
    if _INTENT_QUESTION_PARTICLES.search(t):
        return "question"
    # 「どう」は「どうでも」「どうだって」を除外して判定
    if "どう" in t and "どうでも" not in t and "どうだって" not in t:
        return "question"

    # 2. invitation: 誘い・提案
    if _INTENT_INVITATION.search(t):
        return "invitation"

    # 3. answer_required: 疑問符なしで回答・確認を求める連絡
    if _INTENT_ANSWER_REQUIRED.search(t):
        return "answer_required"

    # 4. emotional_share: 感情・悩み・落ち込みの共有（「疲れた」単体は reaction 側で扱う）
    if _INTENT_EMOTIONAL.search(t):
        return "emotional_share"

    # 5. reaction: 感情・リアクション表現
    if _INTENT_REACTION.search(t):
        return "reaction"

    # 6. report: 上記いずれでもない情報共有（デフォルト）
    return "report"


def build_system_prompt(
    *,
    contact: dict[str, Any],
    condition: str = "",
    chat_history_text: str = "",
    tone: str = "",
    mode: str = "normal",
    current_datetime: datetime | None = None,
    learned_policy_block: str = "",
    positive_pairs_block: str = "",
    contrast_block: str = "",
    counterpart_style_block: str = "",
    my_info: str = "",
    user_knowledge: str = "",
    role: str = "self",
    conversation_ledger: dict[str, Any] | None = None,
    # 互換用引数
    rules: list[str] | None = None,
    references: list[str] | None = None,
    learning_materials: list[str] | None = None,
    training_examples: list[str] | None = None,
    self_profile: dict[str, Any] | None = None,
    user_style_profile: str = "",
    retrieved_reply_pairs: list[dict[str, Any]] | None = None,
    user_approved_replies: list[str] | None = None,
    counterpart_style: str = "",
    learned_preferences: str = "",
    same_contact_gold_block: str = "",
    same_contact_gold_samples: int = 0,
    same_contact_gold_length_median: int | None = None,
    counterpart_length_tier: str = "",
    counterpart_length_chars: int = 0,
    strategy_mode: str = "none",
    candidates: int = 3,
) -> str:
    """8ブロック構成のシステムプロンプトを組み立てる（Conversation-Learned Reply System v3.8）。"""
    raw_contact_name = (contact.get("name") or "").strip()
    contact_name = "" if raw_contact_name in {"相手", "相手さん", "未設定", "不明"} else raw_contact_name
    raw_profile = (contact.get("profile") or "").strip()
    contact_profile = clean_contact_profile(raw_profile)
    season_name, season_restriction = get_current_season_info(current_datetime)

    # Block 1: ROLE & CURRENT DIRECTIVE / REPLY DIRECTIVE
    directive_lines = [
        f"- 現在の月: {season_name}",
        f"  ※(季節外れの嘘の禁止) 季節や気候に触れる場合のみ、現在の時期（{season_name}）と矛盾する表現{season_restriction}をつかないこと（※季節や天気の話題を無理に出す必要は一切ありません。日常会話や趣味、相手との自然なやり取りを最優先してください）。",
        "- 本人の記号の好み: 「、」「。」は基本的に使わない。文意に合うときは「！」「？」や絵文字を使ってよく、何も付けずに終えてもよい。記号や絵文字を機械的に足さず、読みやすさと自然さを優先する。",
    ]
    if mode == "followup":
        directive_lines.append(
            "【追いメッセージ（再活性化・会話復活モード）】\n"
            "しばらく返信が途絶えている相手に、気まずさや返信のプレッシャーを強めず、自然に会話を再開できる追いメッセージを作成します。\n"
            "※(追いメッセージ・反復回避) 未返信の質問と同じ情報を求める質問や、その細分化・言い換えも避ける。自分の直近メッセージの要約・言い換えをしない。プロフィールや過去の共有情報を使うかどうかは任意で、使う場合は確認済みの情報に限る。使える話題が一つなら無理に別の話題を捏造せず短い反応を優先してください。\n"
            "※(質問の扱い) 会話を続けるためだけの質問や、返信負担になる質問は避ける。確認済みの話題に自然につながり、短く答えやすい質問は必要な場合に限ってよい。質問なしの短い反応も正当な選択肢。候補の違いを作るために質問を増やさず、質問の有無だけで候補を差別化しないこと。質問を禁じたり、別話題へ無理に移ったりしないこと。\n"
            "※(プロフィール情報の境界) プロフィールの好みから、所有・経験・具体的な選択傾向を推測しない。趣味に触れること自体も返信の必須条件ではありません。\n"
            "※(時系列と会話段階) 時刻・日付・曜日・相手の近況が履歴で確認できない限り、挨拶や状況を推測で足さない。初回の挨拶や関係上必要な場面以外で定型句を足さない。\n"
            "※(返信の余白) 返事を急かしたり、すぐ返すことを期待したりせず、相手が自分のペースで返せる自然な表現にすること。\n"
            "※(本人の文体・言葉遣いの完全再現) 本人が普段送っているリアルなチャット言葉・語尾・テンポを忠実に再現すること。\n"
            "※(絶対厳守・催促の完全禁止) 「返信まだ？」「忙しい？」「返事待ってます」「既読スルー」等の催促・問い詰め表現は全案で完全禁止。\n"
            "※プロフィールや過去の共有情報を使うかどうかは任意です。使う場合も、自然な反応につながるときだけ取り上げ、話題を使うこと自体を連絡の口実にしないでください。"
        )
    if condition.strip():
        directive_lines.append(f"- 返信希望・条件: {condition.strip()}")
        if "別話題" in condition:
            directive_lines.append("  ※(話題転換ルール) 直前の話題（乗馬や写真等）は再質問・深掘りせず、自然に新しい話題へ移行すること。架空の自己開示で話題転換してはならない。")
    elif mode != "followup":
        directive_lines.append("- 返信希望・条件: (指定なし)")

    # トーン Hard Lock 指示
    if tone == "tame":
        directive_lines.append(
            "- トーン指定: 【タメ口】全ての返信で自然な友達同士のカジュアルな口語（「それめっちゃ分かる笑」「楽しそう！」等）を使う。\n"
            "  ※(絶対厳守) 「です」「ます」「でした」「ました」「でしょう」「ません」「なんですね」「ありますか」「ですか」等の丁寧終止・敬語表現は全候補で完全禁止。"
        )
    elif tone == "keigo":
        directive_lines.append(
            "- トーン指定: 【敬語】全ての返信で敬語（です・ます調）をベースとする。"
            "ただし、ビジネスメールのような堅苦しい接客口調は禁止し、自然な会話敬語にする。"
        )
    elif tone == "hybrid":
        directive_lines.append(
            "- トーン指定: 【ハイブリッド】完全敬語でも完全タメ口でもない、基本は「です・ます調」を維持しながら、リアクション部分に親しみやすい表現を交える。"
        )

    directive_text = "\n".join(directive_lines)

    b1_role = (
        "あなたはマッチングアプリの会話を支援する文章作成アシスタントです。\n"
        "ユーザー（自分）の代わりに、自然で好印象な返信文章を"
        + (
            "文脈に合う3案作成します。候補の違いを作るために話題や反応を無理に変えず、同じ話題から自然に成立する複数案を作ってよいです。\n\n"
            if mode == "followup"
            else "異なる3つの会話展開で3案作成します。\n\n"
        )
        + "【ROLE & CURRENT DIRECTIVE】\n【REPLY DIRECTIVE】今回の返信における最優先命令（Priority 1）\n"
        f"{directive_text}\n"
        "※ユーザーの具体的指示がある場合は必ず最優先（Priority 1）で反映してください。\n"
        "指示を無視して一般的な「無難な返信」を生成してはならない。\n"
        "「自然な返信にする」ことを理由に、ユーザーの具体的な指示を変更・省略してはならない。\n"
        "自由記述に「質問しない」とあれば絶対に質問を入れないこと。"
    )

    # Block 2: HARD INVARIANTS / CORE RULES (不変の絶対規約)
    my_info_text = my_info.strip() or (self_profile.get("my_info", "") if self_profile else "") or "(未登録)"
    user_k_text = user_knowledge.strip() or "(未登録)"
    self_prof_text = _format_profile(self_profile) if self_profile else ""

    flow_rule = (
        "11. 追いメッセージの構成: 未返信の質問を繰り返したり、最後の会話を単に言い換えたりしないこと。未返信の質問と同じ情報を求める質問や、その細分化・言い換えも避けること。自分の直近メッセージの要約・言い換えもしない。プロフィールや過去に共有済みの確認できる情報を使うかどうかは任意であり、別の角度から触れてもよい。使える話題が一つなら、無理に別の話題を捏造して3案を水増ししないこと。同じ情報への短い反応も選択肢に含め、自然に異なる切り口があれば候補間で使い分けること。質問の有無だけで候補を差別化せず、同じ話題を質問表現だけ変えて複数案にしないこと。未確認の行動・体験や近況を作らず、長さは会話に合わせること。短い一文だけで自然に成立するなら、そのまま返してよい。\n"
        "12. 相手の状況を決めつけない: 未確認の近況や生活状況を推測して加えないこと。\n"
        if mode == "followup"
        else "11. 返信の長さ・構造の自由選択: 相手の発言と会話状況に応じて、以下から適切な形を選ぶこと。短いリアクション / 短い共感 / 回答だけ / リアクション＋一言 / リアクション＋質問 / 自己開示 / 自己開示＋質問 / 話題継続 / 話題終了 / 軽い冗談。毎回質問する必要はなく、毎回自己開示する必要もなく、毎回話題を広げる必要もない。相手の発言が短い場合も短い返信は自然な選択肢だが、返信の長さは相手文の文字数だけで決めず、本人Goldや相手別の返信傾向も考慮すること。相手別Goldに長めの傾向が明確なら、話題が許す範囲で長めの候補も作る。ただし不要な説明や質問で膨らませないこと。\n"
        "（Step 7 追記）相手メッセージが短文（short区分）の場合、相槌・共感・一言程度の短い候補を必ず1案以上含めること。3案は可能な場合に異なる会話戦略（短反応 / 少し展開 / 少し詳しい反応）を持たせること。ただし意味のある違いがない場合は無理に違わせないこと（不自然な差別化は不正）。\n"
        "（Step 8 追記）質問は「相手が質問している」または「会話上情報を聞くことが自然」な場合に限定すること。「会話を続けた方がよい」という理由だけでは質問しないこと。NO QUESTION（質問なしの相槌・共感・短反応・一言・労いだけで終える）は正式な戦略として許可する。例: 相手「今日疲れた」→「おつかれさまです」「それは疲れますね」「ゆっくり休んでください」の3案でも成立する。\n"
        "（Step 14-R 追記）おやすみ・またね・了解・ありがとう等の会話終了・受領時は短い返答で終え、新しい質問・話題を追加しないこと。\n"
        "（Step 17-R 追記）書き出しの共感・相槌を毎回同じ定型表現で始めないこと。3案の文頭表現は互いに異なるものにし、共感表現は Gold 実績と文脈から自然に選ぶこと。特に短い相づち・挨拶・受領への返信は、相手の語をそのまま一語返しにせず、本人Goldに沿った短い反応を返すこと。共感・労い・気遣いを1つの返信に積み重ねすぎないこと（必要な反応だけで成立させる。相談・長文への返信はこの限りではない）。相手の発言と会話履歴から自然に導けない新しい話題・質問を勝手に追加しないこと（自然な連想・相づち・既出の話題までは禁止しない）。\n"
        "（Step 17-R2 追記）自然に異なる候補が作れる場合だけ3案を差別化すること。短い相づち・終了・受領（まあね/へー/そっか/了解/ありがとう/おつ 等）では3案が似ても問題ない。方向の違いを質問の有無で作らないこと（質問は必要な場合だけに入れ、3案の差別化のために質問を追加しない）。同じ同意・共感を語尾や同意語だけ変えた言い換え3連（同じ意味を3回言うこと）は、返信の幅が広い場合に避けること。ただし短い相づち・終了・受領など返信の幅が狭い場合は自然な範囲の類似を許容し、本人 Gold が短い言い換えを多用する場合は本人らしさを優先すること。\n"
        "（Step 18-R4 追記）気遣いや共感が自然な場面では、候補群の反応の焦点を変えること。短い労い、相手の気持ちへの共感、話題に沿う穏やかな気遣いなど、無理のない違いを使い分け、全案を同じ助言や締め方にそろえない。気遣いの返信は助言だけに寄せず、労いや共感だけで自然に成立する案も選択肢に含め、全案へ具体的な行動の助言を付けない。質問を足さず、確認できない近況や行動を補わず、短い相づちしか自然でない場面では無理に広げない。\n"
        "（Step 17-R6 追記）相手がすでに話した内容を改めて質問形で聞き返さないこと。相手の短い一言に原因を尋ねる質問を付けないこと。相手の質問・報告に答える際は回答だけで終え、確定した内容への確認質問や回答後の逆質問を付け足さないこと。質問は必要・自然・文脈上意味がある場合のみに入れること（Step 17-R5 の具体例列挙はモデルが例文をコピーするため撤廃し、行動レベルのみ残す）。疑問文の文末の疑問符は1つにすること（？？のような重ね付けはしない。Step 17-R6 loop）。1つの返信に含める疑問符は1つまでとすること（複数の質問を1つの返信に入れない。Step 17-R6 loop2）。\n"
    )
    rule_number_offset = 1 if mode == "followup" else 0
    line_break_rule = (
        "8. 改行は読みやすさに応じて使うこと。一文だけで自然に成立する短い返信に、改行や追加文を強制しない。複数文の場合も読みやすければ同じ行でよい。\n"
        if mode == "followup"
        else "8. 1文1行改行の絶対遵守: 1文ごとに必ず改行を入れること。複数の文を改行なしで1行に続けてはならない（文章の改行は絶対ルール）。\n"
    )

    topic_guidance_rule = (
        f"{14 + rule_number_offset}. 追いメッセージでの話題の扱い: 話題を無理に広げず、質問は必要な場合だけにする。プロフィールや確認済み情報への短い反応で終えてよい。\n"
        if mode == "followup"
        else "14. 相手の話題の完全深掘り（『ほかにも』『ほかに』『他に』『〜以外』『〇〇もいいですけど』の完全禁止・絶対遵守）: 相手が出した話題や好きなもの（例: お寿司、焼き鳥、ラーメン、カフェ等）から逃げて『ほかにも好きな料理はありますか？』『〜のほかに何が好き？』『〜以外だと何が好き？』『〇〇もいいですけど…』のように別の話題に横スライドする質問は『完全禁止』。必ず相手が出した話題そのもの（「何が一番美味しかったですか？」「おすすめのメニューはありますか？」「どこのお店に行ったんですか？」「お気に入りのお店とかあるんですか？」等）を素直に深掘りして話を広げること。\n"
    )

    followup_name_rule = (
        (
            f"1. 相手の呼称: 相手の名前を呼ぶ時は必ず「さん」付け（呼び捨て・あだ名禁止）。"
            f"名前が確認できる場合だけ『{contact_name}さん』と呼べる。呼びかけを無理に足さない。\n"
        )
        if contact_name else "1. 相手の呼称: 名前が未設定または未確認なら名前で呼びかけない。「さん」付けは確認済みの名前にのみ使い、仮の名前や敬称を作らない。\n"
    )

    b2_hard = (
        "【HARD INVARIANTS】\n【CORE RULES】絶対遵守ルール（違反厳禁）\n"
        f"{followup_name_rule}"
        "2. 話者の完全分離（絶対厳守）: 相手の発言した体験・好み・近況（例: お酒が苦手、〇〇に行った、〇〇が好き等）を、自分が言ったかのように語ったり混同しないこと。また自分が話したことを相手が話したかのように扱わないこと。相手の最新発言に対して自分側のリアクションを返すこと。\n"
        "3. 架空自己開示の完全禁止: 架空の自己開示・事実捏造の禁止。ユーザーの設定や会話履歴にない体験（「小さい頃連れて行ってもらった」「普段よく行く」「俺も好き」等）や好みを勝手に捏造しないこと。\n"
        "4. 会話履歴の既出情報重複禁止: 相手が写真コメント等で既に答えた内容や、過去にすでに聞いたことを再質問したり、既に話した自己開示を初めてのように繰り返さない。\n"
        "5. 不自然なカタカナ語の禁止: 『リフレッシュ』等の不自然なカタカナ語は使用しないこと。\n"
        "6. MULTI-TOPIC RULE: 相手が複数の話題を出している場合はメインの話題に絞って自然に展開すること。\n"
        "7. 本人の未確認の経験を尋ねる場合（行った・食べた・試したこと等）、好み（犬派か猫派か、映画が好きか等）、予定・空き状況、生活習慣（起床時刻等）を聞かれ、本人情報・本人側の会話履歴に答えがない場合は、返信案を作らずアプリ利用者への確認として [AI_QUESTION]質問内容[/AI_QUESTION] のみを出力すること。"
        "一般的な反応や相手の好みへの感想を、本人の経験・好みの回答として扱わないこと。アプリ利用者への確認文をチャット相手に送る返信案へ混ぜないこと。"
        "ただし『あれどうなった？』『それって何のこと？』のように会話上の参照先が不明な状況確認は別ケース。これは本人の経験を尋ねる依頼ではないため、アプリ利用者へ質問せず、相手に送る短い確認文を通常のJSON返信候補として作る。このケースでは [AI_QUESTION] を出力しない。\n"
        f"{line_break_rule}"
        "9. 質問は任意: 質問を含めるかどうかは会話状況次第であり、質問なしの短い返信（例: 「それはきついな」「いいな」）も正式な正常系として扱うこと。相手が明確な質問をしている場合は必ず回答すること（例: 「明日何時にする？」→「14時くらいで大丈夫」）。既出質問の繰り返しは禁止。\n"
        "10. 会話継続・文脈参照: 以前の会話で相手が話した内容（趣味・予定・出来事・好み・気持ち等）を会話履歴から正確に参照し、直前の相手メッセージの話題から自然に関連付けて会話を継続すること。プロフィールの細かい単語を唐突に持ち出して直前メッセージを無視してはならない。\n"
        f"{flow_rule}"
        f"{12 + rule_number_offset}. 本人のリアルな文体・口調の完全再現: 返信案は学習プロファイルおよび直近手入力実例の本人の文体・口調（語尾『〜ですよね！』『〜なんですよね笑笑』、一人称『僕』、笑や記号の入れ方）を忠実に再現すること。疑問文の文末の疑問符は必ず1つにすること。？？のような重ね付けはしないこと（厳守。Step 17-R6 loop）。\n"
        "   ※（AI特有の定型構文の完全禁止）以下のような他人行儀で不自然なAI作文は完全禁止:\n"
        "     ×『〜を求めて行ってきました』『〜の時間は最高ですね』『〜思わず共有です』『〜で驚きました』『〜はお好きですか？』『〜はいかがでしょうか？』\n"
        "     〇本人が普段送っているリアルなチャット言葉（『〜行ってみました笑』『めっちゃ美味しかったです！』『〜行ったりします？』等）にすること。\n"
        f"{13 + rule_number_offset}. 季節・気候の矛盾禁止（受動的ルール）: 季節や天候に触れる場合のみ、現在の時期（{season_name}）と矛盾する嘘（夏に『寒くなってきた』、冬に『暑いですね』等）をつかないこと。※季節や天気の話題を無理に出す必要は一切ありません。日常や趣味、相手とのやり取りを最優先してください。\n"
        f"{topic_guidance_rule}"
        f"{15 + rule_number_offset}. 『〜とのこと』『〜と拝見』等の機械的AI表現の完全禁止（絶対遵守）: 『〇〇とのことですが』『〇〇とのこと』『〇〇と拝見しました』『〇〇と書かれていましたので』のような機械的で他人行儀な表現は『完全禁止』。本人が直接写真やメッセージを見て自然に話しかける口語（『〇〇なんですね！』『〇〇美味しそうですね！』）にすること。\n"
        f"{16 + rule_number_offset}. 自然な平仮名表記（絶対遵守）: 『何か』は漢字表記を禁止し、必ず平仮名で『なにか』と表記すること（例: 『なにかありますか？』『なにか食べに行きました？』）。※『ほかに』『ほかにも』を使った質問自体は禁止です。\n"
        "【USER INFO & KNOWLEDGE】\n【SELF & MY INFO & USER KNOWLEDGE】\n"
        f"- 本人基本情報: {my_info_text}\n"
        f"- 本人知識・体験メモ: {user_k_text}"
    )
    if self_prof_text and self_prof_text != "(未設定)":
        b2_hard += f"\n- 本人プロフィール（【SELF】）:\n{self_prof_text}"

    # Step 8: FACT BOUNDARY（相手事実の推測混入防止。架空自己開示禁止とは別軸）
    b2_hard += (
        "\n【FACT BOUNDARY】相手の事実と自分の事実の境界（絶対遵守）\n"
        "- CHAT HISTORY に存在しない具体的事実（行動・場所・時間・理由・感情・経験・状況）を作らないこと。\n"
        "- 相手の発言から具体的な状況を勝手に確定しないこと（例: 「疲れた」→「立ちっぱなしだった」等の具体化は禁止）。\n"
        "- 不明なことを知っている前提で返答しないこと。推測を事実として文章化しないこと。\n"
        "- 自然な感情反応（例: 「大変ですね」「おつかれさまです」）は許可する。具体的事実の追加はしない。\n"
        "- 未知の情報を聞く場合は、断定ではなく質問として尋ねること。\n"
        "- 「眠い」に「眠いですよね」だけのような、相手の状態を同じ言葉で言い換えただけの返事にしない。短い労いや気遣いなど、会話上の反応を添える。\n"
        "- 助詞を省略して不自然な文にしない。送信前に文全体が自然な日本語か確認する。\n"
        "- 自分の事実は CHAT HISTORY・Gold 実績に確認できる場合のみ使うこと（例: 履歴にない「自分も最近映画見ました」は禁止）。\n"
        "- 事実と感想を混同しないこと。相手の発言の要約・分析・説明を長々と返さないこと。"
    )
    if mode != "followup":
        b2_hard += (
            "\n- 参照先が会話履歴から特定できない指示語（「あれ」「それ」「その件」「例の話」など）について、"
            "まだ決まっていない・これから決める・進行中・完了した等の状況を推測で答えない。"
            "根拠として使えるのは、直前の同じ話題について自分が述べた、まだ更新されていない状況だけ。"
            "古い別話題の状況や、同じ履歴内に一度出たというだけの状況は根拠にしない。"
            "参照先が分からなければ、相手に送る返信候補として『何のことだった？』のように短く確認する質問だけを作る。"
            "『ちょっと待ってね』など参照先を確認しない埋め草や、状態を尋ねるふりをした断定は候補にしない。"
            "候補は通常のJSON repliesに入れ、このケースでは [AI_QUESTION] を出力しない（アプリ利用者へ聞くのではなく、チャット相手へ聞く）。"
        )

    # Block 3: CHAT HISTORY / CONTACT & CHAT HISTORY (事実ソース)
    counterpart_label = f"{contact_name}さん" if contact_name else "相手"
    counterpart_title = (
        f"相手（{counterpart_label}）" if counterpart_label != "相手" else "相手"
    )
    contact_info = f"相手のお名前: {contact_name}さん" if contact_name else "相手のお名前: 未設定（名前で呼びかけない）"
    if contact_profile:
        contact_info += f"\n相手のプロフィール（※補助参考情報）:\n{contact_profile}"

    history_parts = [
        "【CHAT HISTORY】\n【CONTACT & CHAT HISTORY】現在までの会話履歴（唯一の事実ソース）",
        contact_info,
    ]

    if conversation_ledger:
        asked_list = conversation_ledger.get("already_asked_questions") or []
        unresolved = conversation_ledger.get("unresolved_question")
        last_c_msg = conversation_ledger.get("last_contact_message") or ""
        intent = (conversation_ledger.get("counterpart_intent") or "report").strip() or "report"
        if intent not in _INTENT_POLICIES:
            intent = "report"
        ledger_lines = [
            "【CONVERSATION STATE & CONTEXT CONTINUATION】（会話状態・文脈継続）",
        ]
        if mode == "followup":
            ledger_lines.append("※（追いメッセージモード）未返信の質問や最後の会話の単なる言い換えは避けること。プロフィールや過去の共有情報を使うかどうかは任意。使える話題が一つなら別の話題を捏造せず、短い反応を優先すること。")
        else:
            if last_c_msg:
                ledger_lines.append(f"★（最優先返答対象）{counterpart_title}の直前の最新メッセージ:\n「{last_c_msg}」\n※まずこのメッセージ内容に対する反応・共感から返信を始めること。反応・共感とは自分の言葉（おつかれ/いいね/わかる/笑など）であり、相手の発言の繰り返しではない。ただし相手の発言の言葉をそのまま言い換えて返信を始めず、自分の反応の言葉から返すこと（Step 17-R・18-R2）。")
            ledger_lines.append(f"【COUNTERPART INTENT】\n{intent}\n（相手発言の意図分類。返信方針を決めるための強い参考情報であり、Hard Rule ではない。最終判断は会話履歴・本人実例・条件を総合して行うこと）")
            ledger_lines.append(f"- Intent別方針（{intent}）: {_INTENT_POLICIES[intent]}")
            # Step 7: 連続質問の抑制（参考情報。相手の質問・確認必要時・Gold質問中心は除外）
            if conversation_ledger.get("prev_self_ended_with_question"):
                ledger_lines.append("- 直近の自分の返信は質問で終わっている。今回は質問なしでも成立する候補を優先し、相手の明確な質問・確認が必要な場合を除き追加の質問は控えること。")
            if unresolved:
                ledger_lines.append(f"- 相手からの直近質問（要回答）: {unresolved}")
        if asked_list:
            ledger_lines.append("- 既出質問・回答済み話題（再質問禁止）これまで自分が相手に聞いた質問:")
            for q in asked_list:
                ledger_lines.append(f"  ・{q}")
            if mode == "followup":
                ledger_lines.append("  ※上記質問の再質問はしないこと。新しい質問は無理に用意せず、必要な場合だけ自然に入れること。")
            else:
                ledger_lines.append("  ※上記質問の再質問は厳禁。相手の発言を踏まえた新しい自然な質問を用意すること。")
        history_parts.append("\n".join(ledger_lines))

    history_parts.append(f"【これまでの会話の流れ】\n{chat_history_text.strip() if chat_history_text.strip() else '(会話開始)'}")
    b3_history = "\n\n".join(history_parts)

    # Block 4: LEARNED USER RESPONSE POLICY & SAME-CONTACT GOLD EXAMPLES
    policy_parts = []
    if learned_policy_block.strip():
        policy_parts.append(f"【LEARNED USER RESPONSE POLICY】\n【USER LEARNED STYLE PROFILE】\n{learned_policy_block.strip()}")
    elif user_style_profile.strip():
        policy_parts.append(f"【LEARNED USER RESPONSE POLICY】\n【USER LEARNED STYLE PROFILE】\n{user_style_profile.strip()}")
    else:
        policy_parts.append(
            "【LEARNED USER RESPONSE POLICY】\n【USER LEARNED STYLE PROFILE】\n"
            "ユーザー本人が実際に送信してきた実績スタイルを最優先として反映すること。"
        )

    if same_contact_gold_block.strip():
        policy_parts.append(same_contact_gold_block.strip())

    b4_policy = "\n\n".join(policy_parts)

    # Block 5: POSITIVE REPLY PAIRS / RETRIEVED USER REPLY PAIRS
    _retrieval_note = (
        "※検索実例はユーザーの文体・語尾・テンポの参考であり、同じ会話構造（質問・自己開示・話題展開の有無）を"
        "毎回再現する指示ではない。実例に質問が含まれていても、今回必ず質問する必要はない。"
    )
    if positive_pairs_block.strip():
        b5_pairs = f"【POSITIVE REPLY PAIRS】\n【RETRIEVED USER REPLY PAIRS】\n{positive_pairs_block.strip()}\n{_retrieval_note}"
    elif retrieved_reply_pairs:
        pair_lines = []
        for idx, p in enumerate(retrieved_reply_pairs, start=1):
            pair_lines.append(f"[良例ペア {idx}]\n相手: {p.get('contact_text') or p.get('contact_turn')}\n自分: {p.get('self_text') or p.get('self_turn')}")
        b5_pairs = "【POSITIVE REPLY PAIRS】\n【RETRIEVED USER REPLY PAIRS】\n" + "\n\n".join(pair_lines) + f"\n{_retrieval_note}"
    else:
        b5_pairs = "【POSITIVE REPLY PAIRS】\n【RETRIEVED USER REPLY PAIRS】\n(該当なし - LEARNED USER RESPONSE POLICY を基準に作成)"

    # Block 6: RELEVANT CONTRAST
    b6_contrast = contrast_block.strip()

    # Block 7: COUNTERPART STYLE ADAPTATION / COUNTERPART WRITING STYLE
    # Step 2: 相手メッセージの長さ区分を傾向情報として付与（Hard Limit ではない）
    length_lines = []
    if same_contact_gold_samples >= 5 and same_contact_gold_length_median is not None:
        length_lines.append(
            f"【CONTACT GOLD LENGTH】本人Goldの文量中央値（観測値）: {same_contact_gold_length_median}文字。"
            "固定の文字数目標ではなく、相手ごとの返信傾向を示す参考情報として扱うこと。"
        )
    if counterpart_length_tier in ("short", "medium", "long"):
        tier_label = {"short": "短文", "medium": "中文", "long": "長文"}[counterpart_length_tier]
        length_lines.append(
            f"【COUNTERPART MESSAGE LENGTH】相手の直近メッセージ: 約{counterpart_length_chars}文字（区分: {counterpart_length_tier}・{tier_label}）"
        )
        if counterpart_length_tier == "short":
            if same_contact_gold_samples >= 5:
                length_lines.append(
                    "- 相手が短文でも、相手別Goldの文量傾向を主な参考にすること。短文という理由だけで一言返信に縮めず、"
                    "話題が許す範囲で本人のGoldに近い文量を使う。ただし無理な話題拡張や余分な説明は避けること。"
                )
            elif same_contact_gold_samples >= 3:
                length_lines.append(
                    "- 短いリアクションは自然な選択肢だが、相手別Goldの文量と現在の話題も見て返信量を決めること。"
                    "相手が短文という理由だけで一言返信に縮めないこと。"
                )
            else:
                length_lines.append("- 相手が短文のため、短いリアクション・共感・一言回答を優先し、長い文章や無理な話題拡張は避けること。")
            if counterpart_length_chars <= 3:
                length_lines.append("- 相手の発言がごく短い相づち・挨拶のため、1行の短い返信を優先し、2行以上の返信は避けること（Step 17-R6 loop）。")
        elif counterpart_length_tier == "medium":
            if same_contact_gold_samples >= 5:
                length_lines.append(
                    "- 相手別Goldの文量傾向を主な参考にし、固定した行数に寄せないこと。"
                    "短い反応が自然な場面では簡潔にし、話題が許す場合はGoldに近い文量を使うこと。"
                )
            else:
                length_lines.append(
                    "- 2〜3行程度を目安にしてよいが、短い一文だけで自然に成立するならそのまま返すこと。"
                    if mode == "followup"
                    else "- 2〜3行程度の自然な返信を基本とすること。"
                )
        else:
            length_lines.append("- 相手の内容に合わせた丁寧な返信をしてよい。")
        length_lines.append("※文字数のHard Limit ではない。回答に必要な長さは許容する。")
    length_text = "\n".join(length_lines)
    cp_summary = counterpart_style_block.strip() or counterpart_style.strip()
    if same_contact_gold_samples >= 3 and cp_summary:
        b7_counterpart = (
            f"【COUNTERPART STYLE ADAPTATION】\n【COUNTERPART WRITING STYLE】{counterpart_title}への適応\n"
            f"{cp_summary}\n"
            "- 返信の要否・内容・長さは現在の会話内容を最優先すること。本人のGold実例とGlobalの本人文体を土台にし、相手の温度感は補助情報にとどめる。\n"
            "- 相手発言のオウム返し・コピー禁止。相手の語句・語尾・口調を模倣せず、距離感を合わせるためだけに質問や説明を追加しないこと。\n"
            "- 相手の短文傾向だけで本人Goldの文量を縮めないこと。Goldに長めの傾向がある場合は、話題が許す範囲で本人らしい長さを保ち、説明の水増しは避ける。相手文の言い換え＋感嘆だけの返信は避け、自分の言葉で反応すること。"
        )
    elif same_contact_gold_samples >= 3:
        b7_counterpart = (
            f"【COUNTERPART STYLE ADAPTATION】\n【COUNTERPART WRITING STYLE】{counterpart_title}への適応\n"
            "- 返信の要否・内容・長さは現在の会話内容を最優先すること。本人のGold実例を最優先し、Globalの本人文体を土台にする。相手の温度感は補助情報にとどめる。\n"
            "- 相手発言のオウム返し・コピー禁止。相手の文体は補助情報にとどめ、語句・語尾・口調を模倣しない。適応のためだけに質問や説明を追加しないこと。\n"
            "- 相手の短文傾向だけで本人Goldの文量を縮めないこと。Goldに長めの傾向がある場合は、話題が許す範囲で本人らしい長さを保ち、説明の水増しは避ける。相手文の言い換え＋感嘆だけの返信は避け、自分の言葉で反応すること。"
        )
    elif cp_summary:
        b7_counterpart = (
            f"【COUNTERPART STYLE ADAPTATION】\n【COUNTERPART WRITING STYLE】{counterpart_title}への適応\n"
            f"{cp_summary}\n"
            "- 相手発言のオウム返し・コピー禁止。自分のスタイルを土台にしつつ、相手の温度感・文量に20〜30%程度自然に適応させること。\n"
            "- 相手が短文中心の場合、説明的な長文にせず短く返すこと。相手文の言い換え＋感嘆だけの返信は避け、自分の言葉で反応すること。"
        )
    else:
        b7_counterpart = (
            f"【COUNTERPART STYLE ADAPTATION】\n【COUNTERPART WRITING STYLE】{counterpart_title}への適応\n"
            "- 相手発言のオウム返し・コピー禁止。自分のスタイルを土台にしつつ、相手の温度感・文量に20〜30%程度自然に適応させること。\n"
            "- 相手が短文中心の場合、説明的な長文にせず短く返すこと。相手文の言い換え＋感嘆だけの返信は避け、自分の言葉で反応すること。"
        )
    if length_text:
        b7_counterpart += f"\n{length_text}"

    # Block 8: OUTPUT CONTRACT / FINAL TASK (誤分割完全阻止契約)
    if mode == "followup":
        b8_contract = (
            "【OUTPUT CONTRACT】\n【FINAL TASK】追いメッセージ出力契約（自然な切り口の複数案・未返信質問の反復禁止・催促厳禁・本人の文体再現）\n"
            "※未返信の質問を繰り返したり、最後の会話を単に言い換えたりしないこと。未返信の質問と同じ情報を求める質問や、その細分化・言い換えも避ける。自分の直近メッセージの要約・言い換えもしない。プロフィールや過去の共有情報を使うかどうかは任意。使える話題が一つなら、無理に別の話題を捏造して3案を水増ししないこと。\n"
            "※プロフィールの好みから、所有・経験・具体的な選択傾向を推測しない。プロフィールに嗜好が書かれているだけなら、所有・飼育・経験・利用の有無を質問で確認しない。プロフィール情報には短い感想として触れられる。プロフィールの話題に触れること自体も返信の必須条件ではない。\n"
            "※時刻・日付・曜日・相手の近況が履歴で確認できない限り、挨拶や状況を推測で足さない。回答時点の日時だけを根拠に、履歴にない出来事の完了・経過を推測しない。未来・過去の時点や期間が会話で明示されていないなら、現在述べられている状況だけに応じ、出来事がすでに終わったとは仮定しない。過去形で近況を尋ねる場合は、その出来事の期間が実際に経過したと会話履歴から確認できるときに限る。自分の希望や予定も、履歴などに根拠がなければ作らない。初回の挨拶や関係上必要な場面以外で定型句を足さない。\n"
            "※本人が普段送っているリアルなチャット言葉・語尾（『〜ですよね！』『〜なんですよね笑笑』『〜ですかね？？』等）や一人称（『僕』）、笑の使い方を忠実に再現すること。\n"
            "※『〜とのこと』『〜と拝見』等の機械的AI表現は完全禁止。『何か』は漢字を使わず平仮名で『なにか』と表記すること。『ほかにも』『ほかに』『他に』『〜以外』『〇〇もいいですけど』の話題切り替えは禁止すること。\n"
            "※季節の話題を無理に入れる必要はありません（季節外れの嘘のみ禁止）。日常会話や相手の趣味・プロフを優先してください。\n"
            "※質問は会話を続けるためだけに足したり、相手に返信負担を生む形にしたりしないこと。確認済みの話題に自然につながり、短く答えやすい質問は必要な場合に限ってよい。質問なしの短い反応も正当な選択肢。\n"
            "以下は候補の切り口です。3案をすべて異なる役割に機械的に割り当てたり、順番を固定したりする必要はありません。候補の違いを作るために話題や反応を無理に変えず、同じ話題から自然に成立する複数案を作ってよいです。同じ確認済み話題で短い反応を作ってよい一方、意味が同じ言い換えだけの案や同じ情報を尋ねる質問を並べないでください。事実を作ってまで切り口を分けてはなりません。自然に複数の切り口がある場合は、反応の焦点や言い方を変えて候補に幅を持たせます。質問を含む案は必要性が高い場合も原則1案までを目安にし、残りは短い反応や感想で構いません:\n\n"
            "■ 【プロフィール・共有情報フック】（確認できる趣味や共有済みの話題に触れる）\n"
            "  ・プロフィールや会話履歴の情報は、自然な感想や軽い問いかけにつながる場合に限り使う。話題を取り上げることや問いかけにすることは必須ではない。相手の趣味に関連する行動を自分がしたと作ってはならない。\n"
            "  ・プロフィールに明記された好みを自然な感想にする程度でよい。未返信質問と同じ情報を求める質問やその細分化はしない。\n\n"
            "  ・例: プロフィールにある趣味なら「カフェ巡り好きなんですね」のような短い感想でよい。\n\n"
            "■ 【軽いリアクション】（気負わせない短い反応や、柔らかいユーモア）\n"
            "  ・返信が途切れたことを責めたり、相手の状況を決めつけたりせず、返事を急かさない自然な一言にする。\n"
            "  ・未確認の近況や生活状況を推測して加えない。\n"
            "  ・例: 相手のプロフィールや直近の話題への短い感想など、返信義務を感じさせない内容にする。\n\n"
            "■ 【相手の関心へのコメント】（相手の確認済みの趣味や話題に別の角度から触れる）\n"
            "  ・これは相手の関心へのコメントを指し、プロフィールだけで本人と関心・好みが共通しているとは扱わない。本人の好み・所有・経験・訪問・飲食などの具体的な事実は、SELF自身の履歴やGold実績に明記されている場合だけ使う。\n"
            "  ・相手のプロフィールや過去の会話にある情報から、自然な感想や関心を示す。確認できる本人の経験がなければ、相手の話題への感想だけで自然にまとめる。\n\n"
            "確認できる事実がない場合は、架空の行動・体験を作らず、プロフィールへの感想や短いリアクションに切り替えてください。\n"
            "2. 催促・問い詰めの完全禁止: 「返信まだ？」「忙しい？」「返信待ってます」等は厳禁。\n"
            "3. 不自然なカタカナ語の禁止: 『リフレッシュ』等のカタカナ語は使用せず、『気分転換』『息抜き』『癒やされる』等の自然な日本語を使うこと。\n"
            "4. 短文・低負担: 長さは会話に合わせて自由に選び、短い一文だけで自然に成立するならそのまま返す。\n"
            "5. 改行: 複数文の場合は読みやすい改行を使ってよいが、一文の短い反応を分割したり、改行を必須にしたりしない。\n"
            + (
                f'6. 出力は必ず JSON形式の {format_tapple_output_contract(candidates)} とし、説明や前置きは出力しないこと。'
                f'{tapple_strategy_contract_guidance()}'
                if strategy_mode == "tapple"
                else '6. 出力は必ず JSON形式の {"replies": ["返信案1", "返信案2", "返信案3"]} のみとし、説明や前置きは一切出力しないこと。'
            )
        )
    else:
        b8_contract = (
            "【OUTPUT CONTRACT】\n【FINAL TASK】出力契約（候補の多様性と独立性・誤分割禁止・1文1行改行・質問任意）\n"
            "1. 完全独立の3案（本人の文体再現・候補の多様性）: 3案それぞれが単独でそのまま送信できる完成品であること。本人の語尾や一人称、テンポを忠実に再現すること。\n"
            "   ※1つの返信文を「案1=反応、案2=自己開示、案3=質問」のように3分割して出力することは厳禁です。\n"
            "   各案がそれぞれ独立した異なる会話展開（本命、別展開、切り口違い）を持つこと（候補の多様性と独立性）。\n"
            "   3案の構成を機械的に固定しないこと（例: 「案1=短いリアクション、案2=少し展開、案3=質問あり」のような固定パターンは禁止）。会話内容に応じて3案とも短くなることも許可する。\n"
            "   意味のある違いがある場合だけ違わせること。候補を無理に差別化して不自然な案を作ってはならない。\n"
            "   目安として、案1は最も自然で短い反応、案2は少し会話を広げる反応、案3は必要なら質問を含む反応とすることが多い。ただし会話内容に応じてこの目安に従わないことも許可する。質問が不自然な場合は案3も質問なしでよい。\n"
            "2. 単なる語尾の差し替えではなく、それぞれ切り口や内容が異なること。相手の話題と無関係な新しい話題（旅行・趣味等の唐突な持ち出し）を開始しないこと。広げる場合は相手の発言から直接つなげること。\n"
            "3. 1文1行改行の絶対遵守: 各案は必ず1文ごとに改行を入れて出力すること。\n"
            "4. 質問は任意: 質問を含めるかは会話状況次第とし、質問なしの案も正式な正常系として扱うこと。質問は会話上必要な場合だけ生成すること。会話を続ける目的だけで質問を追加しないこと（質問すること自体を禁じるものではない）。『ほかにも』『ほかに』『他に』『〜以外』『〇〇もいいですけど』の話題逃げ・並列質問は完全禁止とし、質問する場合も相手が出した話題そのものを深掘りして広げること。相手が明確な質問をしている場合は回答を含めること。\n"
            "5. 『〜とのこと』『〜と拝見』等の機械的AI表現の完全禁止: 『〇〇とのことですが』『〇〇とのこと』『〇〇と拝見しました』等の他人行儀なAI表現は完全禁止し、自然なチャット口語（『〇〇なんですね！』『〇〇いいですね！』）にすること。相手の発言をほぼ同じ意味で言い直しただけの返信（相手「最近映画見てる」→「最近映画見てるんだね」等）は情報量が増えないため避け、自分の言葉での反応（「それ面白そう」等）にすること。\n"
            "6. 自然な平仮名表記: 『何か』は漢字を使わず平仮名で『なにか』と表記すること。\n"
            + (
                f'7. 出力は必ず JSON形式の {format_tapple_output_contract(candidates)} とし、説明や前置きは出力しないこと。'
                f'{tapple_strategy_contract_guidance()}'
                if strategy_mode == "tapple"
                else '7. 出力は必ず JSON形式の {"replies": ["案1の独立返信文章", "案2の独立返信文章", "案3の独立返信文章"]} のみとし、説明や前置きは一切出力しないこと。'
            )
        )

    blocks = [b1_role, b2_hard, b3_history, b4_policy, b5_pairs]
    if b6_contrast:
        blocks.append(b6_contrast)
    blocks.extend([b7_counterpart, b8_contract])

    return "\n\n" + ("\n\n".join(blocks))


_SILENT_SELF_CHECK = (
    "各返信を確定する前に、以下を黙って確認すること（チェック結果自体は出力しないこと）:\n"
    "1. 具体的事実を捏造していないか？\n"
    "2. 不明な状況を事実として断定していないか？\n"
    "3. 相手のメッセージを繰り返していないか？\n"
    "4. 会話継続のためだけの質問を追加していないか？\n"
    "5. 説明文ではなく実際のメッセージらしく聞こえるか？\n"
    "6. ユーザーの Gold 実例と一致しているか？"
)


def format_tapple_output_contract(candidates: int = 3) -> str:
    reply_slots = ", ".join(f'"案{i + 1}"' for i in range(candidates))
    return (
        f'{{"replies": [{reply_slots}], "strategy": '
        '{"action":"continue|clarify|invite|wait|stop",'
        '"rationale":"根拠に基づく短い説明",'
        '"evidence":["会話からの完全一致抜粋"],'
        '"invite_example":null}}'
    )


def tapple_strategy_contract_guidance() -> str:
    return (
        " repliesは必須です。strategyは任意のトップレベル項目で、会話上の根拠がある場合のみ含め、"
        "根拠不足ならstrategyキーを省略してください。"
        "invite_exampleはinvite時のみ文字列にし、それ以外のactionではnullにしてください。"
    )


def build_initial_generation_messages(
    *,
    system_prompt: str,
    chat_history_text: str = "",
    candidates: int = 3,
    mode: str = "normal",
    strategy_mode: str = "none",
    contact_style_instruction: str = "",
) -> list[dict[str, str]]:
    """初回返信生成用のメッセージリストを組み立てる（全履歴の二重投入を廃止）。"""
    if mode == "followup":
        user_instruction = (
            "【追いメッセージ生成命令】\n"
            "【USER LEARNED STYLE PROFILE】および【SAME-CONTACT RECENT GOLD REPLIES】の本人の言葉遣い・語尾（『〜ですよね！』『〜なんですよね笑笑』『〜ですか？？』等）や一人称（『僕』）を忠実に再現してください。\n"
            "未返信の質問を繰り返したり、最後の会話を単に言い換えたりしないでください。未返信の質問と同じ情報を求める質問や、その細分化・言い換えも避け、自分の直近メッセージの要約・言い換えもしないでください。プロフィールや過去の共有情報を使うかどうかは任意です。使える話題が一つなら、無理に別の話題を捏造して3案を水増ししないでください。また『〜とのこと』『〜と拝見』等の機械的AI表現や過度な季節・天気の話題は避け、自然な日常会話で作成してください。\n"
            "『何か』は平仮名『なにか』と表記し、『ほかにも』『ほかに』『〜以外』『〇〇もいいですけど』等の話題切り替えは避けてください。\n"
            "自分の具体的な行動・経験は、CHAT HISTORYまたはGold実績に明記されている場合だけ使ってください。プロフィール情報は相手の好みを理解する参考であり、自分の行動・経験の根拠にはしません。確認できない訪問・飲食・予定・体験を作ってはいけません。根拠がなければ、相手の趣味への感想や軽いリアクションだけで成立させてください。\n"
            "プロフィールの好みから、所有・経験・具体的な選択傾向を推測しないでください。プロフィールにある趣味に触れること自体を返信の必須条件にしないでください。\n"
            "未確認の近況や生活状況を推測して言及しないでください。\n"
            "時刻・日付・曜日・相手の近況が履歴で確認できない限り、挨拶や状況を推測で足さないでください。初回の挨拶や関係上必要な場面以外で定型句を足さないでください。\n"
            "プロフィールや共有済みの話題、軽いリアクション、相手の関心へのコメントなどから、文脈に合う返信を3案作ってください。プロフィールだけを根拠に本人と相手の関心が共通しているとは扱わず、本人の好み・経験はSELFの履歴やGold実績で確認できる場合だけ使ってください。候補の違いを作るために話題や反応を無理に変えず、同じ話題から自然に成立する複数案を作ってよいです。同じ確認済み話題で短い反応を作ってよい一方、意味が同じ言い換えだけの案や同じ情報を尋ねる質問を並べないでください。役割や順番を固定する必要はありません。違いを作るために架空の体験を足したり、相手の状況を決めつけたりしないでください。\n"
            "会話を続けるためだけの質問や、返信負担になる質問は避けてください。確認済みの話題に自然につながり、短く答えやすい質問は必要な場合に限って使ってかまいません。質問なしの短い反応も正当な選択肢です。質問の有無だけで候補を差別化せず、同じ話題を質問表現だけ変えて複数案にしないでください。質問を含む案は必要性が高い場合も原則1案までを目安にし、残りは短い反応や感想で構いません。\n"
            "プロフィールに嗜好が書かれているだけなら、所有・飼育・経験・利用の有無を質問で確認しないでください。プロフィール情報には短い感想として触れられます。回答時点の日時だけを根拠に、履歴にない出来事の完了・経過を推測しないでください。未来・過去の時点や期間が会話で明示されていないなら、現在述べられている状況だけに応じ、出来事がすでに終わったとは仮定しないでください。過去形で近況を尋ねる場合は、その出来事の期間が実際に経過したと会話履歴から確認できるときに限ってください。自分の希望や予定も、履歴などに根拠がなければ作らないでください。\n"
            "3案はそのまま送れる自然な文にし、確認できる内容が少ない場合は、質問や自己開示を無理に足さず短く返してください。長さは会話に合わせ、短い一文だけで自然に成立するならそのまま返してください。\n"
            "相手の『眠い』『疲れた』など一言の状態共有には、状態の言い換えではなく、短い労いや気遣いを一文だけ返してください。原因や勤務状況を推測せず、質問や助言を重ねないでください。\n"
            f"{_SILENT_SELF_CHECK}"
            f'出力は必ず JSON形式の {{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}} のみとし、説明・前置き・解説は一切出力しないでください。'
        )
    else:
        user_instruction = (
            f"上記の会話履歴・相手の最新発言を踏まえ、【LEARNED USER RESPONSE POLICY】/【USER LEARNED STYLE PROFILE】および【SAME-CONTACT RECENT GOLD REPLIES】/【POSITIVE REPLY PAIRS】/【RETRIEVED USER REPLY PAIRS】の本人の文体・言葉遣い・語尾・記号（笑/！）を最優先で忠実に再現してください。\n"
            "『〜とのこと』『〜と拝見』等の機械的AI表現や他人行儀な敬語、過度な季節の話題は完全禁止です。『ほかにも』『ほかに』『他に』『〜以外』『〇〇もいいですけど』等の話題切り替え・並列質問は禁止し、相手が出した話題そのものに触れて自然に話を広げること（感想・共感・関連付けを優先し、質問は情報が本当に必要な場合だけにすること）。『何か』は漢字にせず平仮名『なにか』としてください。\n"
            "質問・自己開示・話題拡張は毎回必須ではありません。相手の発言が短い場合は短い返信（例: 「それはきついな」「いいな」）も正式な正常系として許可します。相手が明確な質問をしている場合は回答を含めてください。\n"
            "会話にない相手の行動（例: お茶を飲んだ・おしゃべりした）や、本人の好み・経験（例: その雰囲気が好き・羨ましい）は推測で付け足さないでください。本人の好みや経験は会話履歴・Gold・本人情報で同じ話題について確認できる場合だけ使い、確認できないときは相手の発言への感想や共感にとどめてください。\n"
            "説明文ではなく、その会話で実際に送るメッセージとして作成すること。相手の発言の要約・分析・言い換えではなく、自分の言葉での反応にすること。相手の発言に出てきた話題の言葉は、自然な場合にそのままの言葉で触れること（使うこと自体を返信条件にしない。例: 相手「明日仕事なんだ」→「おつかれ」で成立し、「仕事」を必ず入れる必要はない。相手「キャンプ行きたいな」→「キャンプいいですね！」のように自然な場合は使う。グッズ・自然等の関連語を足しすぎない）。短い返信にも話題の言葉を1語入れると自然で文脈にも合う（例: 相手「昨日映画見てきた」→「映画いいですね！」。長い説明は不要）。ただし相手の文全体を言い換えて返すことはしない（話題の言葉＋自分の反応で返す。例: 相手「新しいドラマ見始めた」→「ドラマいいですね！」。「新しいドラマ見始めたんですね」のような文全体の言い換え返しはしない）（Step 17-R4・R5・18-R2）。\n"
            "報告への返信では、事実を「〜なんですね」と確認し直す形を定番の書き出しにせず、内容を聞いた自分の感想・共感・ねぎらいを先に返してください。同じ事実を言い換えて再掲するだけの候補を作らないでください。\n"
            "架空の自己開示・事実捏造の禁止を厳守し、1つの返信を分割せず各案が単独で送信できる独立した完成品として3案作成してください。\n"
            f"{_SILENT_SELF_CHECK}"
            f'出力は必ず JSON形式の {{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}} （または逆質問時の [AI_QUESTION]...[/AI_QUESTION]）のみとし、説明・前置き・解説は一切出力しないでください。各返信は必ずダブルクォートで囲み、クォートの欠落・日本語括弧「」・＝の混用をしないこと（Step 17-R2）。'
        )
    if strategy_mode == "tapple":
        user_instruction = user_instruction.replace(
            '{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}',
            format_tapple_output_contract(candidates),
        )
    if contact_style_instruction.strip():
        if "かなり長め" in contact_style_instruction and candidates >= 3:
            contact_length_guidance = (
                "ただし長め傾向が明確な相手では、3案中少なくとも2案を、"
                "確認できる話題への反応と、それに直接つながる別の感想・共感の2つを含めてください。"
                "内容が重ならない自然な二文にしてよく、残りは短めに保ってください。"
            )
        elif candidates >= 2:
            contact_length_guidance = (
                "ただし長め傾向がある場合も、1案だけを他の候補より自然に少し厚くし、"
                "残りは短めに保ってください。"
            )
        else:
            contact_length_guidance = (
                "ただし長め傾向がある場合も、今回の1案に自然な感想・共感を添えられる範囲で反映してください。"
            )
        user_instruction += (
            "\n\n【相手別Gold傾向の優先】\n"
            "十分な手入力Goldがある相手では、相手の直近文の短さやGlobalの一般傾向より、"
            "この相手に対する本人Goldの口調・文量を優先してください。"
            "短文の一般方針だけで全案を短くしないでください。"
            f"{contact_length_guidance}"
            "確認できない行動や結果を足したり、質問で長さを作ったりしないでください。"
            "固定文字数には合わせず、現在の話題に合う範囲で反映してください。\n"
            f"{contact_style_instruction.strip()}"
        )
    if strategy_mode == "tapple":
        if candidates != 3:
            user_instruction = user_instruction.replace("3案", f"{candidates}案")
        user_instruction = user_instruction.replace(
            '{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}',
            format_tapple_output_contract(candidates),
        )
        user_instruction = user_instruction.replace(
            "のみとし、説明・前置き・解説は一切出力しないでください。",
            "とし、説明・前置き・解説は出力しないでください。",
        )
    if strategy_mode == "tapple":
        user_instruction += (
            "\n\n【タップル会話戦略】返信候補とは別に、会話の次の方針を構造化して付けてください。"
            "strategy.action は continue / clarify / invite / wait / stop のいずれかです。"
            "判断根拠はCHAT HISTORYにある相手の発言だけに限定し、evidenceにはその発言からの完全一致の短い抜粋を1〜4件入れてください。"
            "rationaleでは活動や場所への関心と、本人に会う意思・相互の誘いを区別してください。相手が店や活動に関心を示しているなら『具体的な関心がない』とは書かず、相互性が弱い場合は『活動への関心はあるが、会う意思や一緒に行く提案は未確認』と説明してください。"
            "inviteは相手が同意したという意味ではなく、こちらから会うことを低圧に打診するのが会話上適切という判断です。"
            "具体的な共通の活動や場所への関心が相手の発言にあり、その話題で相手も会話を広げている場合は、まだ会う同意がなくても断りやすい誘いを提案できます。直前の相づちだけでは誘わないでください。"
            "相手が『近いうちに行ってみたい』など具体的な活動を近い時期にしたいと述べ、共通の活動への関心と会話の相互性がそろい、安全面の懸念や迷いがない場合は、会う同意とは区別したうえでinviteを基本方針として選んでください。明示的に一緒に行きたいと言われるまで待つ必要はありません。短い相づちだけの場合は誘いません。"
            "返信速度、短い相づち、「いいですね」だけの反応、曖昧な好意は、誘う根拠にしないでください。"
            "安全面への不安や相手を信頼できるか分からないという懸念が示されている場合は、別の箇所に参加意思があってもinviteにせず、相手が安心できるまで会話を続けるか待ってください。"
            "直近の相手発言が続けて短い相づちになり、相手から質問や話題を広げる動きも弱まっている場合は、会話への参加が下がった可能性を考えてください。返信速度だけで判断せず、追撃や説得、追加の誘いをせずに待つか、自然に会話を閉じてください。"
            "断り・拒否があれば stop とし、押し直す提案をしないでください。迷い・曖昧さ・返答待ちは wait または continue にしてください。"
            "actionでinviteを選ぶ場合はinvite_exampleを必ず埋め、返信候補とは別に短く低圧で断りやすい誘い方の例を1つ示してください。人目のある公共の場所を使い、連絡先交換を提案しないでください。invite_exampleの文面にも駅前やカフェなど公共の場所だと分かる表現を含めてください。"
            "具体的な店名や日時を会話にないのに作らないでください。共通の活動に合う公共の場所を一般的に示せない場合はinviteを選ばないでください。"
            "会話にない自分の体験・予定・意向を事実として足さないでください。自分が見ていない写真を見た前提にしないでください。"
            "invite以外のactionではinvite_exampleを必ずnullにしてください。"
            "会話上の根拠が足りない場合はstrategyを省略してください。"
            f"返信候補は必ず{candidates}件だけ作ってください。\n"
            f'出力形式: {format_tapple_output_contract(candidates)}'
            f"。{tapple_strategy_contract_guidance()}戦略カードの内容は会話方針の参考情報であり、そのまま送信する返信候補ではありません。"
        )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_instruction},
    ]


def build_revision_messages(
    *,
    system_prompt: str,
    chat_history_text: str,
    condition: str,
    original_generated: str,
    revision_instruction: str,
    strategy_mode: str = "none",
    candidates: int = 3,
) -> list[dict[str, str]]:
    """修正して再生成するためのメッセージリストを組み立てる。"""
    _override_note = (
        "\n\n【再生成指示】前回の返信案と内容・表現が重複しないよう書き換えること。\n"
        "【USER LEARNED STYLE PROFILE】ユーザー本人の実績スタイルを維持すること。\n"
        "架空の自己開示・事実捏造の禁止を厳守してください。"
    )
    revised_system = f"{system_prompt}{_override_note}"

    if revision_instruction.strip():
        revision_count = candidates if strategy_mode == "tapple" else 3
        directive_text = f"前回の返信案を以下の指示に従って修正し、{revision_count}案再生成してください:\n修正指示: {revision_instruction.strip()}"
    else:
        revision_count = candidates if strategy_mode == "tapple" else 3
        directive_text = (
            f"前回の返信案（{original_generated.strip() or '前回案'}）とは異なる切り口・会話展開で{revision_count}案再生成してください。\n"
            "単に言い換える（単語や語尾を少し変えるだけ）ことは厳禁です。\n"
            "会話の戦略・切り口そのものを大きく変更した新しい3案を作成してください。"
        )

    user_content = (
        f"【REPLY DIRECTIVE】\n{condition or '(指定なし)'}\n\n"
        f"{directive_text}\n\n"
        "※絶対ルールを厳守してください。前回の返信案と内容・表現が重複しないよう書き換えてください。\n\n"
        f'出力は必ず JSON形式の {{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}} で出力してください。'
    )

    if strategy_mode == "tapple":
        user_content = user_content.replace("3案", f"{candidates}案")
        user_content = user_content.replace(
            '{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}',
            format_tapple_output_contract(candidates),
        )
        user_content += (
            "\n\n【タップル会話戦略】返信候補とは別に、CHAT HISTORYの相手発言だけを根拠に"
            "continue / clarify / invite / wait / stop の方針をstrategyとして追加してください。"
            "inviteは相手の同意ではなく、こちらから会うことを低圧に打診するのが会話上適切という判断です。"
            "具体的な共通の活動や場所への関心が相手の発言にあり、その話題で相手も会話を広げている場合は、まだ会う同意がなくても断りやすい誘いを提案できます。直前の相づちだけでは誘わないでください。"
            "evidenceは会話からの完全一致抜粋のみ。返信速度、短い相づち、「いいですね」だけの反応、曖昧な好意は誘う根拠にしないでください。"
            "安全面への不安や相手を信頼できるか分からないという懸念が示されている場合は、参加意思があってもinviteではなくwaitまたはclarifyを選んでください。"
            "直近の相手発言が続けて短い相づちになり、相手から質問や話題を広げる動きも弱まっている場合は、会話への参加が下がった可能性を考えてください。返信速度だけで判断せず、追撃や説得、追加の誘いを避けてwaitまたは自然な会話終了を選んでください。"
            "断りがあればstop。actionでinviteを選ぶ場合はinvite_exampleを必ず埋め、返信候補とは別に短く低圧で断りやすい誘い方の例を1つ示してください。"
            "人目のある公共の場所を使い、連絡先交換を提案しないでください。具体的な店名や日時を会話にないのに作らないでください。"
            "共通の活動に合う公共の場所を一般的に示せない場合はinviteを選ばないでください。invite以外のactionではinvite_exampleを必ずnullにしてください。"
            f"返信候補は必ず{candidates}件だけ作ってください。"
            f'形式: {format_tapple_output_contract(candidates)}'
            f"。{tapple_strategy_contract_guidance()}"
        )

    return [
        {"role": "system", "content": revised_system},
        {"role": "user", "content": f"【CHAT HISTORY】\n{chat_history_text}"},
        {
            "role": "assistant",
            "content": original_generated if original_generated else "(返信案)",
        },
        {
            "role": "user",
            "content": user_content,
        },
    ]
