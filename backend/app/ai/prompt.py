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
    "どこ", "いつ", "なに", "何時", "何時", "誰", "どれ", "どっち", "どちら", "なぜ", "どうして",
)
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
        "相手が話を続けたい可能性が高い場合のみ、自然な質問を検討すること。"
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
    counterpart_length_tier: str = "",
    counterpart_length_chars: int = 0,
) -> str:
    """8ブロック構成のシステムプロンプトを組み立てる（Conversation-Learned Reply System v3.8）。"""
    contact_name = contact.get("name") or "相手"
    raw_profile = (contact.get("profile") or "").strip()
    contact_profile = clean_contact_profile(raw_profile)
    season_name, season_restriction = get_current_season_info(current_datetime)

    # Block 1: ROLE & CURRENT DIRECTIVE / REPLY DIRECTIVE
    directive_lines = [
        f"- 現在の月: {season_name}",
        f"  ※(季節外れの嘘の禁止) 季節や気候に触れる場合のみ、現在の時期（{season_name}）と矛盾する表現{season_restriction}をつかないこと（※季節や天気の話題を無理に出す必要は一切ありません。日常会話や趣味、相手との自然なやり取りを最優先してください）。",
    ]
    if mode == "followup":
        directive_lines.append(
            "【追いメッセージ（再活性化・会話復活モード）】\n"
            "しばらく返信が途絶えている相手に対し、相手の「返信しなかった気まずさ」や「返信負担」を完全に消し去り、思わず反応したくなる復活専用の追いメッセージを作成します。\n"
            "※(直前の話題引きずり禁止・最重要) 直前で途切れた古い話題（会話の続き）をズルズル引きずって返信することは完全禁止です。過去の話題を掘り返さず、完全に新しい切り口・仕掛けでメッセージを作成してください。\n"
            "※(通常メッセージとの差別化) 通常の「相手の発言に質問して会話を広げる」トーンは厳禁。相手は一度返信を止めているため、普通の質問を送ると「また返信義務が来た」とスルーされます。相手が0秒で返せるフックや、思わず口を挟みたくなる仕掛けを作ること。\n"
            "※(本人の文体・言葉遣いの完全再現) 本人が普段送っているリアルなチャット言葉・語尾・テンポを忠実に再現すること。\n"
            "※(絶対厳守・催促の完全禁止) 「返信まだ？」「忙しい？」「返事待ってます」「既読スルー」等の催促・問い詰め表現は全案で完全禁止。\n"
            "※相手のプロフィールや過去の会話履歴（趣味、好きなもの、共有した話題等）をフックに自然な連絡口実を作ること。"
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
        "ユーザー（自分）の代わりに、自然で好印象な返信文章を異なる3つの会話展開で3案作成します。\n\n"
        "【ROLE & CURRENT DIRECTIVE】\n【REPLY DIRECTIVE】今回の返信における最優先命令（Priority 1）\n"
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
        "11. 追いメッセージの構成: 直前の話題を続けず、各案指定のフック（行動報告 / 軽快ツッコミ / 写真なし体験共有）に基づいた軽快な文章（2〜3行）で作成すること。\n"
        if mode == "followup"
        else "11. 返信の長さ・構造の自由選択: 相手の発言と会話状況に応じて、以下から適切な形を選ぶこと。短いリアクション / 短い共感 / 回答だけ / リアクション＋一言 / リアクション＋質問 / 自己開示 / 自己開示＋質問 / 話題継続 / 話題終了 / 軽い冗談。毎回質問する必要はなく、毎回自己開示する必要もなく、毎回話題を広げる必要もない。相手の発言が短い場合、短い返信を優先してよい。\n"
        "（Step 7 追記）相手メッセージが短文（short区分）の場合、相槌・共感・一言程度の短い候補を必ず1案以上含めること。3案は可能な場合に異なる会話戦略（短反応 / 少し展開 / 質問あり）を持たせること。ただし意味のある違いがない場合は無理に違わせないこと（不自然な差別化は不正）。\n"
    )

    b2_hard = (
        "【HARD INVARIANTS】\n【CORE RULES】絶対遵守ルール（違反厳禁）\n"
        f"1. 相手の呼称: 相手の名前を呼ぶ時は必ず「さん」付け（呼び捨て・あだ名禁止）。相手を呼ぶ際は必ず『{contact_name}さん』とする。\n"
        "2. 話者の完全分離（絶対厳守）: 相手の発言した体験・好み・近況（例: お酒が苦手、〇〇に行った、〇〇が好き等）を、自分が言ったかのように語ったり混同しないこと。また自分が話したことを相手が話したかのように扱わないこと。相手の最新発言に対して自分側のリアクションを返すこと。\n"
        "3. 架空自己開示の完全禁止: 架空の自己開示・事実捏造の禁止。ユーザーの設定や会話履歴にない体験（「小さい頃連れて行ってもらった」「普段よく行く」「俺も好き」等）や好みを勝手に捏造しないこと。\n"
        "4. 会話履歴の既出情報重複禁止: 相手が写真コメント等で既に答えた内容や、過去にすでに聞いたことを再質問したり、既に話した自己開示を初めてのように繰り返さない。\n"
        "5. 不自然なカタカナ語の禁止: 『リフレッシュ』等の不自然なカタカナ語は使用しないこと。\n"
        "6. MULTI-TOPIC RULE: 相手が複数の話題を出している場合はメインの話題に絞って自然に展開すること。\n"
        "7. 未知事項の逆質問: 相手からの質問で事実が不明な場合は、返信案を作らず [AI_QUESTION]質問内容[/AI_QUESTION] のみを出力すること。\n"
        "8. 1文1行改行の絶対遵守: 1文ごとに必ず改行を入れること。複数の文を改行なしで1行に続けてはならない（文章の改行は絶対ルール）。\n"
        "9. 質問は任意: 質問を含めるかどうかは会話状況次第であり、質問なしの短い返信（例: 「それはきついな」「いいな」）も正式な正常系として扱うこと。相手が明確な質問をしている場合は必ず回答すること（例: 「明日何時にする？」→「14時くらいで大丈夫」）。既出質問の繰り返しは禁止。\n"
        "10. 会話継続・文脈参照: 以前の会話で相手が話した内容（趣味・予定・出来事・好み・気持ち等）を会話履歴から正確に参照し、直前の相手メッセージの話題から自然に関連付けて会話を継続すること。プロフィールの細かい単語を唐突に持ち出して直前メッセージを無視してはならない。\n"
        f"{flow_rule}"
        f"12. 本人のリアルな文体・口調の完全再現: 返信案は学習プロファイルおよび直近手入力実例の本人の文体・口調（語尾『〜ですよね！』『〜なんですよね笑笑』『〜ですかね？？』『〜ですか？？』、一人称『僕』、笑や記号の入れ方）を忠実に再現すること。\n"
        "   ※（AI特有の定型構文の完全禁止）以下のような他人行儀で不自然なAI作文は完全禁止:\n"
        "     ×『〜を求めて行ってきました』『〜の時間は最高ですね』『〜思わず共有です』『〜で驚きました』『〜はお好きですか？』『〜はいかがでしょうか？』\n"
        "     〇本人が普段送っているリアルなチャット言葉（『〜行ってみました笑』『めっちゃ美味しかったです！』『〜行ったりします？』等）にすること。\n"
        f"13. 季節・気候の矛盾禁止（受動的ルール）: 季節や天候に触れる場合のみ、現在の時期（{season_name}）と矛盾する嘘（夏に『寒くなってきた』、冬に『暑いですね』等）をつかないこと。※季節や天気の話題を無理に出す必要は一切ありません。日常や趣味、相手とのやり取りを最優先してください。\n"
        "14. 相手の話題の完全深掘り（『ほかにも』『ほかに』『他に』『〜以外』『〇〇もいいですけど』の完全禁止・絶対遵守）: 相手が出した話題や好きなもの（例: お寿司、焼き鳥、ラーメン、カフェ等）から逃げて『ほかにも好きな料理はありますか？』『〜のほかに何が好き？』『〜以外だと何が好き？』『〇〇もいいですけど…』のように別の話題に横スライドする質問は『完全禁止』。必ず相手が出した話題そのもの（「何が一番美味しかったですか？」「おすすめのメニューはありますか？」「どこのお店に行ったんですか？」「お気に入りのお店とかあるんですか？」等）を素直に深掘りして話を広げること。\n"
        "15. 『〜とのこと』『〜と拝見』等の機械的AI表現の完全禁止（絶対遵守）: 『〇〇とのことですが』『〇〇とのこと』『〇〇と拝見しました』『〇〇と書かれていましたので』のような機械的で他人行儀な表現は『完全禁止』。本人が直接写真やメッセージを見て自然に話しかける口語（『〇〇なんですね！』『〇〇美味しそうですね！』）にすること。\n"
        "16. 自然な平仮名表記（絶対遵守）: 『何か』は漢字表記を禁止し、必ず平仮名で『なにか』と表記すること（例: 『なにかありますか？』『なにか食べに行きました？』）。※『ほかに』『ほかにも』を使った質問自体は禁止です。\n"
        "【USER INFO & KNOWLEDGE】\n【SELF & MY INFO & USER KNOWLEDGE】\n"
        f"- 本人基本情報: {my_info_text}\n"
        f"- 本人知識・体験メモ: {user_k_text}"
    )
    if self_prof_text and self_prof_text != "(未設定)":
        b2_hard += f"\n- 本人プロフィール（【SELF】）:\n{self_prof_text}"

    # Block 3: CHAT HISTORY / CONTACT & CHAT HISTORY (事実ソース)
    contact_info = f"相手のお名前: {contact_name}さん"
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
            ledger_lines.append("※（追いメッセージモード・話題引きずり禁止）直前で途切れた古い話題（会話の続き）は絶対に蒸し返さず、話題を完全に切り替えて新しいフックを作成してください。")
        else:
            if last_c_msg:
                ledger_lines.append(f"★（最優先返答対象）相手（{contact_name}さん）の直前の最新メッセージ:\n「{last_c_msg}」\n※まずこのメッセージ内容に対する反応・共感から返信を始めること。")
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
    if counterpart_length_tier in ("short", "medium", "long"):
        tier_label = {"short": "短文", "medium": "中文", "long": "長文"}[counterpart_length_tier]
        length_lines.append(
            f"【COUNTERPART MESSAGE LENGTH】相手の直近メッセージ: 約{counterpart_length_chars}文字（区分: {counterpart_length_tier}・{tier_label}）"
        )
        if counterpart_length_tier == "short":
            length_lines.append("- 相手が短文のため、短いリアクション・共感・一言回答を優先し、長い文章や無理な話題拡張は避けること。")
        elif counterpart_length_tier == "medium":
            length_lines.append("- 2〜3行程度の自然な返信を基本とすること。")
        else:
            length_lines.append("- 相手の内容に合わせた丁寧な返信をしてよい。")
        length_lines.append("※文字数のHard Limit ではない。回答に必要な長さは許容する。")
    length_text = "\n".join(length_lines)
    cp_summary = counterpart_style_block.strip() or counterpart_style.strip()
    if cp_summary:
        b7_counterpart = (
            f"【COUNTERPART STYLE ADAPTATION】\n【COUNTERPART WRITING STYLE】相手（{contact_name}さん）への適応\n"
            f"{cp_summary}\n"
            "- 相手発言のオウム返し・コピー禁止。自分のスタイルを土台にしつつ適応させること。\n"
            "- 相手が短文中心の場合、説明的な長文にせず短く返すこと。相手文の言い換え＋感嘆だけの返信は避け、自分の言葉で反応すること。"
        )
    else:
        b7_counterpart = (
            f"【COUNTERPART STYLE ADAPTATION】\n【COUNTERPART WRITING STYLE】相手（{contact_name}さん）への適応\n"
            "- 自分のスタイルを土台にしつつ、相手の温度感・文量に20〜30%程度自然に適応させること。\n"
            "- 相手発言のオウム返し・コピー禁止。\n"
            "- 相手が短文中心の場合、説明的な長文にせず短く返すこと。相手文の言い換え＋感嘆だけの返信は避け、自分の言葉で反応すること。"
        )
    if length_text:
        b7_counterpart += f"\n{length_text}"

    # Block 8: OUTPUT CONTRACT / FINAL TASK (誤分割完全阻止契約)
    if mode == "followup":
        b8_contract = (
            "【OUTPUT CONTRACT】\n【FINAL TASK】追いメッセージ出力契約（3つの厳密な役割分担・直前話題継続禁止・催促厳禁・本人の文体再現・1文1行改行）\n"
            "※直前の途切れた古い話題を続けることは厳禁。3案が似通った内容（似た質問や同じ話題）になることは不正とします。\n"
            "※本人が普段送っているリアルなチャット言葉・語尾（『〜ですよね！』『〜なんですよね笑笑』『〜ですかね？？』等）や一人称（『僕』）、笑の使い方を忠実に再現すること。\n"
            "※『〜とのこと』『〜と拝見』等の機械的AI表現は完全禁止。『何か』は漢字を使わず平仮名で『なにか』と表記すること。『ほかにも』『ほかに』『他に』『〜以外』『〇〇もいいですけど』の話題切り替えは禁止すること。\n"
            "※季節の話題を無理に入れる必要はありません（季節外れの嘘のみ禁止）。日常会話や相手の趣味・プロフを優先してください。\n"
            "必ず以下の「3つの明確に役割が異なる型」を配列の指定位置（replies[0], replies[1], replies[2]）通りに作成すること:\n\n"
            "■ replies[0] = 案1【行動報告・新事実フック】（相手の趣味・プロフに絡めた行動報告）\n"
            "  ・直前の話題は続けず、相手のプロフや趣味に関連して「実際に行った・食べた・試した」リアルな行動を報告する。\n"
            "  ・構成例: 「そういえば〇〇さんのプロフに書いてあったラーメン屋、気になって今日近く通ったんで行ってみました笑\nスープめっちゃ美味しかったですよ！\n〇〇さんは最近美味しいもの食べに行きました？？笑」\n\n"
            "■ replies[1] = 案2【軽快ツッコミ・ユーモアリセット】（クスッと笑えるツッコミで気まずさ解消）\n"
            "  ・重くならず、カラッとした明るいツッコミで返信放置の気まずさをゼロにする。真面目に心配しすぎず軽く。\n"
            "  ・構成例: 「〇〇さん、もしかして冬眠中ですか？笑笑\n最近仕事バタバタしてましたかね！\n休みの日はゆっくり休めそうですか？笑」\n\n"
            "■ replies[2] = 案3【写真なし体験・情景共有フック】（写真添付なしで情景が目に浮かぶ日常共有）\n"
            "  ・まるで美味しそうな写真や面白い写真を送っているかのような、具体的で鮮明な日常の一コマの情景描写・共有。\n"
            "  ・構成例: 「今日食べたハンバーグがめっちゃ美味しかったんですよね！笑\n肉汁すごくて当たりでした笑\n〇〇さんは最近当たりのお店とかありました？笑」\n\n"
            "2. 催促・問い詰めの完全禁止: 「返信まだ？」「忙しい？」「返信待ってます」等は厳禁。\n"
            "3. 不自然なカタカナ語の禁止: 『リフレッシュ』等のカタカナ語は使用せず、『気分転換』『息抜き』『癒やされる』等の自然な日本語を使うこと。\n"
            "4. 短文・低負担（2〜3行）: 相手の返信エネルギーを奪わない軽快な文量にすること。\n"
            "5. 1文1行改行の絶対遵守: 各案は必ず1文ごとに改行を入れて出力すること。\n"
            '6. 出力は必ず JSON形式の {"replies": ["案1の行動報告文章", "案2の軽快ツッコミ文章", "案3の写真なし情景共有文章"]} のみとし、説明や前置きは一切出力しないこと。'
        )
    else:
        b8_contract = (
            "【OUTPUT CONTRACT】\n【FINAL TASK】出力契約（候補の多様性と独立性・誤分割禁止・1文1行改行・質問任意）\n"
            "1. 完全独立の3案（本人の文体再現・候補の多様性）: 3案それぞれが単独でそのまま送信できる完成品であること。本人の語尾や一人称、テンポを忠実に再現すること。\n"
            "   ※1つの返信文を「案1=反応、案2=自己開示、案3=質問」のように3分割して出力することは厳禁です。\n"
            "   各案がそれぞれ独立した異なる会話展開（本命、別展開、切り口違い）を持つこと（候補の多様性と独立性）。\n"
            "   3案の構成を機械的に固定しないこと（例: 「案1=短いリアクション、案2=少し展開、案3=質問あり」のような固定パターンは禁止）。会話内容に応じて3案とも短くなることも許可する。\n"
            "   意味のある違いがある場合だけ違わせること。候補を無理に差別化して不自然な案を作ってはならない。\n"
            "2. 単なる語尾の差し替えではなく、それぞれ切り口や内容が異なること。\n"
            "3. 1文1行改行の絶対遵守: 各案は必ず1文ごとに改行を入れて出力すること。\n"
            "4. 質問は任意: 質問を含めるかは会話状況次第とし、質問なしの案も正式な正常系として扱うこと。『ほかにも』『ほかに』『他に』『〜以外』『〇〇もいいですけど』の話題逃げ・並列質問は完全禁止とし、質問する場合も相手が出した話題そのものを深掘りして広げること。相手が明確な質問をしている場合は回答を含めること。\n"
            "5. 『〜とのこと』『〜と拝見』等の機械的AI表現の完全禁止: 『〇〇とのことですが』『〇〇とのこと』『〇〇と拝見しました』等の他人行儀なAI表現は完全禁止し、自然なチャット口語（『〇〇なんですね！』『〇〇いいですね！』）にすること。\n"
            "6. 自然な平仮名表記: 『何か』は漢字を使わず平仮名で『なにか』と表記すること。\n"
            '7. 出力は必ず JSON形式の {"replies": ["案1の独立返信文章", "案2の独立返信文章", "案3の独立返信文章"]} のみとし、説明や前置きは一切出力しないこと。'
        )

    blocks = [b1_role, b2_hard, b3_history, b4_policy, b5_pairs]
    if b6_contrast:
        blocks.append(b6_contrast)
    blocks.extend([b7_counterpart, b8_contract])

    return "\n\n" + ("\n\n".join(blocks))


