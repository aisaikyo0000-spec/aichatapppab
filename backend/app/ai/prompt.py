"""AIへ渡すプロンプトの組み立て（Conversation-Learned Reply System v3.1）。

【ゼロベース再構築 & 会話状態Ledger + 直近会話最終配置】
1. ROLE & CURRENT DIRECTIVE / REPLY DIRECTIVE（最優先命令・トーンHard Lock）
2. HARD INVARIANTS / CORE RULES（事実整合・話者分離・架空自己開示禁止・さん付け・MULTI-TOPIC・本人基本情報）
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
    """Return the current month and avoid inferring local weather from the calendar."""
    dt = dt or datetime.now()
    return f"{dt.month}月", "月だけを根拠に天候や気温を推測しない"


def classify_message_length(text: str) -> str:
    """相手メッセージの長さ区分を返す（Step 2: 簡易的な長さ適応用）。

    - short: 0〜20文字（返信の長さは話題・本人Gold・会話状況から決める）
    - medium: 21〜80文字（返信の長さは内容・本人Gold・会話状況から決める）
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
_INTENT_CLOSING = re.compile(
    r"今日は(?:このへん|この辺|ここまで|このあたり|この辺り)(?:で|に)|"
    r"そろそろ(?:寝る|休む|落ちる)|(?:じゃあ|では)また(?:ね|明日|今度)?|"
    r"また(?:明後日|明日|来週|来月|今度|いつか)?(?:連絡する|連絡します|話そう|話しましょう|"
    r"お話しよう|お話しましょう|会おう|会いましょう|ね)|今日はありがとう(?:ございました)?|"
    r"お先に失礼します"
)


# Step 3: Intent ごとの返信方針（強い参考情報。Hard Rule ではない）
_INTENT_POLICIES = {
    "question": (
        "相手の質問への回答を最優先すること。回答を無視して別の話題へ移らないこと。"
        "回答だけで自然に成立するなら、無理に追加質問しないこと。"
        "回答を先に返したうえで、直近の話題に沿い会話上の意味があり、相手が答えやすい質問なら続けてもよい。"
        "既知情報の聞き直しや、会話を続けるだけの質問は避けること。"
    ),
    "invitation": (
        "相手の提案・誘いに対する自分の意思を優先して返信すること。"
        "架空の予定や意思を作らないこと。"
    ),
    "answer_required": (
        "相手が求めている回答・確認を優先すること。"
    ),
    "closing": (
        "相手が会話を切り上げているので、短く自然に受け止めて終えること。"
        "新しい話題・質問・連絡時期の約束を足さず、相手が明示した次の機会だけ自然に受け止めること。"
    ),
    "report": (
        "相手は単純に情報共有している。無理に質問して会話を延長しないこと。"
        "短いリアクションだけでもよい。質問は情報共有だけでは必要としない。"
    ),
    "reaction": (
        "相手の感情・リアクションに自然に反応すること。質問は文脈に沿い会話上の意味がある場合に限り、相手が答えやすい形にすること。"
    ),
    "emotional_share": (
        "相手の感情共有に自然に反応し、内容と文脈から共感・感想・気遣い・自然な願いなどの焦点を選ぶこと。本人Goldは主に口調・距離感・文量を整える参考にする。"
        "助言・意見を求めていない感情共有では、理由や詳しい状況を尋ねない。会話上の目的があり、文脈に合い相手が答えやすい質問は、本人Goldの有無にかかわらず選んでよい。Goldは質問を許可する条件ではなく、選ぶ場合の距離感を整える参考にする。"
        "理由や状況を推測せず、毎回同じ労いの定型句に寄らないこと。"
    ),
}