def build_initial_generation_messages(
    *,
    system_prompt: str,
    chat_history_text: str = "",
    candidates: int = 3,
    mode: str = "normal",
) -> list[dict[str, str]]:
    """初回返信生成用のメッセージリストを組み立てる（全履歴の二重投入を廃止）。"""
    if mode == "followup":
        user_instruction = (
            "【追いメッセージ生成命令】\n"
            "【USER LEARNED STYLE PROFILE】および【SAME-CONTACT RECENT GOLD REPLIES】の本人の言葉遣い・語尾（『〜ですよね！』『〜なんですよね笑笑』『〜ですか？？』等）や一人称（『僕』）を忠実に再現してください。\n"
            "直前で途切れた古い話題を続けることは厳禁です。また『〜とのこと』『〜と拝見』等の機械的AI表現や過度な季節・天気の話題は避け、自然な日常会話で作成してください。\n"
            "『何か』は平仮名『なにか』と表記し、『ほかにも』『ほかに』『〜以外』『〇〇もいいですけど』等の話題切り替えは避けてください。\n"
            "必ず以下の3つの明確に役割分担された型を配列の順番通りに出力してください:\n"
            "・replies[0]: 案1（行動報告・新事実フック: 相手の趣味・プロフに絡めて「実際に行った・食べた・試した」リアルな行動報告）\n"
            "・replies[1]: 案2（軽快ツッコミ・ユーモアリセット: 「もしかして冬眠中ですか？笑笑」「最近バタバタしてましたかね！」等のカラッとしたツッコミ＋労いで気まずさ解消）\n"
            "・replies[2]: 案3（写真なし体験・情景共有フック: まるで写真を送るような具体的で美味しそう/面白い日常の一コマの情景描写・共有）\n\n"
            "※3案が似通った普通のメッセージにならないよう、3つの役割を厳密に分けて作成してください。\n"
            f'出力は必ず JSON形式の {{"replies": ["案1の行動報告文章", "案2の軽快ツッコミ文章", "案3の写真なし情景共有文章"]}} のみとし、説明・前置き・解説は一切出力しないでください。'
        )
    else:
        user_instruction = (
            f"上記の会話履歴・相手の最新発言を踏まえ、【LEARNED USER RESPONSE POLICY】/【USER LEARNED STYLE PROFILE】および【SAME-CONTACT RECENT GOLD REPLIES】/【POSITIVE REPLY PAIRS】/【RETRIEVED USER REPLY PAIRS】の本人の文体・言葉遣い・語尾・記号（笑/！）を最優先で忠実に再現してください。\n"
            "『〜とのこと』『〜と拝見』等の機械的AI表現や他人行儀な敬語、過度な季節の話題は完全禁止です。『ほかにも』『ほかに』『他に』『〜以外』『〇〇もいいですけど』等の話題切り替え・並列質問は禁止し、質問する場合は相手が出した話題そのものを共感＋深掘りで自然に話を広げてください。『何か』は漢字にせず平仮名『なにか』としてください。\n"
            "質問・自己開示・話題拡張は毎回必須ではありません。相手の発言が短い場合は短い返信（例: 「それはきついな」「いいな」）も正式な正常系として許可します。相手が明確な質問をしている場合は回答を含めてください。\n"
            "架空の自己開示・事実捏造の禁止を厳守し、1つの返信を分割せず各案が単独で送信できる独立した完成品として3案作成してください。\n"
            f'出力は必ず JSON形式の {{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}} （または逆質問時の [AI_QUESTION]...[/AI_QUESTION]）のみとし、説明・前置き・解説は一切出力しないでください。'
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
) -> list[dict[str, str]]:
    """修正して再生成するためのメッセージリストを組み立てる。"""
    _override_note = (
        "\n\n【再生成指示】前回の返信案と内容・表現が重複しないよう書き換えること。\n"
        "【USER LEARNED STYLE PROFILE】ユーザー本人の実績スタイルを維持すること。\n"
        "架空の自己開示・事実捏造の禁止を厳守してください。"
    )
    revised_system = f"{system_prompt}{_override_note}"

    if revision_instruction.strip():
        directive_text = f"前回の返信案を以下の指示に従って修正し、3案再生成してください:\n修正指示: {revision_instruction.strip()}"
    else:
        directive_text = (
            f"前回の返信案（{original_generated.strip() or '前回案'}）とは異なる切り口・会話展開で3案再生成してください。\n"
            "単に言い換える（単語や語尾を少し変えるだけ）ことは厳禁です。\n"
            "会話の戦略・切り口そのものを大きく変更した新しい3案を作成してください。"
        )

    user_content = (
        f"【REPLY DIRECTIVE】\n{condition or '(指定なし)'}\n\n"
        f"{directive_text}\n\n"
        "※絶対ルールを厳守してください。前回の返信案と内容・表現が重複しないよう書き換えてください。\n\n"
        f'出力は必ず JSON形式の {{"replies": ["案1の返信文章", "案2の返信文章", "案3の返信文章"]}} で出力してください。'
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