def classify_counterpart_intent(text: str) -> str:
    """相手の直近メッセージの意図を決定論的に分類する（Step 3）。

    返り値: question / invitation / answer_required / closing / emotional_share / reaction / report。
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

    # 明示的な別れ・会話の終了。質問・誘い・要回答連絡があれば上位 intent を保つ。
    if _INTENT_CLOSING.search(t):
        return "closing"

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
    same_contact_reply_style_block: str = "",
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
    season_name, season_guidance = get_current_season_info(current_datetime)

    # Block 1: ROLE & CURRENT DIRECTIVE / REPLY DIRECTIVE
    directive_lines = [
        f"- 現在の月: {season_name}（参考情報）",
        f"  ※{season_guidance}。天候・気温・季節感は、会話で確認できる場合だけ触れること。",
        "- 本人の記号の好み: 「、」「。」は基本的に使わない。文意に合うときは「！」「？」や絵文字を使ってよく、何も付けずに終えてもよい。記号や絵文字を機械的に足さず、読みやすさと自然さを優先する。",
    ]
    if mode == "followup":
        directive_lines.append(
            "【追いメッセージ（再活性化・会話復活モード）】\n"
            "しばらく返信が途絶えている相手への候補を作ります。会話履歴から再開が自然と判断できる場合だけ話題をつなぎ、気まずさや返信のプレッシャーを強めません。明確な拒否や自然な終了がある場合は、新しい話題・質問で再開を迫らず、必要なら短く受け止めて終えます。\n"
            "※(追いメッセージ・反復回避) 未返信の質問と同じ情報を求める質問や、その細分化・言い換えも避ける。自分の直近メッセージの要約・言い換えをしない。プロフィールや過去の共有情報を使うかどうかは任意で、使う場合は確認済みの情報に限る。使える話題が一つでも、現在の会話に必要な内容と本人Goldの文量を反映し、短さだけを理由に一言へ縮めない。\n"
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
            "- トーン指定: 【ハイブリッド】完全敬語にも完全タメ口にも寄せず、本人Goldと同一相手への実績を基準に、丁寧さと親しみを自然に混ぜる。語尾を一律にです・ます調へ揃えず、敬語を足して相手との距離を遠ざけない。"
        )

    directive_text = "\n".join(directive_lines)

    b1_role = (
        "あなたはマッチングアプリの会話を支援する文章作成アシスタントです。\n"
        "ユーザー（自分）の代わりに、自然で好印象な返信文章を"
        + (
            "文脈に合う3案を作成します。\n\n"
        )
        + "【ROLE & CURRENT DIRECTIVE】\n【REPLY DIRECTIVE】今回の返信における最優先命令（Priority 1）\n"
        f"{directive_text}\n"
        "※ユーザーの具体的指示がある場合は必ず最優先（Priority 1）で反映してください。\n"
        "指示を無視して一般的な「無難な返信」を生成してはならない。\n"
        "「自然な返信にする」ことを理由に、ユーザーの具体的な指示を変更・省略してはならない。\n"
        "返信作成では、まず会話内容に適切に答え、確認できる事実だけを使う。次に明示された口調指定と本人Gold・学習済み文体を反映する。長さは内容と本人Goldから決め、短い一文で十分なら短くする。3案はそれぞれ送信できる形にするが、候補間の違いは自然に作れる範囲でよく、似た案が自然なら無理に話題・反応を変えない。明示された口調指定がない限り、一般的な敬語を足して本人らしさや相手との距離感を損なわない。\n"
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
        else "（事実の時間軸）今日・昨日・今夜などの時点や、出来事がすでに起きたという前提は、会話中で確認できる場合だけ使う。システム日時だけから相手の状況や行動を推測しない。\n"
    )
    content_choice_guidance = (
        "【REPLY CONTENT CHOICE】\n"
        "今回の発言の意図と必要な返答内容を先に決め、相手の明確な質問にはまず答える。\n"
        "相手の発言を単にオウム返し・言い換えで繰り返さず、自分からの自然な反応や回答を返す。\n"
        "本人Goldの文量傾向を参考にしつつ、今回の話題と必要な返答内容を優先し、自然に完結するなら短く返す。短い入力というだけで内容のある状態共有を一言に縮めず、Goldの長さに合わせるための水増しもしない。\n"
        "会話を続けるためだけの質問は加えない。確認や会話上の目的があり、相手が答えやすい質問は使ってよい。質問なしで自然に終えるならそこで終え、自然な終了時に新しい話題を足さない。\n"
        "複数案は、自然に異なる焦点がある場合だけ分ける。内容や長さ、質問の有無を機械的に変えない。\n"
        if mode != "followup"
        else ""
    )
    rule_number_offset = 1 if mode == "followup" else 0
    line_break_rule = (
        "8. 改行は読みやすさに応じて使うこと。短い返信に改行や追加文を強制せず、複数文も自然に読めるなら同じ行にまとめてよい。\n"
    )

    topic_guidance_rule = (
        f"{14 + rule_number_offset}. 追いメッセージでの話題の扱い: 話題を無理に広げず、質問は直近の話題に沿い会話上の意味がある場合に限って使う。プロフィールや確認済み情報への短い反応で終えてもよい。\n"
        if mode == "followup"
        else ""
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
        "5. 語彙は話題と文脈に合わせて自然に選ぶこと。特定のカタカナ語や接続語を一律に禁止しない。\n"
        "6. MULTI-TOPIC RULE: 相手が複数の話題を出している場合はメインの話題に絞って自然に展開すること。\n"
        "7. 本人の未確認の経験を尋ねる場合（行った・食べた・試したこと等）、好み（犬派か猫派か、映画が好きか等）、予定・空き状況、生活習慣（起床時刻等）を聞かれ、本人情報・本人側の会話履歴に答えがない場合は、返信案を作らずアプリ利用者への確認として [AI_QUESTION]質問内容[/AI_QUESTION] のみを出力すること。"
        "一般的な反応や相手の好みへの感想を、本人の経験・好みの回答として扱わないこと。アプリ利用者への確認文をチャット相手に送る返信案へ混ぜないこと。"
        "ただし『あれどうなった？』『それって何のこと？』のように会話上の参照先が不明な状況確認は別ケース。これは本人の経験を尋ねる依頼ではないため、アプリ利用者へ質問せず、相手に送る短い確認文を通常のJSON返信候補として作る。このケースでは [AI_QUESTION] を出力しない。\n"
        f"{line_break_rule}"
        f"{'9. 質問は任意: 質問を含めるかどうかは会話状況次第。相手が明確な質問をしている場合は必ず回答し、既出質問は繰り返さない。\n' if mode == 'followup' else ''}"
        "10. 文脈参照: 以前の会話で相手が話した内容（趣味・予定・出来事・好み・気持ち等）は、今回の話題に自然に関連し返信に役立つ場合だけ正確に参照する。プロフィールの細かい単語を唐突に持ち出して直近メッセージを無視せず、自然に終える場面では会話を続ける話題を足さない。\n"
        f"{flow_rule}"
        f"{content_choice_guidance}"
        f"{12 + rule_number_offset}. 本人のリアルな文体・口調の完全再現: 返信案は学習プロファイルおよび直近手入力実例の本人の文体・口調（語尾『〜ですよね！』『〜なんですよね笑笑』、一人称『僕』、笑や記号の入れ方）を忠実に再現すること。疑問文の文末の疑問符は必ず1つにすること。？？のような重ね付けはしないこと（厳守。Step 17-R6 loop）。\n"
        "   ※AIの定型文や説明口調に寄せず、本人の普段の口語と現在の会話に合わせる。特定の単語や言い回しを固定で禁止するのではなく、文全体が自然かで判断する。\n"
        f"{13 + rule_number_offset}. 季節・気候を推測しない: 月や地域だけで天候・気温を決めつけず、会話で確認できる情報がある場合だけ触れること。\n"
        f"{topic_guidance_rule}"
        f"{15 + rule_number_offset}. 定型的な報告口調を避ける: 返信冒頭が形式的な引用・説明にならないよう、本人の普段の口語と会話の流れに合わせる。特定の表現を一律禁止せず、自然な報告や引用は文脈に応じて使う。\n"
        f"{16 + rule_number_offset}. 表記は本人Goldや会話文脈に合わせ、漢字・ひらがなを機械的に固定しないこと。語句だけを禁止せず、相手が出した話題とのつながりを見て表現を選ぶ。\n"
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
        "- 「眠い」に「眠いですよね」だけのような単純な言い換えで終えない。短い労いや気遣いは自然な選択肢だが、毎回加える必要はなく、本人Goldと会話に合う反応を選ぶ。\n"
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
            ledger_lines.append("※（追いメッセージモード）未返信の質問や最後の会話の単なる言い換えは避けること。プロフィールや過去の共有情報を使うかどうかは任意。使える話題が一つでも、必要な内容と本人Goldの文量を反映し、話題を捏造したり短さだけで一言へ縮めたりしないこと。")
        else:
            if last_c_msg:
                ledger_lines.append(f"★（最優先返答対象）{counterpart_title}の直前の最新メッセージ:\n「{last_c_msg}」\n※この内容へ直接反応すること。毎回同じ共感・労いの書き出しを使わず、回答・感想・共感・本人Goldと一致する会話調など、現在の話題に自然な入り方を選ぶ。相手の発言をそのまま言い換えない。")
            ledger_lines.append(f"【COUNTERPART INTENT】\n{intent}\n（相手発言の意図分類。返信方針を決めるための強い参考情報であり、Hard Rule ではない。最終判断は会話履歴・本人実例・条件を総合して行うこと）")
            ledger_lines.append(f"- Intent別方針（{intent}）: {_INTENT_POLICIES[intent]}")
            if conversation_ledger.get("prev_self_ended_with_question"):
                ledger_lines.append(
                    "- 質問の扱い: 直前の本人返信は質問で終わっている。今回は質問なしでも成立する候補を優先し、"
                    "相手の明確な質問への回答や、未解決事項の確認に必要な質問は引き続き使ってよい。"
                )
            if unresolved:
                ledger_lines.append(f"- 相手からの直近質問（要回答）: {unresolved}")
        if asked_list:
            ledger_lines.append("- 既出質問・回答済み話題（再質問禁止）これまで自分が相手に聞いた質問:")
            for q in asked_list:
                ledger_lines.append(f"  ・{q}")
            if mode == "followup":
                ledger_lines.append("  ※上記質問の再質問はしないこと。新しい質問は無理に用意せず、直近の会話に沿い会話上の意味がある場合に限り自然に入れてよい。")
            else:
                ledger_lines.append("  ※上記質問の再質問は避けること。この一覧は質問を増やす指示ではない。直近の話題に沿い会話上の意味があり相手が答えやすい場合に限り、新しい質問を入れてよい。反応だけで自然なら質問なしで終えてよい。")
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

    same_contact_reply_style = same_contact_reply_style_block.strip()
    if same_contact_reply_style:
        if same_contact_gold_samples >= 5:
            local_style_priority = (
                "本人の十分な同一相手Goldに由来する傾向です。明示された口調指定がない場合は、"
                "現在の会話内容と安全性に反しない範囲で、Globalの一般的な丁寧語よりこの本人Gold傾向を優先してください。"
            )
        else:
            local_style_priority = (
                "同一相手Goldはまだ少数のため弱い参考情報です。Globalの本人Goldを基準にし、"
                "この傾向だけで距離感・口調・文量を切り替えないでください。"
            )
        policy_parts.append(
            "【USER'S SAME-CONTACT REPLY STYLE】\n"
            "この相手に本人が実際に送ったGoldから抽出した、本人自身の返信スタイルです。"
            "相手本人の書き方とは別の情報です。"
            f"{local_style_priority}\n"
            f"{same_contact_reply_style}"
        )

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
    if counterpart_length_tier in ("short", "medium", "long"):
        tier_label = {"short": "短文", "medium": "中文", "long": "長文"}[counterpart_length_tier]
        length_lines.append(
            f"【COUNTERPART MESSAGE LENGTH】相手の直近メッセージ: 約{counterpart_length_chars}文字（区分: {counterpart_length_tier}・{tier_label}）"
        )
    length_text = "\n".join(length_lines)
    cp_summary = counterpart_style_block.strip() or counterpart_style.strip()
    b7_counterpart = (
        f"【COUNTERPART STYLE ADAPTATION】\n【COUNTERPART WRITING STYLE】{counterpart_title}への適応\n"
        f"{cp_summary}\n"
        "相手の文体は模倣せず、温度感を距離感の補助情報として扱う。"
        if cp_summary
        else (
            f"【COUNTERPART STYLE ADAPTATION】\n【COUNTERPART WRITING STYLE】{counterpart_title}への適応\n"
            "相手の文体は模倣せず、温度感を距離感の補助情報として扱う。"
        )
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
            "※形式的な引用・説明の定型文に寄せず、本人の普段の口語に合わせる。漢字・ひらがなの表記はGoldと文脈に合わせる。\n"
            "※月や地域だけから天候・気温を推測せず、確認できる情報がある場合だけ季節の話題に触れてください。\n"
            "※質問は会話を続けるためだけに足したり、相手に返信負担を生む形にしたりしないこと。確認済みの話題に自然につながり、短く答えやすい質問は必要な場合に限ってよい。質問なしの短い反応も正当な選択肢。\n"
            "以下は候補の切り口です。3案をすべて異なる役割に機械的に割り当てたり、順番を固定したりする必要はありません。候補の違いを作るために話題や反応を無理に変えず、同じ話題から自然に成立する複数案を作ってよいです。同じ確認済み話題で短い反応を作ってよい一方、同じ情報を尋ねる質問を重ねないでください。事実を作ってまで切り口を分けてはなりません。自然に複数の切り口がある場合は、反応の焦点や言い方を変えて候補に幅を持たせてもよいです。質問は必要性がある案にだけ含め、複数の未解決点それぞれに質問が自然な場合は複数案に含めてかまいません。\n\n"
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
            "3. 語彙は話題と文脈に合わせて自然に選び、特定のカタカナ語を一律に禁止しないこと。\n"
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
            "【OUTPUT CONTRACT】\n【FINAL TASK】出力契約（独立した返信案・誤分割防止）\n"
            "1. 完全独立の3案（本人の文体再現）: 3案それぞれが単独でそのまま送信できる完成品であること。本人の語尾や一人称、テンポを忠実に再現すること。\n"
            "   ※1つの返信文を「案1=反応、案2=自己開示、案3=質問」のように3分割して出力することは厳禁です。\n"
            "2. 改行は読みやすさに応じて使うこと。短い返信や複数文に、文ごとの改行を強制しないこと。\n"
            "3. 定型的な報告口調を避ける: 形式的な引用・説明で始めず、本人の普段の口語に合わせる。個別の語句だけで機械的に判定せず、会話に自然な報告や引用は許容する。相手の発言をほぼ同じ意味で言い直しただけの返信は避け、自分の言葉で反応すること。\n"
            "4. 表記は本人Goldと文脈に合わせ、漢字・ひらがなを機械的に固定しないこと。\n"
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
    "4. 説明文ではなく実際のメッセージらしく聞こえるか？\n"
    "5. 本人の普段の言い方として自然か？"
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
        " repliesとstrategyは両方必須のトップレベル項目です。strategyは必須で、判断が難しい場合も省略せず、"
        "根拠不足ならaction=waitまたはstopとして、会話にある相手の発言をevidenceにしてください。"
        "invite_exampleはinvite時のみ文字列にし、それ以外のactionではnullにしてください。"
    )


def build_initial_generation_messages(
    *,
    system_prompt: str,
    chat_history_text: str = "",
    candidates: int = 3,
    mode: str = "normal",
    strategy_mode: str = "none",
) -> list[dict[str, str]]:
    """初回返信生成用のメッセージリストを組み立てる（全履歴の二重投入を廃止）。"""
    if mode == "followup":
        user_instruction = (
            "【追いメッセージ生成命令】\n"
            "【USER LEARNED STYLE PROFILE】および【SAME-CONTACT RECENT GOLD REPLIES】の本人の言葉遣い・語尾（『〜ですよね！』『〜なんですよね笑笑』『〜ですか？？』等）や一人称（『僕』）を忠実に再現してください。\n"
            "未返信の質問を繰り返したり、最後の会話を単に言い換えたりしないでください。未返信の質問と同じ情報を求める質問や、その細分化・言い換えも避け、自分の直近メッセージの要約・言い換えもしないでください。プロフィールや過去の共有情報を使うかどうかは任意です。使える話題が一つなら、無理に別の話題を捏造して3案を水増ししないでください。形式的な説明口調に寄せず、自然な日常会話で作成してください。\n"
            "表記は本人Goldと文脈に合わせ、接続語を一律に避けず、相手の話題と無関係な切り替えや並列質問を避けてください。\n"
            "自分の具体的な行動・経験は、CHAT HISTORYまたはGold実績に明記されている場合だけ使ってください。プロフィール情報は相手の好みを理解する参考であり、自分の行動・経験の根拠にはしません。確認できない訪問・飲食・予定・体験を作ってはいけません。根拠がなければ、相手の趣味への感想や軽いリアクションだけで成立させてください。\n"
            "プロフィールの好みから、所有・経験・具体的な選択傾向を推測しないでください。プロフィールにある趣味に触れること自体を返信の必須条件にしないでください。\n"
            "未確認の近況や生活状況を推測して言及しないでください。\n"
            "再開が自然な場合だけ話題を続け、明確な拒否や自然な終了がある場合は新しい話題・質問で再活性化を迫らないでください。必要なら短く受け止めて終えてください。\n"
            "時刻・日付・曜日・相手の近況が履歴で確認できない限り、挨拶や状況を推測で足さないでください。初回の挨拶や関係上必要な場面以外で定型句を足さないでください。\n"
            "プロフィールや共有済みの話題、軽いリアクション、相手の関心へのコメントなどから、文脈に合う返信を3案作ってください。プロフィールだけを根拠に本人と相手の関心が共通しているとは扱わず、本人の好み・経験はSELFの履歴やGold実績で確認できる場合だけ使ってください。候補の違いを作るために話題や反応を無理に変えず、同じ話題から自然に成立する複数案を作ってよいです。同じ確認済み話題で短い反応を作ってよい一方、意味が同じ言い換えだけの案や同じ情報を尋ねる質問を並べないでください。役割や順番を固定する必要はありません。違いを作るために架空の体験を足したり、相手の状況を決めつけたりしないでください。\n"
            "会話を続けるためだけの質問や、返信負担になる質問は避けてください。確認済みの話題に自然につながり、短く答えやすい質問は必要な場合に限って使ってかまいません。質問なしの短い反応も正当な選択肢です。質問の有無だけで候補を差別化せず、同じ話題を質問表現だけ変えて複数案にしないでください。質問は必要性がある案にだけ含め、複数の未解決点それぞれに質問が自然な場合は複数案に含めてかまいません。\n"
            "プロフィールに嗜好が書かれているだけなら、所有・飼育・経験・利用の有無を質問で確認しないでください。プロフィール情報には短い感想として触れられます。回答時点の日時だけを根拠に、履歴にない出来事の完了・経過を推測しないでください。未来・過去の時点や期間が会話で明示されていないなら、現在述べられている状況だけに応じ、出来事がすでに終わったとは仮定しないでください。過去形で近況を尋ねる場合は、その出来事の期間が実際に経過したと会話履歴から確認できるときに限ってください。自分の希望や予定も、履歴などに根拠がなければ作らないでください。\n"
            "3案はそのまま送れる自然な文にしてください。確認できる内容が少ない場合も、架空の内容や無関係な質問・自己開示で水増しせず、必要な内容と本人Goldに合わせた長さにしてください。内容への返答が自然に短く成立するなら、直接回答や一言の反応も短文で構いません。内容のある状態共有を入力の短さだけで一言に縮めないでください。\n"
            "『眠い』『疲れた』などの状態共有には、状況の言い換えだけでなく、本人Goldと文脈に合う感想・共感・気遣い・自然な願いなどから反応を選んでください。労いや共感を毎回の書き出しにせず、原因や勤務状況を推測したり、質問や助言で水増ししたりしないでください。Goldの文量は目安であり、内容への適切な返答を優先してください。\n"
            f"{_SILENT_SELF_CHECK}"
            f'出力は必ず JSON形式の {{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}} のみとし、説明・前置き・解説は一切出力しないでください。'
        )
    else:
        user_instruction = (
            "会話履歴と相手の最新発言を読み、意図・質問への回答・会話の流れに合う、そのまま送れる独立した返信案を3つ作ってください。以下の優先順位に従ってください。\n"
            "1. 今回の内容への適切な返答を決める。確認できない事実や相手の状況、本人の経験・好み・予定は作らない。プロフィールの嗜好は本人の経験の根拠にしない。\n"
            "2. 明示された口調指定があれば最優先する。指定がなければ本人の実際のGoldと学習済みスタイルを使い、十分な同一相手Goldがある場合は本人のその相手への口調・距離感を一般的な丁寧語より優先して反映する。Goldの返信例はテンポや構成を参考にし、語句・文面をコピーしない。\n"
            f"{_SILENT_SELF_CHECK}"
            f'出力は必ず JSON形式の {{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}} （または逆質問時の [AI_QUESTION]...[/AI_QUESTION]）のみとし、説明・前置き・解説は一切出力しないでください。各返信は必ずダブルクォートで囲み、クォートの欠落・日本語括弧「」・＝の混用をしないこと（Step 17-R2）。'
        )
    if strategy_mode == "tapple":
        user_instruction = user_instruction.replace(
            '{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}',
            format_tapple_output_contract(candidates),
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
            "会話上の根拠が足りない場合もstrategyを省略せず、安全側のwaitを選んでください。"
            "waitまたはstopの返信候補も会う提案を含めないでください。stopでは将来の誘いや再連絡を提案しないでください。"
            "相手が直前の誘いを受け入れた場合や具体的な代替日を提案した場合は、再度誘うinviteではなくcontinueを選び、必要な日程調整を明確に進めてください。"
            "直前の誘いを受け入れた場合は、各返信候補で自然に日程調整へ進んでください。たとえば都合のよい時期を尋ねるか、『日程はまた相談しよう』のように伝えます。全案を質問にせず、同じ質問を繰り返さないでください。自分の空き日や日時は会話にない限り作らないでください。"
            "相手が誘いに迷っている場合や安全面を懸念している場合はwaitを選び、不安を解消したふりや会う約束をしないでください。"
            "返信候補はstrategy.actionと矛盾させないでください。action=inviteの場合、各返信候補にも会話で根拠づけられる低圧な誘いを含め、短い相づちや関心の言い換えだけで終わる案を混ぜないでください。相手が同意した、または約束が確定したとは扱わないでください。action=continueやwaitなどinvite以外の場合に、相手の活動への関心はあるが一緒に行く意思が不明なら、その関心に自然に反応し、同行を前提とした表現や追加の誘いを避けてください。質問は会話上必要な場合だけにします。"
            "相手が挙げた活動や話題に直接つながる返信にし、無難な一般論へ話題をずらさないでください。会話にない店の雰囲気・特徴・人気・周辺の変化や、自分の習慣・感想を事実のように付け足さないでください。相手の発言を全文コピーする必要はありませんが、自然な短い反応までEchoとして避ける必要はありません。"
            "相手が場所や活動に関心を示したときは、直前の自分の発言にある関心や具体的な話題と結びつけて返してください。訪問した事実がないのに店の雰囲気を知っているように述べず、相手の希望を一緒に行く約束へ読み替えないでください。"
            "action=invite以外で相手が『行ってみたい』と話したときは、その希望を受け止め、対象への自然な反応か関連する短い問いで会話を返してください。本人の関心が履歴にあるなら共有してよく、相手に一人で行くよう勧めたり、会話にない店の特徴を想像したりしないでください。"
            "本人が直前の自分の発言で同じ場所や活動への関心を示しているなら、その既知の関心を一度だけ共有して会話のつながりを作ってください。相手の『いいですね』への同意だけで全案を終えたり、相手へ一人で行くよう勧めたりしないでください。"
            "候補返信にもstrategyと矛盾する誘い・日程確定を含めず、会話にない店の雰囲気・メニュー・評判を作らないでください。"
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
