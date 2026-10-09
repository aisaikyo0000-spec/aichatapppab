"""AI返信生成・修正再生成API。

Frontendから外部AI APIを直接呼び出さず、必ずこのAPI経由で生成する。
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import re
import time
import unicodedata
from typing import Any

from fastapi import APIRouter, HTTPException

from .. import config, database, learning
from ..ai import factory, naturalness, prompt
from ..reply_policy import question_necessity
from ..ai.base import AIError
from ..ai.config import get_ai_config, get_contact_ai_config
from ..schemas import GenerateRequest, TappleStrategy

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
    match = re.fullmatch(
        r"\[AI_QUESTION\]\s*((?:(?!\[/AI_QUESTION\]).)+?)(?:\s*\[/AI_QUESTION\])?",
        t,
        re.DOTALL,
    )
    if match:
        q = match.group(1).strip()
        if q:
            return q
    # Some providers wrap the safe question in the API's JSON reply shape.
    # Accept only a single reply containing a complete question tag; never pull
    # a private question out of a multi-candidate sendable response.
    try:
        payload = json.loads(t)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(payload, dict) and set(payload) == {"replies"}:
        replies = payload.get("replies")
        if isinstance(replies, list) and len(replies) == 1 and isinstance(replies[0], str):
            nested = re.fullmatch(
                r"\[AI_QUESTION\]\s*((?:(?!\[/AI_QUESTION\]).)+?)(?:\s*\[/AI_QUESTION\])?",
                replies[0].strip(), re.DOTALL,
            )
            if nested and nested.group(1).strip():
                return nested.group(1).strip()
    return None


def _parse_replies_strict(
    raw: str, candidates: int, *, strategy_mode: str = "none"
) -> list[str]:
    """出力を厳格にパースする。newline fallback は完全排除。

    1. candidates == 1 の場合: 生テキスト（空以外）
    2. JSON形式: {"replies": [...]} または {"candidates": [...]} または [...]
    3. 【案1】【案2】【案3】または 案1: 案2: 案3: の形式
    4. 候補数が candidates と一致しない場合は空リスト（パース失敗）を返す。
    """
    t = _strip_code_fence(raw).strip()
    if not t:
        return []
    if candidates <= 1 and strategy_mode != "tapple":
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

    if candidates <= 1 and strategy_mode == "tapple":
        return []

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


_TAPPLE_DECLINE_RE = re.compile(
    r"(?:会いたくない(?!わけではない|わけではありません|わけじゃない|わけじゃありません|とは言えない|とは言えません|とは限らない|とは限りません)|"
    r"お会いしたく(?:は)?ない(?:です)?(?!わけではない|わけではありません|わけじゃない|わけじゃありません|とは言えない|とは言えません|とは限らない|とは限りません)|"
    r"お会いしたく(?:は)?ありません(?!わけではありません|とは言えません|とは限りません|とは思っていません|とは思ってない)|"
    r"会いたくありません(?!わけではありません|とは言えません|とは限りません|とは思っていません|とは思ってない)|"
    r"行きたくない(?!わけではない|わけではありません|わけじゃない|わけじゃありません|とは言えない|とは言えません|とは限らない|とは限りません)|"
    r"行きたくありません(?!わけではありません|とは言えません|とは限りません)|"
    r"(?:会いたい|行きたい).{0,12}(?:思わない|思いません|思っていない|思っていません|"
    r"思ってない|思ってません|思っていなかった|思ってなかった|"
    r"わけではない|わけじゃない|とは限らない|とは言えない)|"
    r"(?:会う|お会いする|行く).{0,8}つもりは(?:ない|ありません)|"
    r"(?:お会いする|ご一緒する|会うことは).{0,12}(?:難し(?!くない|くはない|くはありません|くありません|"
    r"かった|くなかった|くはなかった|くはありませんでした|くありませんでした|"
    r"ければ|いなら|いならば|いだったら|いであれば|"
    r"いと言われるかもしれ(?:ない|ません)|いとは言われるかもしれ(?:ない|ません)|"
    r"いかもしれ(?:ない|ません)|いとは言えません|いとは言い切れません|"
    r"いとは限りません|いわけではない|"
    r"いわけではありません|いわけじゃない|いわけじゃありません|いとは思わない|"
    r"いと思わない|いとは思いません|いと思いません|いとは思っていない|"
    r"いとは思っていません|いとは思ってない|いと思っていない|いと思っていません|"
    r"いと思ってない|いとは思えない|いとは思えません|いと思えない|いと思えません|"
    r"いと感じない|いと感じません|いことではない|いことではありません|"
    r"いことはない|いことはありません)|"
    r"無理(?!ではない|ではありません|じゃない|じゃありません|じゃなく|なら|ならば|だったら|であれば|"
    r"かもしれ(?:ない|ません)|"
    r"(?:だ)?とは思(?:いません|わない|っていません|ってない|えません)|(?:だ)?と思われたくない)|"
    r"厳し(?!くない|くありません|くはない|くはありません|かった|くなかった|くはなかった|"
    r"ければ|いなら|いならば|いだったら|いであれば|いかもしれ(?:ない|ません)|"
    r"いとは言われるかもしれ(?:ない|ません)|いと言われるかもしれ(?:ない|ません)|"
    r"いとは言えない|いとは言えません|"
    r"いとは限らない|いとは限りません|いわけではない|いわけではありません|"
    r"いわけじゃない|いわけじゃありません|いとは思っていない|いとは思っていません|"
    r"いとは思ってない|いと思っていない|いと思っていません|いと思ってない)|"
    r"(?:できな(?!くはない)|できません|できかねます)|したくな|"
    r"差し?控え(?:ます|たい|た|させて)|控え(?:ます|たい|た|させて)|"
    r"辞退|見送(?:ります|りたい|らせて)|致しかね|やめ(?:ます|たい|ておく|ておきます|ておきたい|よう)|"
    r"考えていな|ご?遠慮(?:します|いたします|したい(?:です)?|させてください|させていただきます|"
    r"させてもらいます|ください|願います|いただけますか|いただけませんか|"
    r"いただければと思います|いただきたい(?:です)?))|"
    r"会う(?:こと|の)(?:は|を)?考えられ(?:ません|ない)(?!わけではない|わけではありません)|"
    r"会う(?:のは|ことは)?(?:今は)?(?:会う)?気分"
    r"(?:ではありません|ではないです|じゃないです|ではない|じゃない)|"
    r"会うのは.{0,8}避けたい(?:です)?|"
    r"会う.{0,12}(?:難し(?!くない|くはない|くはありません|くありません|"
    r"かった|くなかった|くはなかった|くはありませんでした|くありませんでした|"
    r"ければ|いなら|いならば|いだったら|いであれば|"
    r"いと言われるかもしれ(?:ない|ません)|いとは言われるかもしれ(?:ない|ません)|いとは言えません|"
    r"いとは言い切れません|いかもしれ(?:ない|ません)|いとは限りません|"
    r"いわけではない|いわけではありません|"
    r"いわけじゃない|いわけじゃありません|いとは思わない|いと思わない|"
    r"いとは思いません|いと思いません|いとは思っていない|いとは思っていません|"
    r"いとは思ってない|いと思っていない|いと思っていません|いと思ってない|"
    r"いとは思えない|いとは思えません|いと思えない|いと思えません|いと感じない|"
    r"いと感じません|いことではない|いことではありません|いことはない|いことはありません)|"
    r"無理(?!ではない|ではありません|じゃない|じゃありません|じゃなく|なら|ならば|だったら|であれば|"
    r"かもしれ(?:ない|ません)|"
    r"(?:だ)?とは思(?:いません|わない|っていません|ってない|えません)|(?:だ)?と思われたくない)|"
    r"厳し(?!くない|くありません|くはない|くはありません|かった|くなかった|くはなかった|"
    r"ければ|いなら|いならば|いだったら|いであれば|"
    r"いかもしれ(?:ない|ません)|"
    r"いとは言われるかもしれ(?:ない|ません)|いと言われるかもしれ(?:ない|ません)|"
    r"いとは言えない|いとは言えません|"
    r"いとは限らない|いとは限りません|いわけではない|いわけではありません|"
    r"いわけじゃない|いわけじゃありません|いとは思っていない|いとは思っていません|"
    r"いとは思ってない|いと思っていない|いと思っていません|いと思ってない)|"
    r"(?:できな(?!くはない)|できません|できかねます)|したくな|"
    r"差し?控え(?:ます|たい|た|させて)|控え(?:ます|たい|た|させて)|"
    r"やめ(?:ます|たい|ておく|ておきます|ておきたい|よう)|"
    r"考えていな)|"
    r"(?:お会いできません(?!とは思えません|とは言い切れません|と感じません|ということはありません)|"
    r"お会いできない(?:です)?|"
    r"お会いできかねます)|"
    r"(?:お誘い(?:について)?|会う|お会いする|ご一緒する|デート|お出かけ)"
    r"(?:こと|の)?(?:は|を)(?:ちょっと)?"
    r"(?:ご?遠慮(?:します|いたします|したい(?:です)?|させてください|させていただきます|"
    r"させてもらいます|ください|願います|いただけますか|いただけませんか|"
    r"いただければと思います|いただきたい(?:です)?)|お断り(?:します|いたします|申し上げます|"
    r"したい(?:です)?|させてください|させていただきます|させてもらいます|"
    r"です(?=。|$)|(?=。|$)))|"
    r"今回は.{0,12}お断り(?:します|いたします|申し上げます|したい(?:です)?|"
    r"させてください|させていただきます|させてもらいます|です(?=。|$)|(?=。|$))|"
    r"(?:デート|お出かけ).{0,12}(?:難し|無理|できな|したくな)|"
    r"(?:会えない(?!わけではない|わけではありません|わけじゃない|わけじゃありません|とは言えない|とは言えません|とは限らない|とは限りません|かな|かも)|"
    r"会えません(?!か|わけではありません|とは言えません)|"
    r"行けない(?!わけではない|わけではありません|わけじゃない|わけじゃありません|とは言えない|とは言えません|とは限らない|とは限りません|かな|かも)|"
    r"行けません(?!か|わけではありません|とは言えません)|"
    r"空いていない(?!わけではない|わけではありません|わけじゃない|わけじゃありません|とは言えない|とは言えません|かも)|"
    r"空いてません(?!か|わけではありません|とは言えません)|"
    r"都合が合わない(?!わけではない|わけではありません|わけじゃない|わけじゃありません|とは言えない|とは言えません|かも)|"
    r"都合が合いません(?!か|わけではありません|とは言えません)|"
    r"都合がつかない(?!わけではない|わけではありません|わけじゃない|わけじゃありません|とは言えない|とは言えません|かも)|"
    r"都合がつきません(?!か|わけではありません|とは言えません)|"
    r"空いていません(?!か|わけではありません|とは言えません))|"
    r"(?:土曜|土曜日|日曜|日曜日|平日|週末|今週|来週|今月|来月|再来月|別の日|別日)"
    r".{0,12}(?:予定があって|予定があり|都合が悪|空いていない|空いてません|厳し)|"
    r"(?:お誘い|今回は).{0,8}(?:辞退|見送|控え)|"
    r"今回は.{0,8}(?:やめ|遠慮(?!なく))|"
    r"ごめんなさい.{0,16}(?:会|行)|今は.{0,8}(?:難し|無理|できな))"
)
_TAPPLE_COUNTERPROPOSAL_RE = re.compile(
    r"(?:土曜|土曜日|日曜|日曜日|平日|週末|来週|今週|今月|来月|再来月|その日).{0,12}"
    r"(?:難し|無理|会えな|会えません|行けな|行けません|空いていない|空いてません|"
    r"都合が合わな|都合が合いません|都合がつかな|都合がつきません|都合が悪|"
    r"予定があって|予定があり|予定が合わな)"
    r".{0,16}(?:でも|けど|が|なら|で|、).{0,12}"
    r"(?:土曜|土曜日|日曜|日曜日|平日|週末|来週|今週|今月|来月|再来月|別の日|別日)"
    r".{0,8}(?:なら|は).{0,8}"
    r"(?:大丈夫|会え(?:ます|る)|行け(?:ます|る)|空いて(?:います|ます|る)|"
    r"都合がつきます|都合がつく|都合が合います|都合が合う)|"
    r"(?:忙し|予定が合わな|都合が悪|会えな|行けな|難し|無理)"
    r".{0,16}(?:でも|けど|が|なら|で|、).{0,16}"
    r"(?:土曜|土曜日|日曜|日曜日|平日|週末|来週|今週|今月|来月|再来月|別の日|別日)"
    r".{0,8}(?:なら|は).{0,8}"
    r"(?:大丈夫|会え(?:ます|る)|行け(?:ます|る)|空いて(?:います|ます|る)|"
    r"都合がつきます|都合がつく|都合が合います|都合が合う)"
)
_TAPPLE_ACCEPTED_INVITATION_RE = re.compile(
    r"(?:ぜひ|喜んで).{0,12}(?:一緒に|行き|会い|大丈夫)|"
    r"一緒に.{0,8}(?:行きたい|行きましょう|会いたい|会いましょう)|"
    r"(?:行きたい|会いたい)(?:です|！|$)"
)
_TAPPLE_CONTRADICTORY_INVITE_RATIONALE_RE = re.compile(
    r"(?:意思を示して(?:いません|おらず)|"
    r"(?:意思がない|意思はない)(?!わけではない|わけじゃない|とは限らない|とは言えない)|"
    r"希望していません|曖昧さが残|とは言い切れない|と言い切れない|"
    r"とは言えない(?!わけではない)|"
    r"前向きではない|断って(?:いる|います|いた|る)|断った(?:可能性|かもしれ)|"
    r"断られて(?:いる|います|いた)|拒否して(?:いる|います|いた)|"
    r"拒否した(?:可能性|かもしれ)|会いたくない(?:と|って|様子)|"
    r"行きたくない(?:と|って|様子)|(?:会う|行く)のは難し)"
)
_TAPPLE_CONTRADICTORY_STOP_RATIONALE_RE = re.compile(
    r"(?:会いたい気持ち.{0,8}(?:強|ある)|会いたい意思|行きたい意思|"
    r"明確な参加意思|ぜひ.{0,8}(?:会い|一緒に行き)|一緒に行きたい)"
)
_TAPPLE_UNSUPPORTED_INTENT_INFERENCE_RE = re.compile(
    r"(?:会いたい気持ちがない|会いたい意思がない|"
    r"会いたくない(?!わけではない(?:です)?|わけではありません|わけじゃない(?:です)?|"
    r"わけじゃありません|とは限らない(?:です)?|とは限りません|"
    r"とは言えない(?:です)?|とは言えません)|"
    r"行きたくない(?!わけではない(?:です)?|わけではありません|わけじゃない(?:です)?|"
    r"わけじゃありません|とは限らない(?:です)?|とは限りません|"
    r"とは言えない(?:です)?|とは言えません))"
)
_TAPPLE_OBVIOUS_TOPIC_PIVOT_RE = re.compile(
    r"(?:ところで|話(?:は)?変わるけど|そういえば|ちなみに).{0,24}"
    r"(?:転職|就職|副業|投資|資格|仕事の相談|会社の相談|恋愛相談|悩み相談)|"
    r"(?:転職して|転職を考え|就職して|副業を始め|投資を始め|投資に興味|"
    r"資格を取|新しい会社に入|会社に入った|仕事の相談に乗って|"
    r"会社の話.{0,10}(?:相談|ある)|(?:会社|仕事).{0,10}相談に乗って|相談に乗ってください|"
    r"政治に興味|政治について|ゲームを始め|ゲームをしませんか|遊びませんか|"
    r"釣りを始め|おすすめの竿|釣り.{0,12}(?:ハマ|好き|趣味|行く)|引っ越)"
)
_TAPPLE_DISRESPECTFUL_REPLY_RE = re.compile(
    r"(?:自分勝手|わがまま|面倒くさい|しつこい|頭おかしい|意味わからない|"
    r"だからモテない|性格悪い|常識ない|最低(?:ですね|だね|な人|すぎ)|"
    r"死ね|死んで|消えて(?:ください|くれ|よ|ろ)|馬鹿|バカ|ばか|クズ|くず|キモい|"
    r"気持ち悪い|うざい|ウザい|頭悪すぎ|メンヘラ|地雷女|ブス|性格終わって|"
    r"(?:^|[。！？!?])クソ(?:$|[。！？!?]))"
)
_TAPPLE_UNVERIFIABLE_SAFETY_ASSURANCE_RE = re.compile(
    r"(?:絶対|必ず|間違いなく|確実に).{0,8}"
    r"(?:安全|安心して会え)(?!とは言えない|とは言えません|と言い切れない|と言い切れません)|"
    r"(?:安全|安心して会え).{0,12}(?:保証|確約)(?!でき(?:る|ます)?とは言えない|"
    r"できません|できない)|"
    r"(?:安全|危険).{0,8}(?:問題ありません|大丈夫です)(?!とは言えません|とは言えない)"
)
_TAPPLE_PAST_MEETING_REAFFIRMATION_RE = re.compile(
    r"(?:前回|前は|先日|以前|この前).{0,14}"
    r"(?:会えなかった|会えませんでした|会うのは無理だった|会うのは難しかった|会うのは厳しかった)"
    r".{0,16}(?:今度|次回|改めて).{0,8}"
    r"(?:会いたい|会え(?:ます|る)|一緒に行きたい)"
)
_TAPPLE_HISTORICAL_DECLINE_CONTEXT_RE = re.compile(
    r"(?:前は|以前は|昔は|かつて|前回は|以前).{0,24}"
    r"(?:と思ってい(?:ました|た)|と考えてい(?:ました|た)|"
    r"難しかった|厳しかった|無理だった)"
)


def _unqualified_tapple_decline_matches(text: str) -> list[re.Match[str]]:
    declines = list(_TAPPLE_DECLINE_RE.finditer(text))
    if not declines:
        return []
    counterproposals = list(_TAPPLE_COUNTERPROPOSAL_RE.finditer(text))
    third_party_declines = list(_TAPPLE_THIRD_PARTY_DECLINE_RE.finditer(text))
    reported_third_party_quotes = list(
        _TAPPLE_REPORTED_THIRD_PARTY_QUOTE_RE.finditer(text)
    )
    quoted_declines = list(_TAPPLE_QUOTED_DECLINE_RE.finditer(text))
    reported_declines = list(_TAPPLE_REPORTED_DECLINE_RE.finditer(text))
    past_reaffirmations = list(_TAPPLE_PAST_MEETING_REAFFIRMATION_RE.finditer(text))
    return [
        decline
        for decline in declines
        if not _is_negated_tapple_decline(text, decline)
        if not any(
            counter.start() <= decline.start() < counter.end()
            for counter in counterproposals
        )
        and not any(
            third_party.start() <= decline.start() < third_party.end()
            for third_party in third_party_declines
        )
        and not any(
            quote.start() <= decline.start() < quote.end()
            for quote in reported_third_party_quotes
        )
        and not any(
            quote.start() <= decline.start() < quote.end()
            for quote in quoted_declines
        )
        and not any(
            report.start() <= decline.start() < report.end()
            for report in reported_declines
        )
        and not any(
            reaffirmation.start() <= decline.start() < reaffirmation.end()
            for reaffirmation in past_reaffirmations
        )
        and not _has_reaffirmed_tapple_intent_after_decline(
            text, decline, quoted_declines
        )
        and not _has_current_tapple_intent_before_historical_decline(
            text, decline, quoted_declines
        )
        and not _has_linked_tapple_counterproposal(text, decline)
    ]


def _has_reaffirmed_tapple_intent_after_decline(
    text: str,
    decline: re.Match[str],
    quoted_declines: list[re.Match[str]],
) -> bool:
    for positive in _TAPPLE_INVITE_POSITIVE_RE.finditer(text, decline.end()):
        transition = text[decline.end() : positive.start()]
        if not re.search(r"(?:今は|現在は|今なら)", transition):
            continue
        if any(
            quote.start() <= decline.start() < quote.end()
            for quote in quoted_declines
        ):
            continue
        if _has_first_person_tapple_intent_evidence(
            text, positive.group(0), _TAPPLE_INVITE_POSITIVE_RE
        ):
            return True
    return False


def _has_current_tapple_intent_before_historical_decline(
    text: str,
    decline: re.Match[str],
    quoted_declines: list[re.Match[str]],
) -> bool:
    reported_quotes = list(_TAPPLE_REPORTED_THIRD_PARTY_QUOTE_RE.finditer(text))
    third_party_declines = list(_TAPPLE_THIRD_PARTY_DECLINE_RE.finditer(text))
    if any(
        quote.start() <= decline.start() < quote.end()
        for quote in [*quoted_declines, *reported_quotes, *third_party_declines]
    ):
        return False

    for positive in _TAPPLE_INVITE_POSITIVE_RE.finditer(text, 0, decline.start()):
        current_context = text[max(0, positive.start() - 20) : positive.start()]
        if not re.search(r"(?:今は|現在は|今なら)", current_context):
            continue
        if not _has_first_person_tapple_intent_evidence(
            text, positive.group(0), _TAPPLE_INVITE_POSITIVE_RE
        ):
            continue
        for historical_decline in _TAPPLE_HISTORICAL_DECLINE_CONTEXT_RE.finditer(
            text, positive.end()
        ):
            if (
                historical_decline.start() <= decline.start()
                and decline.end() <= historical_decline.end()
            ):
                return True
    return False


def _tapple_text_without_superseded_historical_declines(text: str) -> str:
    declines = list(_TAPPLE_DECLINE_RE.finditer(text))
    quoted_declines = list(_TAPPLE_QUOTED_DECLINE_RE.finditer(text))
    reported_quotes = list(_TAPPLE_REPORTED_THIRD_PARTY_QUOTE_RE.finditer(text))
    third_party_declines = list(_TAPPLE_THIRD_PARTY_DECLINE_RE.finditer(text))
    reported_declines = list(_TAPPLE_REPORTED_DECLINE_RE.finditer(text))
    attributed_spans = [
        *quoted_declines,
        *reported_quotes,
        *third_party_declines,
        *reported_declines,
    ]
    removals: list[tuple[int, int]] = []

    for decline in declines:
        if _is_negated_tapple_decline(text, decline):
            continue
        if any(
            span.start() <= decline.start() < span.end()
            for span in attributed_spans
        ):
            continue
        if _has_current_tapple_intent_before_historical_decline(
            text, decline, quoted_declines
        ) or _has_reaffirmed_tapple_intent_after_decline(
            text, decline, quoted_declines
        ):
            removals.append((decline.start(), decline.end()))

    if not removals:
        return text
    result = list(text)
    for start, end in removals:
        result[start:end] = " " * (end - start)
    return "".join(result)


def _has_linked_tapple_counterproposal(
    text: str, decline: re.Match[str]
) -> bool:
    if not any(
        marker in decline.group(0)
        for marker in ("難し", "厳し", "無理", "会えません", "お会いできません")
    ):
        return False
    continuation = text[decline.end() : decline.end() + 48]
    return bool(
        re.search(r"(?:が|けど|けれど|でも|。)", continuation)
        and not _TAPPLE_THIRD_PARTY_COUNTERPROPOSAL_RE.search(continuation)
        and _TAPPLE_DIRECT_COUNTERPROPOSAL_RE.search(continuation)
    )


_TAPPLE_DECLINE_NEGATION_SUFFIX_RE = re.compile(
    r"^(?:い)?(?:とは(?:言えない|言えません|言い切れない|言い切れません|限らない|限りません|思わない|思いません|"
    r"思っていない|思っていません|思ってない|思ってません|思えない|思えません|"
    r"感じない|感じません)|と(?:思わない|思いません|思えない|"
    r"思えません|感じない|感じません)|ということは(?:ない|ありません)|"
    r"わけでは(?:ない|ありません)|わけじゃ(?:ない|ありません)|"
    r"ことでは(?:ない|ありません)|ことは(?:ない|ありません))"
)


def _is_negated_tapple_decline(text: str, decline: re.Match[str]) -> bool:
    suffix = text[decline.end() : decline.end() + 28]
    return bool(_TAPPLE_DECLINE_NEGATION_SUFFIX_RE.match(suffix))


_TAPPLE_THIRD_PARTY_DECLINE_RE = re.compile(
    r"(?:友達|友人|同僚|家族|兄弟|兄|姉|弟|妹|父|母|両親|親戚|いとこ|従兄弟|従姉妹|"
    r"甥|姪|祖父|祖母|先輩|後輩|知人|別の人|他の人|ほかの人|彼氏|彼女)"
    r"から.{0,16}(?:お会いしたく(?:は)?ない|お会いしたく(?:は)?ありません|"
    r"会いたくない|会いたくありません|(?:お会いする|会う).{0,10}つもりは(?:ない|ありません)|"
    r"(?:お会いする|会う).{0,12}(?:難し|無理|控え|遠慮))"
    r".{0,16}(?:と言われ|って言われ|と聞|って聞)|"
    r"(?:友達|友人|同僚|家族|兄弟|兄|姉|弟|妹|父|母|両親|親戚|いとこ|従兄弟|従姉妹|"
    r"甥|姪|祖父|祖母|先輩|後輩|知人|別の人|他の人|ほかの人|彼氏|彼女)"
    r"(?:が|は).{0,24}(?:(?:お会いする|ご一緒する|会う|行く).{0,12}"
    r"(?:難し|無理|できな|控え|遠慮|つもりは(?:ない|ありません))|"
    r"(?:お?会いたくない|お?会いたくありません|お会いしたくない|お会いしたくありません|"
    r"行きたくない|行きたくありません))"
    r".{0,16}(?:と言って|って言って|と話して|って話して|と聞いて|って聞いて)"
)


_TAPPLE_QUOTED_DECLINE_RE = re.compile(
    r"[「『][^」』]{0,20}(?:お会いしたく(?:は)?ない|お会いしたく(?:は)?ありません|"
    r"会いたくない|会いたくありません|(?:お会いする|ご一緒する|会う|行く).{0,10}"
    r"(?:難し|無理|できな|控え|遠慮|つもりは(?:ない|ありません)))[^」』]{0,8}[」』]"
    r"(?:"
    r"(?:と|って)(?:(?:友達|友人|同僚|家族|相手|本人|知人|知り合い|先輩|後輩|同級生|"
    r"彼氏|彼女|元彼女?|元カレ|元カノ|別の人|他の人|ほかの人|人)(?:に|から))?"
    r"(?:言われ|聞かされ|聞き)|"
    r"(?:と(?:(?:友達|友人|同僚|家族|相手|本人|知人|知り合い|人)に)?"
    r"(?:私は|私が)?言(?:いました|った|って)|[。.!！?？])"
    r".{0,20}(?:会いたい|会ってみたい|一緒に行きたい|行きたい)"
    r")"
)
_TAPPLE_REPORTED_DECLINE_RE = re.compile(
    r"(?:会う|お会いする|ご一緒する|デート|お出かけ).{0,12}"
    r"(?:無理|難し|厳し|できな|控え|遠慮|お断り)"
    r".{0,12}(?:と|って)(?:(?:友達|友人|同僚|家族|相手|本人|知人|人)に)?"
    r"(?:言われ|聞かされ|聞き|言われる)"
)


_TAPPLE_DIRECT_COUNTERPROPOSAL_RE = re.compile(
    r"(?:今日|明日|土曜|土曜日|日曜|日曜日|平日|週末|"
    r"来週|今週|今月|来月|再来月|別の日|別日)"
    r".{0,8}(?:なら|は|に).{0,10}"
    r"(?:大丈夫|会え(?:ます|る)|行け(?:ます|る)|空いて(?:います|ます|る)|"
    r"都合がつきます|都合がつく|都合が合います|都合が合う)"
)


def _unresolved_tapple_decline_match(
    conversation_messages: list[dict[str, Any]], last_contact_index: int
) -> re.Match[str] | None:
    unresolved: re.Match[str] | None = None
    unresolved_is_date_specific = False
    for message in conversation_messages[: last_contact_index + 1]:
        if message.get("sender") != "contact":
            continue
        text = prompt.clean_chat_message_content(str(message.get("content") or ""))
        decline_matches = _unqualified_tapple_decline_matches(text)
        date_unavailability_matches = list(
            _TAPPLE_DATE_UNAVAILABILITY_RE.finditer(text)
        )
        for decline_match in decline_matches:
            is_date_specific = bool(
                any(
                    date_match.start() <= decline_match.start() < date_match.end()
                    for date_match in date_unavailability_matches
                )
            )
            if not is_date_specific or unresolved is None or unresolved_is_date_specific:
                unresolved = decline_match
                unresolved_is_date_specific = is_date_specific
        if (
            unresolved
            and not decline_matches
            and _has_first_person_tapple_invite_positive(text)
            and not _has_tapple_explicit_hesitation(text)
            and not _has_tapple_safety_concern(text)
        ):
            unresolved = None
            unresolved_is_date_specific = False
        elif (
            unresolved
            and unresolved_is_date_specific
            and not decline_matches
            and _TAPPLE_DIRECT_COUNTERPROPOSAL_RE.search(text)
            and not _TAPPLE_THIRD_PARTY_COUNTERPROPOSAL_RE.search(text)
            and not _TAPPLE_THIRD_PARTY_INTEREST_RE.search(text)
            and not _has_tapple_explicit_hesitation(text)
            and not _has_tapple_safety_concern(text)
        ):
            unresolved = None
            unresolved_is_date_specific = False
    return unresolved
_TAPPLE_INVITE_POSITIVE_RE = re.compile(
    r"(?:一緒に.{0,8}(?:行きたい|行こう|行きましょう|会いたい|会おう|会いましょう)|"
    r"(?:今度|近いうち).{0,8}(?:一緒に行きたい|会いたい|会いましょう)|"
    r"(?:会いたい|会ってみたい)(?:です|！|。|$)|会いましょう|誘って(?:ください|ね|！|$))"
)
_TAPPLE_ACTIVITY_INTEREST_RE = re.compile(
    r"(?:行ってみたい(?:です|！|。|$)|食べてみたい(?:です|！|。|$)|"
    r"見てみたい(?:です|！|。|$)|試してみたい(?:です|！|。|$)|"
    r"体験してみたい(?:です|！|。|$)|"
    r"気になって(?:います|ます|る)(?:ね|！|。|$)|"
    r"興味が(?:あります|ある)(?:ね|！|。|$)|また行きたい(?:です|！|。|$))"
)
_TAPPLE_SHARED_ACTIVITY_TERMS = (
    "カフェ", "喫茶", "コーヒー", "紅茶", "パンケーキ", "スイーツ", "ケーキ",
    "ランチ", "ディナー", "ごはん", "食事", "焼肉", "ラーメン", "映画", "ミステリー",
    "展示", "美術館", "水族館", "動物園", "遊園地", "ライブ", "音楽", "旅行", "温泉",
    "散歩", "公園", "スポーツ", "サッカー", "野球", "ゲーム", "読書", "小説", "文庫", "料理",
)
_TAPPLE_GENERIC_INTEREST_TERMS = frozenset(
    {"最近", "今日", "昨日", "週末", "休日", "今度", "興味", "関心", "体験", "経験", "趣味", "活動", "共通", "一緒", "時間", "場所", "ところ", "本当"}
)


def _extract_tapple_interest_terms(text: str) -> set[str]:
    """Extract hobby terms only when they act as a specific activity noun."""
    candidate_re = re.compile(
        r"[\u30A0-\u30FFー]{2,}|[\u3400-\u4DBF\u4E00-\u9FFF々]{2,}|"
        r"[A-Za-z][A-Za-z0-9+#.-]{1,}"
    )
    activity_predicate_re = re.compile(
        r"^(?:が|は|も|に|を|なら|とか|巡り|鑑賞|する|して|の)?"
        r".{0,2}(?:好き|興味|関心|気にな|ハマ|はま|楽し|よく.{0,2}"
        r"(?:行く|する|見る|食べる)|行きたい|行ってみたい|食べてみたい|"
        r"見てみたい|試してみたい|体験|したい|趣味|おすすめ)"
    )
    return {
        match.group(0)
        for match in candidate_re.finditer(text)
        if match.group(0) not in _TAPPLE_GENERIC_INTEREST_TERMS
        and activity_predicate_re.search(text[match.end() :])
    }


_TAPPLE_ACTIVITY_DISINTEREST_PREDICATE = (
    r"(?:好き(?:では|じゃ)(?:ありません|ない(?!わけ|こと))|"
    r"得意(?:では|じゃ)(?:ありません|ない(?!わけ|こと))|"
    r"苦手(?!(?:(?:では|じゃ)(?:ない|ありません)|なわけではない|なわけじゃない|"
    r"というわけではない|というほどではない))|"
    r"嫌い(?!(?:(?:では|じゃ)(?:ない|ありません)|なわけではない|なわけじゃない|"
    r"というわけではない|というほどではない))|"
    r"興味(?:が)?(?:ありません|ない(?!わけ|こと))|"
    r"行きたく(?:ありません|ない(?!わけ|こと))|"
    r"行かない(?!わけ|こと)|"
    r"行く気が(?:ありません|ない(?!わけ|こと))|"
    r"行かなくな(?:った|りました)|"
    r"気になりません|気にならない(?!わけ|こと))"
)
_TAPPLE_ACTIVITY_DISINTEREST_PREFIX = (
    r"(?:あまり|そんなに|全然|もう|最近は|最近|ちょっと)?"
)
_TAPPLE_ACTIVITY_DISINTEREST_RE = re.compile(
    rf"^(?:(?:(?:は|が|も|には|に|では|なら|だと)[、,：:\s]*)?"
    rf"{_TAPPLE_ACTIVITY_DISINTEREST_PREFIX}"
    rf"{_TAPPLE_ACTIVITY_DISINTEREST_PREDICATE}|"
    rf"(?:に|へ|を)?(?:行く|行ってみる|訪れる|見る|観る|食べる|する)"
    rf"(?:の|こと)(?:は|が)[、,：:\s]*"
    rf"{_TAPPLE_ACTIVITY_DISINTEREST_PREFIX}"
    rf"{_TAPPLE_ACTIVITY_DISINTEREST_PREDICATE})"
)
_TAPPLE_RENEWED_ACTIVITY_POSITIVE_RE = re.compile(
    r"(?:(?<!では)(?<!じゃ)(?:好き|得意)(?:です|だ|で|な|かも|と思います|と思う)?|"
    r"行きたい|行ってみたい|食べてみたい|見てみたい|試してみたい|"
    r"体験してみたい|気になって(?:います|ます|る)|興味が(?:あります|ある))"
)


def _has_recent_self_disinterest_in_tapple_activity(
    conversation_messages: list[dict[str, Any]],
    last_contact_index: int,
    activity_term: str,
) -> bool:
    self_texts = [
        prompt.clean_chat_message_content(str(message.get("content") or ""))
        for message in conversation_messages[:last_contact_index]
        if message.get("sender") == "self"
        and prompt.clean_chat_message_content(str(message.get("content") or ""))
    ]
    renewed_interest_re = re.compile(
        r"(?:また|今度|これから|今は|今でも).{0,8}"
        r"(?:好き|行きたい|行ってみたい|気になって|興味)"
    )
    is_disinterested = False
    for self_text in self_texts:
        events: list[tuple[int, bool]] = []
        for clause_match in re.finditer(r"[^。！？!?\n]+", self_text):
            clause = clause_match.group(0)
            for term_match in re.finditer(re.escape(activity_term), clause):
                following_text = clause[term_match.end() :]
                disinterest_match = _TAPPLE_ACTIVITY_DISINTEREST_RE.match(
                    following_text
                )
                if disinterest_match is not None:
                    disinterest_end = (
                        clause_match.start()
                        + term_match.end()
                        + disinterest_match.end()
                    )
                    events.append(
                        (
                            clause_match.start()
                            + term_match.end()
                            + disinterest_match.start(),
                            True,
                        )
                    )
                    later_text = self_text[disinterest_end:]
                    renewal_match = renewed_interest_re.search(later_text)
                    if renewal_match:
                        renewal_context = later_text[: renewal_match.end()]
                        other_terms = (
                            set(_TAPPLE_SHARED_ACTIVITY_TERMS)
                            | _extract_tapple_interest_terms(renewal_context)
                        ) - {activity_term}
                        renewal_tail = later_text[renewal_match.end() :]
                        renewal_is_negative = bool(
                            _TAPPLE_ACTIVITY_DISINTEREST_RE.match(renewal_tail)
                        )
                        if not renewal_is_negative and not any(
                            term in renewal_context for term in other_terms
                        ):
                            events.append(
                                (disinterest_end + renewal_match.start(), False)
                            )
                    continue

                positive_match = _TAPPLE_RENEWED_ACTIVITY_POSITIVE_RE.search(
                    following_text
                ) or _TAPPLE_ACTIVITY_INTEREST_RE.search(following_text)
                if positive_match:
                    events.append(
                        (
                            clause_match.start()
                            + term_match.end()
                            + positive_match.start(),
                            False,
                        )
                    )

        for _position, disinterest_event in sorted(events):
            is_disinterested = disinterest_event

    return is_disinterested


def _has_recent_shared_tapple_activity(
    conversation_messages: list[dict[str, Any]],
    last_contact_index: int,
    last_contact_text: str,
) -> bool:
    interest_match = _TAPPLE_ACTIVITY_INTEREST_RE.search(last_contact_text)
    if interest_match is None:
        return False

    clause_start = max(
        last_contact_text.rfind(mark, 0, interest_match.start())
        for mark in ("。", "！", "？", "!", "?", "\n")
    ) + 1
    clause_end_candidates = [
        index
        for mark in ("。", "！", "？", "!", "?", "\n")
        if (index := last_contact_text.find(mark, interest_match.end())) >= 0
    ]
    interest_clause = last_contact_text[
        clause_start : min(clause_end_candidates, default=len(last_contact_text))
    ]
    contrast_markers = (
        "とは言っても", "とはいうものの", "とはいえ", "とは言え", "ですが",
        "だけど", "けれど", "けど", "ものの", "一方で", "でも", "が、", "が,",
    )
    contrast_positions = [
        (interest_clause.rfind(marker), marker)
        for marker in contrast_markers
        if interest_clause.rfind(marker) >= 0
    ]
    if contrast_positions:
        contrast_position, marker = max(contrast_positions)
        interest_clause = interest_clause[contrast_position + len(marker) :]

    previous_contact = next(
        (
            (index, prompt.clean_chat_message_content(
                str(conversation_messages[index].get("content") or "")
            ))
            for index in range(last_contact_index - 1, -1, -1)
            if conversation_messages[index].get("sender") == "contact"
            and prompt.clean_chat_message_content(
                str(conversation_messages[index].get("content") or "")
            )
        ),
        None,
    )
    if previous_contact is None:
        return False
    previous_contact_index, previous_contact_text = previous_contact
    if not re.search(
        r"(?:好き|気にな|楽しみ|行ってみたい|食べてみたい|見てみたい|"
        r"はまって|おすすめ)",
        previous_contact_text,
    ) and not _TAPPLE_ACTIVITY_INTEREST_RE.search(previous_contact_text) and not any(
        mark in previous_contact_text for mark in ("？", "?")
    ):
        return False

    prior_self_texts = [
        prompt.clean_chat_message_content(str(message.get("content") or ""))
        for message in conversation_messages[previous_contact_index + 1 : last_contact_index]
        if message.get("sender") == "self"
    ]
    prior_contact_texts = [
        prompt.clean_chat_message_content(str(message.get("content") or ""))
        for message in conversation_messages[: last_contact_index]
        if message.get("sender") == "contact"
    ]
    candidate_terms = set(_TAPPLE_SHARED_ACTIVITY_TERMS)
    for prior_text in (*prior_contact_texts, *prior_self_texts, interest_clause):
        candidate_terms.update(_extract_tapple_interest_terms(prior_text))
    for term in sorted(candidate_terms, key=len, reverse=True):
        if term not in interest_clause or not any(term in text for text in prior_contact_texts):
            continue
        if _has_recent_self_disinterest_in_tapple_activity(
            conversation_messages, last_contact_index, term
        ):
            continue
        if term in previous_contact_text and any(term in text for text in prior_self_texts):
            return True
        if (
            any(mark in previous_contact_text for mark in ("？", "?"))
            and any(term in text for text in prior_self_texts)
        ):
            return True
    return False


def _has_tapple_relevant_invite_hedge(text: str) -> bool:
    activity_interest = _TAPPLE_ACTIVITY_INTEREST_RE.search(text)
    if activity_interest:
        conditional_before_interest = text[: activity_interest.start()]
        if re.search(
            r"(?:もし.{0,12}|(?:予定|都合|時間|タイミング|機会).{0,12})"
            r"(?:たら|れば|かも|かな)",
            conditional_before_interest,
        ):
            return True
        return _TAPPLE_INVITE_HEDGE_RE.search(text[activity_interest.end() :]) is not None

    return _has_tapple_post_acceptance_hedge(text)


_TAPPLE_INVITE_HEDGE_RE = re.compile(
    r"(?:たら|れば|かも|かな|いつか|できたら|できれば|行けたら|会えたら|"
    r"行けない|会えない|難し|無理|今は|"
    r"まだ.{0,8}(?:難し|無理|会え|行け|迷|悩|考え|早い|未定|分から|わから|決められ|決めかね|不安|抵抗))"
)
_TAPPLE_EXPLICIT_HESITATION_RE = re.compile(
    r"^\s*(?:(?:正直|実は|私は|私も|本当は|ぶっちゃけ)(?:、|,)?.{0,8})?"
    r"(?:(?:まだ|少し|ちょっと).{0,8})?"
    r"(?:迷って|悩んで|考えさせて|決めかね).{0,20}"
    r"(?:一緒に行き|行きたい|会いたい|会いましょう|デート|会う)|"
    r"(?:会う|デート|会いたい|一緒に行く)"
    r"(?!日(?:程|時|取り|を|で|が|の|に|は|なら)|曜日|候補日|場所|店|"
    r"か.{0,4}(?:どこ|場所|店)|何を着|何を話|服装|話題|天気)"
    r"(?:かどうか|べきか|こと自体|ことに|ことを|のを|のは|か).{0,8}"
    r"(?:迷って|迷い|悩んで|悩み|考え|決め)|"
    r"(?:一緒に行き|行きたい|会いたい|会いましょう|デート|会う)"
    r"(?!日(?:程|時|取り|を|で|が|の|に|は|なら)|曜日|候補日|場所|店|"
    r"か.{0,4}(?:どこ|場所|店)|何を着|何を話|服装|話題|天気).{0,12}"
    r"(?:けど|けれど|でも|ものの|ですが|だが|が、|が,)、?"
    r"(?:(?:少し|ちょっと|まだ).{0,2})?"
    r"(?:迷って|迷い|悩んで|悩み|考えさせて|考えたい|決めかね)"
)
_TAPPLE_LOGISTICS_HESITATION_RE = re.compile(
    r"(?:会う日|日程|候補|曜日|どこで会う|会う場所|場所|お店|店|"
    r"何を着|何着|服装|何を話|話題)"
    r"[^。.!！?？\n]{0,8}(?:迷って|迷い|悩んで|悩み|考え)"
)
_TAPPLE_LATER_HESITATION_RE = re.compile(
    r"(?:(?:正直|実は|私は|私も|本当は|ぶっちゃけ|でも|それでも|まだ|少し|ちょっと)"
    r".{0,8})?(?:迷って|迷い|迷う|悩んで|悩み|悩む|考えさせて|決めかね)"
)
_TAPPLE_HESITATION_QUALIFIED_NEGATION_RE = re.compile(
    r"(?:迷って|悩んで)(?:い)?(?:は|が)?(?:い)?(?:ません|ないです|ない|おりません)"
    r"(?:とは言え|とは言い切れ|とは限ら|わけでは|わけじゃ|かもしれ)"
)
_TAPPLE_HESITATION_NEGATION_RE = re.compile(
    r"抵抗(?:感)?(?:は|が|を)?(?:ありません|ないです|ない|ございません)|"
    r"抵抗(?:感)?(?:を)?感じて(?:い)?ません|抵抗(?:感)?(?:を)?感じて(?:い)?ない(?:です)?|"
    r"緊張(?:は|が|を)?(?:ありません|ないです|ない|ございません|しません|しない(?:です)?)|"
    r"(?:迷って|悩んで)(?:い)?(?:は|が|と)?(?:い)?"
    r"(?:ません|ないです|ない|おりません)|"
    r"(?:は|が)?(?:迷い|悩み)(?:ません|ありません|ないです|ない|ございません)|"
    r"(?:迷い|悩み)(?:は|が)?(?:ありません|ないです|ない|ございません)|"
    r"(?:迷って|悩んで)いるとは言えません"
)
_TAPPLE_DIRECT_MEETING_HESITATION_RE = re.compile(
    r"(?<!どこで)(?:会う|会うこと|会うの)(?!日(?:程|時|取り|を|で|が|の|に|は|なら)|"
    r"場所|曜日|候補日|前に何を話)[^、,。.!！?？\n]{0,12}"
    r"(?:迷って|迷い|迷う|悩んで|悩み|悩む|ためら|気が進みません?)|"
    r"(?:会いたい|一緒に行きたい).{0,8}"
    r"(?:けど|けれど|でも|ものの|ですが|だが|が、|が,)、?"
    r"(?:(?:少し|ちょっと|まだ).{0,2})?"
    r"(?:迷って|迷い|迷う|悩んで|悩み|悩む|考えさせて|決めかね|気が進みません?)|"
    r"(?:会う|直接会う).{0,8}(?:ちょっと|少し)\s*(?:…|\.\.\.|．．．)$"
)
_TAPPLE_UNRELATED_HESITATION_TOPIC_RE = re.compile(
    r"(?:仕事|家族|友達|友人|同僚|職場|会社|勉強|転職先|転職|資格|進路|就職|"
    r"引っ越し|引越し|将来|健康|天気)"
    r"(?:が|は|も|に|を|のこと(?:で|を|に|が)?|について|に関して|で|から)"
    r"(?:(?!会う|会える|デート|対面|直接)[^。.!！?？\n、,，]){0,18}"
    r"(?:迷って|迷い|迷う|悩んで|悩み|悩む|考え)"
)
_TAPPLE_UNRELATED_SAFETY_TOPIC_RE = re.compile(
    r"(?:仕事|職場|会社|家庭|家族|勉強|転職)"
    r"(?:の|に関する|について(?:は|の|が|も)?|からの)?$"
)
_TAPPLE_SAFETY_CONTEXT_RE = re.compile(
    r"(?:会う|会える|デート|対面|直接|安全|信頼|信用|身元|素性)"
)
_TAPPLE_UNRELATED_SAFETY_CLAUSE_RE = re.compile(
    r"(?:仕事|職場|会社|現場|工場)"
    r"(?:(?!会う|会える|デート|対面|直接)[^。.!！?？\n、,，]){0,20}"
    r"(?:安全|不安|心配|怖|こわ|恐)"
)
_TAPPLE_UNRELATED_CONCERN_RE = re.compile(
    r"(?:(?:母|父|親|家族|友達|友人|同僚|明日|今日|来週|今週|週末)(?:の|について)?)?"
    r"(?:(?:仕事の)?面接|試験勉強|試験|テスト|体調|健康|予定|天気|雨|雪|気温|台風|風|暑さ|寒さ|予報)"
    r"[^。.!！?？\n、,]{0,12}(?:不安|心配|気になる|気掛かり|緊張)"
)
_TAPPLE_MEETING_REFERENCE_RE = re.compile(r"(?:会う|会える|デート|対面|直接)")
_TAPPLE_MEETING_LOGISTICS_CONCERN_RE = re.compile(
    r"(?:会う|デート)(?:の)?日程[^。.!！?？\n、,]{0,8}(?:不安|心配|気になる)|"
    r"(?:会う|デート)(?:の)?日[^。.!！?？\n、,]{0,8}"
    r"(?:雨|天気|予定|時間|都合|交通)[^。.!！?？\n、,]{0,8}"
    r"(?:不安|心配|気になる)"
)
_TAPPLE_UNRELATED_FEAR_OBJECT_RE = re.compile(
    r"(?:怖い|こわい|恐い)(?:映画|ホラー|話|夢|小説|作品|映像|ドラマ|ゲーム|漫画|マンガ)"
)
_TAPPLE_DECISION_TIME_REQUEST_RE = re.compile(
    r"(?:考える|考えさせて|決める|判断する).{0,8}(?:時間|猶予).{0,8}"
    r"(?:ほしい|ください|もらえ|いただけ|もらってもいい|いただいてもいい|"
    r"もらってもよい|いただいてもよい)|"
    r"(?:決める|判断する)前に.{0,8}(?:時間|猶予).{0,8}(?:ほしい|ください|もらえ|いただけ)|"
    r"(?:考えてから|考えた上で).{0,8}(?:返事|返信|回答).{0,8}(?:したい|させて)|"
    r"(?:少し|もう少し|ちょっと)?考えたい(?:です)?|"
    r"(?:返事|返信|回答).{0,8}(?:時間|猶予).{0,8}(?:ほしい|ください|もらえ|いただけ)|"
    r"(?:返事|返信|回答).{0,12}(?:もう少し|少し)?考えてから.{0,8}(?:したい|させて)|"
    r"(?:返事|返信).{0,16}待って.{0,8}"
    r"(?:もらえ|もらってもいい|いただけ|ください|ほしい|ほしいです)|"
    r"(?:返事|返信|回答).{0,8}(?:明日|明後日|今日の後|あとで|後で|後ほど|後日|来週)"
    r".{0,8}(?:します|する|しますね|するね|返します|返す|答えます|答える)|"
    r"(?:明日|明後日|今日の後|あとで|後で|後ほど|後日|来週)"
    r"(?:まで(?:には|に)?|には|は)?(?:返事|返信|回答).{0,8}"
    r"(?:します|する|しますね|するね|返します|返す|答えます|答える)|"
    r"(?:明日|明後日|今日の後|あとで|後で|後ほど|後日|来週)"
    r"(?:まで(?:には|に)?|には|まで)?待って.{0,8}"
    r"(?:もらえ|もらってもいい|いただけ|ください|ほしい|ほしいです)"
)
_TAPPLE_MEETING_DEFERRAL_RE = re.compile(
    r"(?:もう少し|もうちょっと|まずは|しばらく).{0,12}"
    r"(?:メッセージ|やり取り|話).{0,16}(?:してから|続けてから)|"
    r"(?:会う|会うのは|会うことは|デート).{0,8}(?:まだ早い|早いと思|抵抗|ためら)|"
    r"(?:先に|まずは).{0,8}(?:メッセージ|やり取り).{0,12}(?:して|続け)"
)
_TAPPLE_SAFETY_CONCERN_RE = re.compile(
    r"(?:会う|会える|デート|直接会う|会いに行く)[^。.!！?？]{0,20}"
    r"(?:不安|怖|こわ|恐|心配|抵抗|ためら|気が進まない|緊張|"
    r"ハードル(?:が)?高|勇気(?:が)?(?:出ない|出ません|ありません|ない|いります|いる))|"
    r"(?:会いたい|一緒に行きたい)[^。.!！?？]{0,12}[。.!！?？][^。.!！?？]{0,8}"
    r"(?:少し|ちょっと|まだ)?(?:不安|怖い|こわい|恐い|心配|抵抗|ためら|緊張)|"
    r"(?:会いたい|一緒に行きたい)[^。.!！?？]{0,4}(?:けど|けれど|が|ものの|でも)(?:、|,)?"
    r"[^。.!！?？、,]{0,10}(?:少し|ちょっと|まだ)?"
    r"(?:不安|怖|こわ|恐|心配|抵抗|ためら|緊張|ハードル(?:が)?高|"
    r"勇気(?:が)?(?:出ない|出ません|ありません|ない|いります|いる))|"
    r"(?:初対面|初めて会う|会ったことがない).{0,16}"
    r"(?:警戒|不安|怖|こわ|恐|心配|抵抗|ためら|慎重|用心|勇気(?:が)?(?:ない|いります|いる))|"
    r"(?:初めて|初対面).{0,10}(?:不安|怖|こわ|恐|心配|抵抗|ためら|慎重|用心)|"
    r"警戒しないと(?:いけない|ならない|だめ|危ない|危険|まずい|"
    r"ですよね|ですね|だよね|だね|ね|な|[。.!！?？、,]|$)|"
    r"(?:安全|安全性).{0,12}(?:かどうか|か).{0,12}(?:分から|わから|不明|判断できない)|"
    r"(?:安全面|安全性|安全).{0,10}(?:不安|心配|怖|こわ|恐)|"
    r"(?:安全面|安全性|安全).{0,12}(?:気にな|確認したい|気掛かり)|"
    r"(?:初対面|初めて会う|会ったことがない).{0,16}警戒|"
    r"警戒.{0,12}(?:初対面|初めて会う|会ったことがない|会う|デート|相手|あなた)|"
    r"(?:相手|あなた).{0,8}警戒|"
    r"(?:不安|怖|こわ|恐|心配).{0,20}(?:安全|信頼|信用|身元|素性)|"
    r"(?:信頼|信用|信じられ).{0,12}(?:できるか|まだ|難し|不安|心配|わから|分から|怖|こわ|恐)|"
    r"(?:信頼|信用|信じられ)(?:できない|できるか不安)|"
    r"(?:信じて(?:いい|よい)か|信じても大丈夫か).{0,12}"
    r"(?:不安|心配|怖|こわ|恐|迷|分から|わから)|"
    r"(?:安全|信頼|信用|あなた).{0,12}"
    r"(?:信じきれない|信じ切れない|信じきれません|信じ切れません|信じられない|信じられません)|"
    r"(?:身元|素性|相手のこと|相手について|相手がどんな人|どんな方|どんな人).{0,18}"
    r"(?:分から|わから|知ら|不安|心配|怖|こわ|恐)|"
    r"会ったことが(?:ない|なくて|なかった).{0,10}(?:不安|心配|怖|こわ|恐)"
)
_TAPPLE_SAFETY_CONCERN_NEGATION_RE = re.compile(
    r"抵抗(?:感)?(?:は|が|を)?(?:ありません|ないです|ない|ございません)|"
    r"抵抗(?:感)?(?:を)?感じて(?:い)?ません|抵抗(?:感)?(?:を)?感じて(?:い)?ない(?:です)?|"
    r"緊張(?:は|が|を)?(?:ありません|ないです|ない|ございません|しません|しない(?:です)?)|"
    r"ためらい?(?:は|が)?(?:ありません|ないです|ない|ございません|しません|しない(?:です)?)|"
    r"警戒(?:心)?(?:する)?(?:必要|こと|理由|必要性)(?:は|が)?"
    r"(?:ありません|ないです|ない|ございません)|"
    r"警戒(?:心)?(?:を)?しなくて(?:も)?"
    r"(?:大丈夫|いい|よい|問題ない|平気)(?:です)?|"
    r"(?:不安|怖|こわ|恐|心配)(?:い|く|さ)?(?:だと)?(?:では|じゃ|とは|は|を|に)?"
    r"(?:ありません|ないです|ない|ございません|して(?:い)?ません|して(?:い)?ない(?:です)?|"
    r"感じて(?:い)?ません|感じて(?:い)?ない(?:です)?|感じません|感じない(?:です)?|"
    r"思いません|思わない(?:です)?)|"
    r"警戒(?:心)?(?:を|は|が)?(?:しません|"
    r"しない(?:です)?(?!と(?:いけない|ならない|だめ|危ない|危険|まずい|"
    r"ですよね|ですね|だよね|だね|ね|な|[。.!！?？、,]|$))|"
    r"して(?:い)?ません|して(?:い)?ない(?:です)?|"
    r"(?:ありません|ないです|ない)"
    r"(?!と(?:危ない|危険|まずい|いけない|ならない))"
    r")"
)
_TAPPLE_SAFETY_CONCERN_QUALIFIED_NEGATION_RE = re.compile(
    r"(?:(?:不安|怖|こわ|恐|心配)(?:く)?(?:だと)?(?:では|じゃ|とは|は|を)?"
    r"(?:ありません|ないです|ない|ございません|して(?:い)?ません|して(?:い)?ない(?:です)?|"
    r"感じて(?:い)?ません|感じて(?:い)?ない(?:です)?)|"
    r"(?:抵抗(?:感)?|緊張|ためらい?)(?:は|が|を)?"
    r"(?:ありません|ないです|ない|ございません|しません|しない(?:です)?)|"
    r"警戒(?:心)?(?:を)?しなくて(?:も)?"
    r"(?:大丈夫|いい|よい|問題ない|平気)(?:です)?|"
    r"警戒(?:心)?(?:する)?(?:必要|こと|理由|必要性)(?:は|が)?"
    r"(?:ありません|ないです|ない|ございません)|"
    r"警戒(?:心)?(?:を|は|が)?(?:しません|"
    r"しない(?:です)?(?!と(?:いけない|ならない|だめ|危ない|危険|まずい))|"
    r"して(?:い)?ません|して(?:い)?ない(?:です)?|ありません|ないです|ない))"
    r"(?:"
    r"(?:とは|と)(?:言え|いえ|言い切れ|いいきれ)(?:ない|ません)|"
    r"とは限らない|とは限りません|わけではない|わけじゃない|"
    r"かも(?:しれない|しれません)|"
    r"(?:とは|と)?思え(?:ない|ません)|(?:とは|と)?思わ(?:ない|ないです|ないかも)"
    r")"
)


def _has_tapple_safety_concern(text: str) -> bool:
    unrelated_concerns = list(_TAPPLE_UNRELATED_CONCERN_RE.finditer(text))
    for match in _TAPPLE_SAFETY_CONCERN_QUALIFIED_NEGATION_RE.finditer(text):
        overlaps_unrelated_concern = any(
            unrelated.start() < match.end() and match.start() < unrelated.end()
            and not _TAPPLE_MEETING_REFERENCE_RE.search(
                re.split(
                    r"[。.!！?？、,，]|ですが|だけど|けれど|けど|ものの|でも",
                    text[: unrelated.start()],
                )[-1]
            )
            for unrelated in unrelated_concerns
        )
        if overlaps_unrelated_concern:
            continue
        nearby_context = text[max(0, match.start() - 16) : match.start()]
        if _TAPPLE_UNRELATED_SAFETY_TOPIC_RE.search(nearby_context) and not (
            _TAPPLE_SAFETY_CONTEXT_RE.search(nearby_context)
        ):
            continue
        return True
    without_denied_concerns = _TAPPLE_SAFETY_CONCERN_NEGATION_RE.sub("", text)
    without_unrelated_safety = _TAPPLE_UNRELATED_SAFETY_CLAUSE_RE.sub("", without_denied_concerns)
    without_unrelated_safety = _TAPPLE_MEETING_LOGISTICS_CONCERN_RE.sub(
        "", without_unrelated_safety
    )

    def remove_unrelated_concern(match: re.Match[str]) -> str:
        clause_prefix = re.split(
            r"[。.!！?？、,，]|ですが|だけど|けれど|けど|ものの|でも",
            match.string[: match.start()],
        )[-1]
        if _TAPPLE_MEETING_REFERENCE_RE.search(clause_prefix):
            return match.group(0)
        return ""

    without_unrelated_safety = _TAPPLE_UNRELATED_CONCERN_RE.sub(
        remove_unrelated_concern, without_unrelated_safety
    )
    without_unrelated_safety = _TAPPLE_UNRELATED_FEAR_OBJECT_RE.sub("", without_unrelated_safety)
    return _TAPPLE_SAFETY_CONCERN_RE.search(without_unrelated_safety) is not None


def _has_tapple_explicit_hesitation(text: str) -> bool:
    text_without_unrelated_hesitation = _TAPPLE_UNRELATED_HESITATION_TOPIC_RE.sub("", text)
    if _TAPPLE_HESITATION_QUALIFIED_NEGATION_RE.search(text_without_unrelated_hesitation):
        return True

    text_without_denied_hesitation = _TAPPLE_HESITATION_NEGATION_RE.sub(
        "", text_without_unrelated_hesitation
    )
    if _TAPPLE_DIRECT_MEETING_HESITATION_RE.search(text_without_denied_hesitation):
        return True

    accepted = _TAPPLE_ACCEPTED_INVITATION_RE.search(text_without_denied_hesitation)
    text_without_logistics_hesitation = _TAPPLE_LOGISTICS_HESITATION_RE.sub(
        "", text_without_denied_hesitation
    )
    sentences = re.split(r"[。.!！?？\n]+", text_without_logistics_hesitation)
    accepted_sentence_index = next(
        (
            index
            for index, sentence in enumerate(sentences)
            if _TAPPLE_ACCEPTED_INVITATION_RE.search(sentence)
        ),
        None,
    )
    standalone_hesitation = accepted_sentence_index is not None and any(
        _TAPPLE_LATER_HESITATION_RE.search(sentence)
        and not _TAPPLE_UNRELATED_HESITATION_TOPIC_RE.search(sentence)
        for sentence in sentences
    )
    return (
        _TAPPLE_EXPLICIT_HESITATION_RE.search(text_without_logistics_hesitation) is not None
        or standalone_hesitation
        or bool(
            accepted
            and _TAPPLE_DECISION_TIME_REQUEST_RE.search(text_without_denied_hesitation)
        )
        or _TAPPLE_MEETING_DEFERRAL_RE.search(text_without_logistics_hesitation)
        is not None
    )


_TAPPLE_SAFETY_RESOLUTION_RE = re.compile(
    r"(?:(?:会うこと|会うの|初対面|対面|安全面|安全).{0,16}"
    r"(?:不安(?:は|が)?(?:なくなりました|なくなった|消えました|消えた|"
    r"和らぎました|和らいだ|解消しました|解消した)|"
    r"心配(?:は|が)?(?:なくなりました|なくなった|消えました|消えた|解消しました|解消した)|"
    r"安心(?:しました|した)|大丈夫です|問題ありません|問題ないです|心配ありません)|"
    r"(?:不安(?:は|が)?(?:なくなりました|なくなった|消えました|消えた|"
    r"和らぎました|和らいだ|解消しました|解消した)|安心(?:しました|した))"
    r".{0,16}(?:会うこと|会うの|初対面|対面|安全面|安全))|"
    r"(?:(?:会うこと|会うの|直接会う|初対面|対面|安全面|安全).{0,12})"
    r"(?:不安|心配|怖|こわ|恐)(?:く)?(?:は|が)?"
    r"(?:ありません|ないです|ない|ございません)"
)
_TAPPLE_SAFETY_RESOLUTION_QUALIFIER_RE = re.compile(
    r"(?:とは言え|わけでは|わけじゃ|かもしれ|かも|かな|みたい|気がし(?:ます|た)?|そうにない|ていません|ていない|"
    r"ないとは|と思(?:う|います|った|いました)|ですか|でしょう|？|\?|まだ.{0,8}(?:不安|心配|安心)|"
    r"(?:不安|心配).{0,8}(?:残|続|ある))"
)
_TAPPLE_SAFETY_CONTRADICTORY_TAIL_RE = re.compile(
    r"(?:が|けど|けれど|でも|ものの).{0,20}(?:不安|心配|怖|恐|抵抗|迷|難し)|"
    r"(?:[。.!！?？\n]|^).{0,10}(?:まだ|やっぱり|少し|ちょっと).{0,6}"
    r"(?:不安|心配|怖|恐|抵抗|迷|難し)"
)


def _has_unresolved_tapple_safety_or_hesitation(
    conversation_messages: list[dict[str, Any]], last_contact_index: int
) -> bool:
    unresolved_safety = False
    unresolved_hesitation = False
    for message in conversation_messages[: last_contact_index + 1]:
        if message.get("sender") != "contact":
            continue
        text = prompt.clean_chat_message_content(str(message.get("content") or ""))
        resolution_match = _TAPPLE_SAFETY_RESOLUTION_RE.search(text)
        text_without_resolution = (
            text[: resolution_match.start()] + text[resolution_match.end() :]
            if resolution_match
            else text
        )
        concern_after_resolution = bool(
            resolution_match
            and (
                _has_tapple_safety_concern(text_without_resolution)
                or _TAPPLE_SAFETY_CONTRADICTORY_TAIL_RE.search(text_without_resolution)
            )
        )
        if concern_after_resolution:
            unresolved_safety = True
        elif resolution_match:
            clause_start = max(
                (text.rfind(boundary, 0, resolution_match.start()) + 1
                 for boundary in "。.!！?？\n"),
                default=0,
            )
            clause_ends = [
                text.find(boundary, resolution_match.end())
                for boundary in "。.!！?？\n"
                if text.find(boundary, resolution_match.end()) >= 0
            ]
            clause_end = min(clause_ends, default=len(text))
            resolution_clause = text[clause_start:clause_end]
            if not _TAPPLE_SAFETY_RESOLUTION_QUALIFIER_RE.search(resolution_clause):
                unresolved_safety = False
        elif _has_tapple_safety_concern(text):
            unresolved_safety = True

        has_first_person_invite_positive = any(
            not any(
                third_party.start() < positive.end()
                and positive.start() < third_party.end()
                for third_party in _TAPPLE_THIRD_PARTY_INTEREST_RE.finditer(text)
            )
            for positive in _TAPPLE_INVITE_POSITIVE_RE.finditer(text)
        )
        if _has_tapple_explicit_hesitation(text):
            unresolved_hesitation = True
        elif has_first_person_invite_positive:
            unresolved_hesitation = False

    return unresolved_safety or unresolved_hesitation


def _has_tapple_post_acceptance_hedge(text: str) -> bool:
    accepted = _TAPPLE_ACCEPTED_INVITATION_RE.search(text)
    return bool(accepted and _TAPPLE_INVITE_HEDGE_RE.search(text[accepted.end() :]))


_TAPPLE_THIRD_PARTY_PERSON_PATTERN = (
    r"(?:友達|友人|同僚|家族|兄弟|兄|姉|弟|妹|父|母|両親|親戚|"
    r"いとこ|従兄弟|従姉妹|甥|姪|祖父|祖母|先輩|後輩|知人|別の人|他の人|ほかの人|"
    r"彼氏|彼女)"
)
_TAPPLE_REPORTED_THIRD_PARTY_QUOTE_RE = re.compile(
    _TAPPLE_THIRD_PARTY_PERSON_PATTERN
    + r"(?:が|は).{0,12}[「『][^」』]{0,120}[」』](?:と|って)?"
    + r"(?:言って|話して|聞いて|伝えて)"
)


_TAPPLE_THIRD_PARTY_INTEREST_RE = re.compile(
    _TAPPLE_THIRD_PARTY_PERSON_PATTERN
    + r"(?:が|は).{0,20}(?:会いたい|会ってみたい|一緒に行きたい|行きたい).{0,12}"
    + r"(?:と言って|って言って|と話して|って話して|と聞|って聞|と言われ|って言われ)|"
    + _TAPPLE_THIRD_PARTY_PERSON_PATTERN
    + r"から.{0,16}"
    + r"(?:会いたい|会ってみたい|一緒に行きたい|行きたい).{0,12}"
    + r"(?:と言われ|って言われ|と聞|って聞|と伝えられ|と言って|って言って|と話して|って話して)|"
    + _TAPPLE_THIRD_PARTY_PERSON_PATTERN
    + r"(?:に|と)(?:あなた)?(?:会いたい|会ってみたい)|"
    + _TAPPLE_THIRD_PARTY_PERSON_PATTERN
    + r"(?:が|は|も)(?:(?:あなた|私)(?:に|と)(?:一緒に)?|一緒に|ぜひ)?"
    + r"(?:会いたい|会ってみたい|会いたがって|行きたい|行きたがって|行こう|会おう)|"
    + _TAPPLE_THIRD_PARTY_PERSON_PATTERN
    + r"と(?:一緒に)?(?:行きたい|行こう|会いたい|会おう)|"
    + _TAPPLE_THIRD_PARTY_PERSON_PATTERN
    + r"を.{0,6}誘って(?:ください|ね)|"
    + r"(?:行きたい|会いたい).{0,16}(?:って|と)(?:言って(?:た|いた|います)|聞いて(?:た|いた|います))"
)


def _has_first_person_tapple_intent_evidence(
    text: str, evidence: str, intent_pattern: re.Pattern[str]
) -> bool:
    third_party_matches = list(_TAPPLE_THIRD_PARTY_INTEREST_RE.finditer(text))
    evidence_start = 0
    while (evidence_start := text.find(evidence, evidence_start)) >= 0:
        for intent_match in intent_pattern.finditer(evidence):
            match_start = evidence_start + intent_match.start()
            match_end = evidence_start + intent_match.end()
            if not any(
                third_party.start() < match_end
                and match_start < third_party.end()
                for third_party in third_party_matches
            ):
                return True
        evidence_start += 1
    return False


def _has_first_person_tapple_invite_positive(text: str) -> bool:
    return _has_first_person_tapple_intent_evidence(
        text, text, _TAPPLE_INVITE_POSITIVE_RE
    )


_TAPPLE_THIRD_PARTY_COUNTERPROPOSAL_RE = re.compile(
    _TAPPLE_THIRD_PARTY_PERSON_PATTERN
    + r".{0,40}"
    r"(?P<date>土曜|土曜日|日曜|日曜日|平日|週末|来週|今週|今月|来月|再来月|別の日|別日).{0,16}"
    r"(?:大丈夫|会え(?:ます|る)|行け(?:ます|る)|空いて(?:います|ます|る)|"
    r"都合がつきます|都合がつく|都合が合います|都合が合う)"
)
_TAPPLE_FIRST_PERSON_COUNTERPROPOSAL_RE = re.compile(
    r"(?:私|自分)(?:は|なら|も).{0,12}"
    r"(?P<date>土曜|土曜日|日曜|日曜日|平日|週末|来週|今週|今月|来月|再来月|別の日|別日).{0,8}"
    r"(?:なら|は).{0,8}"
    r"(?:大丈夫|会え(?:ます|る)|行け(?:ます|る)|空いて(?:います|ます|る)|"
    r"都合がつきます|都合がつく|都合が合います|都合が合う)"
)
_TAPPLE_DATE_UNAVAILABILITY_RE = re.compile(
    r"(?:今日|明日|前回|前は|先日|土曜|土曜日|日曜|日曜日|平日|週末|"
    r"来週|今週|今月|来月|再来月|別の日|別日)"
    r".{0,12}(?:難し|無理|会えない|会えません|行けない|行けません|"
    r"予定があって|予定があり|予定が合わな|都合が悪|空いていない|空いてません|厳し)"
)
_TAPPLE_REINVITATION_RE = re.compile(
    r"(?:(?:今度|また|次|来週|今週(?:末)?|週末|今日|明日|いつか|改めて|"
    r"落ち着いたら|都合が合えば|タイミングが合えば|よかったら|もしよければ).{0,20})?"
    r"(?:一緒に|二人で|カフェ|喫茶店|ご飯|ごはん|食事|デート|お出かけ|お茶|映画|"
    r"会(?:う|えたら|いたら|いたい|わない|える)|行けたら|行きたい)"
    r".{0,16}(?:行きませんか|行きましょう|行こう(?:よ)?|会いませんか|会いましょう|"
    r"会おう(?:よ)?|会わない|しませんか|しましょう|しよう(?:よ)?|どう(?:ですか|かな)?|"
    r"嬉しい|うれしい|楽しみ|いいね|良ければ|よければ|たいな|できたら|できれば)"
    r"|(?:(?:今度|また|次|来週|週末|今日|明日|よかったら|もしよければ).{0,12})?"
    r"(?:会わない|行かない|デートしない|遊びに行かない|"
    r"(?:映画|カフェ|ご飯|ごはん|食事|デート|お茶|飲み|遊び)(?:を|に)?"
    r"(?:見ない|行かない|食べない|しない|飲まない)|"
    r"(?:お茶|飲み|ご飯|ごはん|食事)しない)(?:[？?]|$)"
    r"|(?:ぜひ|よかったら|もしよければ)?(?:一緒に)?"
    r"(?:お会いしましょう|お会いしませんか|会いましょう|会いませんか|会おう(?:よ)?|"
    r"行きましょう|行きませんか|行こう(?:よ)?)"
    r"(?:[。！!？?]|$)"
)
_TAPPLE_RECONSIDERATION_PRESSURE_RE = re.compile(
    r"(?:考え直|考えなお|もう一度.{0,8}考え).{0,12}"
    r"(?:ほしい|もらえ|くれ|うれしい|嬉しい|ください|ませんか|どう)"
    r"|(?:一度|一回)だけでも.{0,8}(?:会|会って|会えば)"
    r"|(?:一度|一回)(?:だけ|だけでも)[、,\s]*"
    r"(?:会(?:う|って|えば)|会う(?:こと|の).{0,8}(?:考え|検討))"
    r"|(?:少し|今回|一度|一回|最後|もう一度|もう一回)だけ.{0,8}"
    r"(?:会って|会うことを).{0,8}(?:ほしい|ください|くれ|もらえ)"
    r"|(?:ちょっと|少し).{0,4}だけ.{0,8}(?:会って|会うことを).{0,8}"
    r"(?:考え|検討).{0,12}(?:ください|くれ|もらえ|ほしい)"
    r"|そう言わずに.{0,12}(?:会って|会うこと).{0,8}(?:ください|くれ|もらえ|ほしい)"
    r"|もう少し.{0,8}(?:考え|検討).{0,12}(?:ください|くれ|もらえ|ほしい|ませんか)"
    r"|考え直.{0,12}(?:いただけると|いただけますか|いただけませんか).{0,8}"
    r"(?:幸い|嬉しい|うれしい)"
    r"|(?:(?:もう一度|もう一回|最後に|一度|一回).{0,8}"
    r"(?:チャンス|お願い).{0,8}(?:ください|くれ|もらえ|お願いします)?)"
    r"|(?:チャンス|お願い).{0,8}(?:ください|くれませんか|お願いします)"
)
_TAPPLE_INVITATION_EXAMPLE_PRESSURE_RE = re.compile(
    r"(?:断る(?:なんて|わけ(?:が)?)?ない|断れない)"
    r"|(?:絶対|必ず|当然|もう決まり).{0,12}(?:来て|来る|会おう|行こう|参加)"
    r"|(?:来る|来て(?:くれる|くださる)?|会う|会って|行く|行って|参加する)"
    r".{0,8}(?:よね|でしょう|だよね|でしょ)"
    r"|考え直(?:して|してよ|してください|してくれ)"
    r"|(?:" + _TAPPLE_RECONSIDERATION_PRESSURE_RE.pattern + r")"
)
_TAPPLE_SCHEDULING_PROPOSAL_RE = re.compile(
    r"(?:今度|また|次|来月|再来月|今月|来年|来週|今週(?:末)?|週末|今日|明日|土曜(?:日)?|日曜(?:日)?|"
    r"平日|いつか|改めて|落ち着いたら|都合が合えば).{0,16}"
    r"(?:そこ|一緒|会う|会え|会いましょう|会いませんか|行く|行け|行きましょう|"
    r"行きませんか|カフェ|ご飯|お茶|予定|都合|空いて|どう(?:ですか|かな)?|"
    r"大丈夫|行こう|会おう|しませんか|しよう)"
    r"|(?:都合|空き|予定).{0,12}(?:ありますか|どうですか|つきますか|合いますか)"
)
_TAPPLE_PUBLIC_PLACE_RE = re.compile(r"(?:カフェ|喫茶店|レストラン|飲食店|ホテルのロビー|ボルダリングジム|スポーツジム|スポーツ施設|体育館|ボウリング場|公共の場所|人通りのある場所|人の多い場所|商業施設|フードコート|駅前|公園)")
_TAPPLE_PRIVATE_PLACE_RE = re.compile(
    r"(?:自宅|お?うち(?:で|に|へ|集合|待ち合わせ|飲み)|お?家(?:で|に|へ|集合|待ち合わせ|飲み)|ホテル|客室|個室|スイートルーム|スイート|ルーム)"
)


def _is_tapple_invitation_example_safe(example: object) -> bool:
    """Require a public, non-coercive invitation example with no contact exchange."""
    if not isinstance(example, str) or not example.strip():
        return False
    return not (
        not _TAPPLE_PUBLIC_PLACE_RE.search(example)
        or _is_tapple_private_place_proposal(example)
        or _TAPPLE_CONTACT_EXCHANGE_RE.search(example)
        or _TAPPLE_INVITATION_EXAMPLE_PRESSURE_RE.search(example)
    )
_TAPPLE_NEGATED_PRIVATE_PLACE_RE = re.compile(
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事)|ホテル|ラブホテル|ラブホ|民泊|貸別荘|ゲストハウス|レンタルルーム|旅館|客室|個室|スイートルーム|スイート|ルーム)(?:には|は)行かない(?![？?])|"
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事)|ホテル|ラブホテル|ラブホ|民泊|貸別荘|ゲストハウス|レンタルルーム|旅館|客室|個室|スイートルーム|スイート|ルーム)(?:では|には)会わない(?![？?])|"
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事)|ホテル|ラブホテル|ラブホ|民泊|貸別荘|ゲストハウス|レンタルルーム|旅館|客室|個室|スイートルーム|スイート|ルーム)"
    r"(?:には|では|に|で|へ|は)?(?:.{0,12}(?:会うのは|会うのを|行くのは|行くのを))?"
    r"(?:行かず|行かないで|行きません(?!か)|泊まらず|泊まらないで|泊まらなくて|"
    r"会わず|会わないで|会いません(?!か)|使わず|使わないで|やめて|避けて|ではなく|じゃなく)"
)
_TAPPLE_SAFE_PRIVATE_REFERENCE_RE = re.compile(
    r"(?:家|お?うち|お?家|自宅)(?:の)?(?:近く|近所|付近)(?:の)?"
    r"(?:カフェ|喫茶店|レストラン|飲食店)|"
    r"ホテルの(?:カフェ|喫茶店|レストラン|飲食店|ロビー|フロント|エントランス|ラウンジ|バー)"
)
_TAPPLE_PRIVATE_PLACE_PROPOSAL_RE = re.compile(
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事)|ホテル|客室|個室|スイートルーム|スイート|ルーム)(?:に|で|へ)?"
    r"(?:泊まりませんか|泊まりましょう|泊まろう|泊まりたい|泊まってください|"
    r"泊まっていきませんか|泊まっていかない|泊まっていきましょう|泊まっていこう|"
    r"泊まっていきたい|泊まっていく|泊まってく|"
    r"泊まらない|一泊しませんか|一泊しましょう|一泊しよう)|"
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事))(?:に|へ|で)?"
    r"(?:来て|来ませんか|来ない(?!で|ね)|遊びに来|おいで(?:よ)?|お越し)|"
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事))(?:の中|のなか)(?:で|に|へ).{0,8}"
    r"(?:会いませんか|会いましょう|会おう|行きませんか|行きましょう|行こう)|"
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事))(?:で|に|へ).{0,16}"
    r"(?:映画を見|ご飯を食べ|食事し|飲み|会って|寄っ|過ご|遊びに来).{0,20}"
    r"(?:しませんか|ませんか|しよう|よう(?:よ)?|行こう|行きましょう|どう(?:ですか|かな))|"
    r"ホテル(?:に)?(?:行きませんか|行きましょう|行こう(?:よ)?|行きたい)|"
    r"ホテル.{0,10}(?:泊まり|泊まって|泊まりませんか|泊まろう).{0,8}"
    r"(?:会おう|会い|過ご|デート)|"
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事)|ホテル|客室|個室|スイートルーム|スイート|ルーム)(?:を.{0,8}待ち合わせ場所に|"
    r"(?:で|に|へ)?(?:集合|待ち合わせ|落ち合|合流))|"
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事)|ホテル|客室|個室|スイートルーム|スイート|ルーム)(?:で|に|へ)"
    r".{0,8}(?:会いませんか|会いましょう|会おう|行きませんか|行きましょう|行こう|"
    r"飲みませんか|飲みましょう|映画を見ませんか|過ごしませんか|話しませんか|"
    r"待ち合わせしませんか|待ち合わせしよう|待ち合わせしよ|どう(?:ですか|かな))|"
    r"ホテル.{0,8}(?:ラウンジ|客室|部屋)に(?:行きませんか|行きましょう|行こう)|"
    r"(?:家飲み|うち飲み|宅飲み)(?:しませんか|しない(?:？|\?)|しよ(?:う)?|しようよ)|"
    r"(?:宅飲み)(?:しませんか|しよう(?:よ)?|しない(?:？|\?)|しよ(?:う)?|どう(?:ですか|かな|[？?]))|"
    r"(?:自宅|部屋|お?うち|お?家(?!族|事)|家(?!族|事)|ホテル|ラブホテル|ラブホ|客室|個室|"
    r"スイートルーム|スイート|ルーム)(?:に|で|へ|の中で|の中に|のバーで)?"
    r".{0,36}(?:泊まろ|泊まっていく|お泊まり(?:しない|しませんか|しよう)|"
    r"映画でも?見ませんか|映画見ない(?:？|\?|$)|料理しませんか|"
    r"ご飯食べよ|ご飯食べない(?:？|\?|$)|食べよ|飲も(?:う)?|飲まない(?:？|\?|$)|"
    r"ゆっくりしよう|休もう|休まない(?:？|\?)|過ごそう|"
    r"寄っていかない|寄っていこう|寄ってよ|寄らない(?:？|\?|$)|"
    r"行きませんか|行きましょう|行こう|行かない(?:？|\?|$)|"
    r"会いませんか|会いましょう|会おう|会わない(?:？|\?)|"
    r"見ませんか|食べませんか|飲みませんか|しませんか|しよう(?:よ)?|よう(?:よ)?|"
    r"しない(?:？|\?|$)|どう(?:ですか|かな|[？?]))|"
    r"(?:民泊|貸別荘|ゲストハウス|レンタルルーム|旅館)(?:で|に|へ)"
    r".{0,36}(?:泊まろ|泊まって|会いませんか|会いましょう|会おう|行こう|"
    r"行きましょう|過ごそう|休もう|どう(?:ですか|かな|[？?]))|"
    r"(?:泊まっていく|泊まっていかない|泊まろ(?:う)?|お泊まり(?:しない|しませんか|しよう))"
    r"(?:[？?！!。]|$)"
)
_TAPPLE_CONTACT_EXCHANGE_RE = re.compile(
    r"(?:LINE|ライン|連絡先|電話番号|メールアドレス|メアド|SNS|インスタ|Instagram|"
    r"(?<![A-Za-z0-9])(?:X|Twitter|Discord|DM)(?![A-Za-z0-9])|カカオ|ID|QR)",
    re.IGNORECASE,
)
_TAPPLE_CONTACT_EXCHANGE_ACTION_RE = re.compile(
    r"(?:交換|教え|送っ|追加|登録|つな|繋|ID|QR|"
    r"やって(?:い)?(?:る|ます)|使って(?:い)?(?:る|ます)|やりとり|"
    r"話(?:そ|しません|しましょう|したい|そう)|し(?:ない|ません|ましょう|たい)|"
    r"連絡(?:を)?(?:取|と)り?(?:ません|ましょう|たい)|連絡しよ)"
)


def _is_tapple_contact_exchange_request(text: str) -> bool:
    return bool(
        _TAPPLE_CONTACT_EXCHANGE_RE.search(text)
        and _TAPPLE_CONTACT_EXCHANGE_ACTION_RE.search(text)
    )


def _is_tapple_private_place_proposal(text: str) -> bool:
    text_without_negated_mentions = _TAPPLE_NEGATED_PRIVATE_PLACE_RE.sub(" ", text)
    text_without_contextual_public_places = _TAPPLE_SAFE_PRIVATE_REFERENCE_RE.sub(
        " ", text_without_negated_mentions
    )
    return bool(_TAPPLE_PRIVATE_PLACE_PROPOSAL_RE.search(text_without_contextual_public_places))


def _parse_tapple_strategy(
    raw: str, conversation_messages: list[dict[str, Any]]
) -> TappleStrategy | None:
    """Parse only evidence-grounded strategy metadata; never gate reply generation."""
    try:
        payload = json.loads(_strip_code_fence(raw))
        if not isinstance(payload, dict) or not isinstance(payload.get("strategy"), dict):
            return None
        proposed = TappleStrategy.model_validate(payload["strategy"])
    except (json.JSONDecodeError, TypeError, ValueError, AttributeError):
        return None

    contact_messages = [
        prompt.clean_chat_message_content(str(message.get("content") or ""))
        for message in conversation_messages
        if message.get("sender") == "contact"
        and prompt.clean_chat_message_content(str(message.get("content") or ""))
    ]
    if not contact_messages:
        return None
    exact_evidence = [
        evidence for evidence in proposed.evidence
        if evidence and any(evidence in message for message in contact_messages)
    ]
    if len(exact_evidence) != len(proposed.evidence):
        return None

    last_contact = contact_messages[-1]
    last_contact_index = next(
        index
        for index in range(len(conversation_messages) - 1, -1, -1)
        if conversation_messages[index].get("sender") == "contact"
        and prompt.clean_chat_message_content(
            str(conversation_messages[index].get("content") or "")
        )
    )
    decline_match = _unresolved_tapple_decline_match(
        conversation_messages, last_contact_index
    )
    if decline_match:
        return TappleStrategy(
            action="stop",
            rationale="相手が会うことに明確な難しさを示しているため、誘い直さずここで止めます。",
            evidence=[decline_match.group(0)],
            invite_example=None,
        )

    if proposed.action == "invite":
        recent_activity_disinterest = any(
            term in evidence
            and _has_recent_self_disinterest_in_tapple_activity(
                conversation_messages, last_contact_index, term
            )
            for evidence in exact_evidence
            for term in _TAPPLE_SHARED_ACTIVITY_TERMS
        )
        has_current_invitation_readiness = any(
            evidence in last_contact
            and (
                _has_first_person_tapple_intent_evidence(
                    last_contact, evidence, _TAPPLE_INVITE_POSITIVE_RE
                )
                or (
                    _has_first_person_tapple_intent_evidence(
                        last_contact, evidence, _TAPPLE_ACTIVITY_INTEREST_RE
                    )
                    and _has_recent_shared_tapple_activity(
                        conversation_messages, last_contact_index, last_contact
                    )
                )
            )
            and not _has_tapple_relevant_invite_hedge(
                _tapple_text_without_superseded_historical_declines(last_contact)
            )
            and not _has_tapple_explicit_hesitation(last_contact)
            and not _has_tapple_safety_concern(last_contact)
            and not _has_unresolved_tapple_safety_or_hesitation(
                conversation_messages, last_contact_index
            )
            for evidence in exact_evidence
        )
        if recent_activity_disinterest or not has_current_invitation_readiness:
            return TappleStrategy(
                action="wait",
                rationale="会う提案につながる具体的な関心が確認できないため、今は誘わず会話を続けるか反応を待ちます。返信の速さや曖昧な相づちは誘う根拠にしません。",
                evidence=exact_evidence,
                invite_example=None,
            )
        safe_example = proposed.invite_example
        if safe_example and not _is_tapple_invitation_example_safe(safe_example):
            safe_example = None
        safety_notice = (
            "AIは相手の信頼性や実際の安全性を判断できません。"
            "この提案は相手の同意を意味しません。相手が迷ったり断ったりしたら誘い直さないでください。"
            "自分が信頼でき、安全に会えると感じる場合に限り、この案を検討してください。"
        )
        rationale_limit = max(0, 500 - len(safety_notice) - 1)
        rationale = f"{proposed.rationale[:rationale_limit].rstrip()} {safety_notice}"
        return proposed.model_copy(
            update={"invite_example": safe_example, "rationale": rationale}
        )

    # An invitation example is meaningful only when the guarded invite action passes.
    return proposed.model_copy(update={"invite_example": None})


def _tapple_strategy_output_violations(
    strategy: TappleStrategy | None,
) -> list[str]:
    """Require actionable guidance when the validated strategy recommends inviting."""
    if (
        strategy is not None
        and strategy.action == "invite"
        and not (strategy.invite_example or "").strip()
    ):
        return [
            "inviteを選ぶ場合は、返信候補とは別に安全な公共の場所を使った低圧な誘い方の例をinvite_exampleへ入れてください。"
            "会話にない店名や日時を作らず、例を出せない場合はinvite以外のactionを選んでください。"
        ]
    return []


_EXPERIENCE_ACTIONS: dict[str, tuple[str, ...]] = {
    "visit": ("行ってきました", "行ってきた", "訪れてきました", "訪れてきた", "行けた", "行けて", "行った", "行きました", "行って", "行く", "行きます", "訪れた", "訪れました", "訪れる"),
    "eat": ("食べてきました", "食べてきた", "食べた", "食べました", "食べて", "食べる", "食べます"),
    "drink": ("飲んできました", "飲んできた", "飲んだ", "飲みました", "飲んで", "飲む", "飲みます"),
    "watch": ("見てきました", "見てきた", "見た", "見ました", "見て", "見る", "見ます"),
    "use": ("使ってきました", "使ってきた", "使った", "使いました", "使って", "使う", "使います"),
    "ride": ("乗ってきました", "乗ってきた", "乗った", "乗りました", "乗って", "乗る", "乗ります"),
    "reside": ("暮らしてきました", "暮らしてきた", "住んできました", "住んできた", "暮らした", "暮らしました", "住んだ", "住みました", "暮らして", "住んで"),
}
def _experience_action(text: str) -> tuple[str, re.Match[str]] | None:
    """Return the rightmost supported experience verb (the active clause)."""
    matches: list[tuple[int, str, re.Match[str]]] = []
    for action, forms in _EXPERIENCE_ACTIONS.items():
        pattern = re.compile("|".join(map(re.escape, sorted(forms, key=len, reverse=True))))
        matches.extend((match.start(), action, match) for match in pattern.finditer(text))
    if matches:
        _, action, match = max(matches, key=lambda item: item[0])
        return action, match
    generic = re.search(r"経験(?:が)?(?:ある|あります|ない|ありません)", text)
    if generic:
        return "experience", generic
    return None


def _topic_before(text: str, end: int) -> str:
    """Extract a compact Japanese topic immediately before an experience verb."""
    prefix = text[:end].strip()
    prefix = re.split(r"[、。！？!?\s]", prefix)[-1]
    # Protect common temporal expressions from the delimiter "で" below.
    prefix = re.sub(r"(?:今まで|これまで)", "", prefix)
    prefix = re.split(r"(?:で|けど|けれど|だけど|ので|から|そして)", prefix)[-1]
    prefix = re.sub(
        r"^(?:(?:[0-9]+|[一二三四五六七八九十]+)年間?|(?:学生|高校|大学|社会人)時代|"
        r"子どもの頃|この前|今まで|昔|以前|前|今|最近|一度|何度か|昨日|先日|たまに|よく|時々|ときどき|しょっちゅう|いつも)(?:に|は|から|頃)?",
        "",
        prefix,
    )
    prefix = re.sub(r"^(?:自分|私|わたし|僕|ぼく|俺)(?:も|は|が|の)?", "", prefix)
    prefix = re.sub(r"(?:食べ|飲み|見|観|買い)に$", "", prefix)
    prefix = re.sub(r"(?:まだ|もう|すでに|今まで|学生時代|昔|以前|前|今|最近|先日|この前)(?:に|は|から|頃)?$", "", prefix)
    prefix = re.sub(r"(?:って|は|が|を|に|で|なら|とか|の)+$", "", prefix)
    return prefix[-12:]


def _experience_claim(text: str, default_topic: str, default_action: str) -> dict[str, str] | None:
    """Classify the first direct answer claim, without borrowing polarity from later clauses."""
    normalized = unicodedata.normalize("NFKC", text)
    # Standalone yes/no answers can inherit the question's action. Do not mistake
    # existential observations such as "そういう店ありますよね" for a yes-answer.
    generic_answer = re.match(
        r"^\s*(?:(?:うん|はい|ううん|いいえ|いや|そう|一応|実は|確か|たしか|多分|たぶん|まあ|"
        r"自分も|私も|わたしも|僕も|ぼくも|俺も)[、,]?\s*)?"
        r"(?:あります|ある|ありません|ない)(?:よ|ね|です|ですよ|ですね|ですよね|！|!|。|$)",
        normalized,
    )
    if generic_answer:
        answer = generic_answer.group(0)
        return {
            "topic": default_topic,
            "action": default_action,
            "polarity": "negative" if _is_negative_experience(answer, allow_bare=True) else "positive",
            "strength": "single",
        }

    # Inspect one sentence at a time so a later third-party mention cannot change
    # the polarity or subject of the user's direct answer.
    clauses = _split_experience_clauses(normalized)
    past_action_forms = {
        "行った", "行きました", "訪れた", "訪れました", "食べた", "食べました",
        "飲んだ", "飲みました", "見た", "見ました", "使った", "使いました",
        "乗った", "乗りました", "行ってきた", "行ってきました",
        "訪れてきた", "訪れてきました", "食べてきた", "食べてきました",
        "飲んできた", "飲んできました", "見てきた", "見てきました",
        "使ってきた", "使ってきました", "乗ってきた", "乗ってきました",
        "暮らした", "暮らしました", "住んだ", "住みました",
    }
    for clause in clauses:
        action_match = _experience_action(clause)
        if not action_match:
            continue
        prefix = clause[:action_match[1].start()]
        suffix = clause[action_match[1].end():]
        # Exclude a locally attributed third-party claim, not a separate later mention.
        if re.search(
            r"(?:(?:彼氏|彼女|恋人|友達|友人|家族|相手|あの人|その人|彼)(?:は|が|も|には|にも|なら|にとって)|人|方)$",
            prefix,
        ):
            continue
        if re.search(
            r"こと(?:が)?(?:ある|あります|ない|ありません)人(?:は|が|も|には|にも|なら|に|多い)|"
            r"(?:こと(?:が)?(?:ない|ありません)|て(?:い)?(?:ない|ません))人(?:は|が|も|には|にも|なら|に)?|"
            r"人(?:は|が|も|には|にも|なら|に|多い)",
            action_match[0] + suffix,
        ):
            continue

        action_key = action_match[0]
        topic = _topic_before(clause, action_match[1].start()) or default_topic
        has_past = bool(
            action_match[1].group() in past_action_forms
            or (
                action_key == "reside"
                and re.search(r"(?:住んで(?:います|いる|る)|暮らして(?:います|いる|る))", clause)
            )
            or re.search(
                r"(?:この前|前に|最近|一度|何度か|昨日|先日|こと(?:が)?ある|こと(?:が)?ない|こと(?:が)?ありません|"
                r"てきた|てきました|てない|ていない|てません|ていません|てる|てます|ています|ている|てた|ていた|"
                r"行けた|行けて|"
                r"した|しました|んだ|たよ|たね|たんです)",
                clause,
            )
        )
        is_habit = _has_habit_frequency(clause) or bool(re.search(r"て(?:る|ます|います|いる)", clause))
        if not (has_past or is_habit):
            continue
        if re.search(r"(?:こと(?:が)?なくはない|なくはない|ないわけではない|ないこともない)", clause):
            polarity = "ambiguous"
        else:
            polarity = "negative" if _is_negative_experience(clause) else "positive"
        return {
            "topic": topic, "action": action_key, "polarity": polarity,
            "strength": "habit" if is_habit else "single",
        }
    return None


def _has_habit_frequency(text: str) -> bool:
    return bool(re.search(
        r"(?:よく|たまに|時々|ときどき|しょっちゅう|いつも|普段|"
        r"月に(?:一|１|[1-9１-９])度|毎月|週に?(?:一|１|[1-9１-９])回|毎週|"
        r"週[1-9１-９]回|年に(?:一|１|[1-9１-９])度|毎年|て(?:る|ます|います|いる|た|いた))",
        text,
    ))


def _is_negative_experience(text: str, *, allow_bare: bool = False) -> bool:
    action_negation = re.search(
        r"(?:こと(?:が|は)?(?:ない|ありません)|"
        r"(?:行っ|行け|食べ|飲ん|見|使っ|乗っ)?て(?:い)?(?:ない|ません)|"
        r"行かない|行きません|食べない|食べません|飲まない|飲みません|"
        r"見ない|見ません|使わない|使いません|乗らない|乗りません|なかった)",
        text,
    )
    if action_negation:
        return True
    return allow_bare and bool(re.search(r"(?:ありません|ない)(?:よ|ね|です|ですよ|ですね|！|!|。|$)", text))


def _split_experience_clauses(text: str) -> list[str]:
    clauses = re.split(r"(?<=[。！？!?、,])|(?:けれど|けど|だけど|でも)|[\r\n]", text)
    split_clauses: list[str] = []
    for clause in clauses:
        split_clauses.extend(re.split(r"が(?=(?:今|最近|現在|行|訪れ|食べ|飲み|見|使い|乗|詳しく))", clause))
    return split_clauses


def _experience_topic_matches(claim_topic: str, fact_topic: str, is_generic_experience: bool) -> bool:
    if not claim_topic or not fact_topic:
        return False
    if claim_topic == fact_topic:
        return True
    if not is_generic_experience:
        return claim_topic in fact_topic or fact_topic in claim_topic

    # A broad experience label can match a concrete travel/activity subtype, but
    # not an unrelated use of the same word (e.g. overseas dramas vs. overseas trips).
    subtypes = ("旅行", "出張", "留学", "滞在", "観光", "訪問", "渡航", "生活", "居住", "移住", "駐在", "屋")
    if fact_topic.startswith(claim_topic):
        return fact_topic[len(claim_topic):].startswith(subtypes)
    if fact_topic.endswith(claim_topic):
        prefix = fact_topic[:-len(claim_topic)]
        return prefix in ("海外", "国内", "一人", "家族", "短期", "長期")
    return False


def _fact_supports_experience_claim(fact: str, claim: dict[str, str]) -> bool:
    normalized = unicodedata.normalize("NFKC", fact)
    fact_clauses = _split_experience_clauses(normalized)
    for fact_clause in fact_clauses:
        action_match = _experience_action(fact_clause)
        if not action_match:
            continue
        action = action_match[0]
        is_generic_experience = claim["action"] == "experience"
        if not is_generic_experience and action != claim["action"]:
            continue

        fact_topic = _topic_before(fact_clause, action_match[1].start())
        if not _experience_topic_matches(claim["topic"], fact_topic, is_generic_experience):
            continue
        if re.search(r"(?:こと(?:が)?なくはない|なくはない|ないわけではない|ないこともない)", fact_clause):
            continue
        polarity = "negative" if _is_negative_experience(fact_clause) else "positive"
        if polarity != claim["polarity"]:
            continue
        if claim["strength"] == "habit" and not _has_habit_frequency(fact_clause):
            continue
        return True
    return False


def _last_experience_question(text: str) -> tuple[str, re.Match[str], str] | None:
    """Return the action/topic from the last explicit experience-question clause."""
    normalized = unicodedata.normalize("NFKC", text)
    clauses = re.split(r"(?<=[?？。！!])", normalized)
    for clause in reversed(clauses):
        if not re.search(r"[?？]", clause):
            continue
        # Require the experience wording or the action itself to close the question;
        # this excludes quoted history such as "行ったことあるって話したよね？".
        if not re.search(
            r"(?:(?:こと(?:が|は)?(?:ある|あります|ない|ありません)(?:んです|の|んだっけ|だっけ|かな|かも)?|"
            r"こと(?:が|は)?(?:とか)?ある|こと(?:が|は)?あったりする|"
            r"経験(?:が)?(?:ある|あります|ない|ありません)(?:んです|の|んだっけ|だっけ|かな|かも)?)(?:か)?|"
            r"(?:行った|行きました|食べた|食べました|飲んだ|飲みました|見た|見ました|使った|使いました|乗った|乗りました)(?:の|んだっけ|だっけ|かな)?)\?\s*$",
            clause,
        ):
            continue
        action_match = _experience_action(clause)
        if action_match:
            return action_match[0], action_match[1], clause
    return None


def _needs_private_experience_confirmation(
    counterpart_message: str,
    known_self_facts: list[str] | None,
) -> bool:
    """Whether an experience question in the latest message lacks any known answer."""
    experience_question = _last_experience_question(counterpart_message)
    if not experience_question:
        return False

    action, action_match, question_clause = experience_question
    topic = _topic_before(question_clause, action_match.start())
    for polarity in ("positive", "negative"):
        possible_answer = {
            "topic": topic,
            "action": action,
            "polarity": polarity,
            "strength": "single",
        }
        if any(
            _fact_supports_experience_claim(fact, possible_answer)
            for fact in (known_self_facts or [])
            if fact
        ):
            return False
    return True


def _personal_preference_question_topics(counterpart_message: str) -> list[str]:
    """Return concrete topics in a direct question about the user's preference."""
    normalized = unicodedata.normalize("NFKC", counterpart_message or "")
    looks_like_question = bool(re.search(r"[?？]|(?:なの|ですか|ますか|かな)$", normalized))
    if not looks_like_question:
        return []
    if (
        re.search(r"(?:犬と猫|犬か猫|犬猫)", normalized)
        or ("犬派" in normalized and "猫派" in normalized)
    ):
        topics = ["犬", "猫"]
    elif "犬派" in normalized:
        topics = ["犬"]
    elif "猫派" in normalized:
        topics = ["猫"]
    else:
        topics = []

    for clause in reversed(re.split(r"(?<=[?？。！!])", normalized)):
        if not re.search(r"[?？]", clause):
            continue
        choice = re.search(
            r"([^\s、。！？?と]{1,10}?)と([^\s、。！？?]{1,10}?)(?:なら)?、?(?:どっち|どちら)(?:が|も)?(?:好き|嫌い|苦手|得意|大丈夫|平気|いける|食べられる|できます|できる)",
            clause,
        )
        if choice:
            for raw_topic in choice.groups():
                topic = raw_topic.rstrip("はがを")
                if topic and topic not in topics:
                    topics.append(topic)
        match = re.search(
            r"([^\s、。！？?]{1,10}?)(?:は)?(?:好き|嫌い|苦手|得意|大丈夫|平気|いける|食べられる|できます|できる)",
            clause,
        )
        if match:
            topic = re.sub(r"(?:って|とは|っては|のことは|のは|は|が|を|も)$", "", match.group(1))
            topic = topic.lstrip("おご")
            if topic.endswith("の") and not topic.endswith("もの"):
                topic = topic[:-1]
            topic = topic.rstrip("はがを")
            topic = re.sub(r"^(?:この|その|あの|どの)", "", topic)
            if re.search(r"(?:どっち|どちら|両方|どちらか)", topic):
                continue
            if topic and topic not in topics:
                topics.append(topic)
    return topics


def _fact_supports_preference(fact: str, topic: str) -> bool:
    normalized = unicodedata.normalize("NFKC", fact or "")
    return bool(re.search(rf"{re.escape(topic)}.{{0,12}}(?:派|好き|嫌い|苦手|得意|大丈夫|平気|いける|食べられる|できます|できる)", normalized))


def _preference_polarity(text: str, topic: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", text or "")
    match = re.search(
        rf"{re.escape(topic)}(?:[^。！？?]{{0,12}})(?:派|好き|嫌い|苦手|得意|大丈夫|平気|いける|食べられる|できます|できる)", normalized
    )
    if not match:
        match = re.search(r"(?:好き|嫌い|苦手|得意|大丈夫|平気|いける|無理|だめ|駄目|食べられる|食べられない)", normalized)
    if not match:
        return None
    phrase = match.group(0)
    suffix = normalized[match.end():match.end() + 8]
    prefix = normalized[max(0, match.start() - 6):match.start()]
    if re.search(r"(?:嫌い|苦手|無理|だめ|駄目|できない|食べられない)", phrase) or re.match(
        r"(?:じゃない|ではない|じゃありません|ではありません|くない|くありません)", suffix
    ) or re.search(r"(?:あまり|そんなに).{0,3}$", prefix):
        return "negative"
    if re.search(r"(?:好き|得意|大丈夫|平気|いける|食べられる|できます|できる|派)", phrase):
        return "positive"
    return None


def _preference_claim_topics(reply: str, topics: list[str]) -> set[str]:
    normalized = unicodedata.normalize("NFKC", reply or "")
    claims = set()
    for topic in topics:
        if re.search(
            rf"{re.escape(topic)}(?:[^。！？?]{{0,12}})(?:派|好き|嫌い|苦手|得意|大丈夫|平気|いける|食べられる|できます|できる)",
            normalized,
        ):
            claims.add(topic)
    if len(topics) == 1 and re.search(
        r"(?:好き|嫌い|苦手|得意|大丈夫|平気|いける|無理|だめ|駄目|食べられる|食べられない)",
        normalized,
    ):
        claims.add(topics[0])
    if len(topics) > 1 and re.search(r"(?:どっちも|どちらも|両方|どちらかといえば両方)", normalized):
        if re.search(r"(?:好き|嫌い|苦手|得意|派|大丈夫|平気|いける|無理|だめ|駄目)", normalized):
            claims.update(topics)
    return claims


def _personal_schedule_question_topic(counterpart_message: str) -> str:
    """Classify direct questions about the user's availability or wake-up time."""
    normalized = unicodedata.normalize("NFKC", counterpart_message or "")
    looks_like_question = bool(re.search(r"[?？]|(?:空いてる|空いてます|空いています|行ける|行けます|いける|いけます|大丈夫)$", normalized))
    if not looks_like_question:
        return ""
    date_or_availability = r"(?:(?:20\d{2}年)?\d{1,2}(?:月|/)\d{1,2}日?|来月(?:の)?\d{1,2}日?|今月(?:の)?\d{1,2}日?|(?:今度の|次の)?[月火水木金土日]曜(?:日)?|明後日|明日|今日|土日|週末|平日|来週|今週|いつ|何日|何曜日)"
    if re.search(
        rf"{date_or_availability}.{{0,10}}(?:どっち|どちら|空いて|予定|都合|行け|いけ|大丈夫)|"
        rf"(?:空いて|予定|都合|行け|いけ|大丈夫).{{0,8}}{date_or_availability}",
        normalized,
    ):
        return "availability"
    if re.search(r"何時.{0,6}(?:起き|起床)|(?:起き|起床).{0,6}何時", normalized):
        return "wake_time"
    return ""


_AVAILABILITY_PERIOD_RE = re.compile(
    r"(?:20\d{2}/\d{1,2}/\d{1,2}|(?:20\d{2}年)?\d{1,2}(?:月|/)\d{1,2}日?)|"
    r"来月(?:の)?\d{1,2}日?|今月(?:の)?\d{1,2}日?|"
    r"(?:今度の|次の)?[月火水木金土日]曜(?:日)?|"
    r"来週(?:末|土日)?|今週(?:末|土日)?|明後日|明日|今日|土日|週末|平日"
)


def _canonical_availability_period(period: str) -> str:
    normalized = period.removeprefix("今度の").removeprefix("次の").replace("の", "")
    slash_date = re.fullmatch(r"(?:(20\d{2})/)?(\d{1,2})/(\d{1,2})", normalized)
    if slash_date:
        year, month, day = slash_date.groups()
        year_prefix = f"{year}年" if year else ""
        return f"{year_prefix}{int(month)}月{int(day)}日"
    if normalized.startswith("来週"):
        return "来週末" if normalized != "来週" else "来週"
    if normalized.startswith("今週"):
        return "今週末" if normalized != "今週" else "今週"
    if re.fullmatch(r"[月火水木金土日]曜日", normalized):
        return normalized[:-1]
    if re.fullmatch(r"(?:20\d{2}年)?\d{1,2}月\d{1,2}日?", normalized) and not normalized.endswith("日"):
        return f"{normalized}日"
    return normalized


def _availability_periods(text: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    return {_canonical_availability_period(match.group()) for match in _AVAILABILITY_PERIOD_RE.finditer(normalized)}


def _availability_claims_by_period(text: str) -> dict[str, str]:
    normalized = unicodedata.normalize("NFKC", text or "")
    matches = list(_AVAILABILITY_PERIOD_RE.finditer(normalized))
    segment_polarities = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        segment_polarities.append(_availability_polarity(normalized[match.start():end]))
    if len(matches) > 1 and segment_polarities[-1]:
        last_start = matches[-1].start()
        for index, match in enumerate(matches[:-1]):
            connector = normalized[match.end():last_start]
            if (
                not segment_polarities[index]
                and re.search(r"(?:と|も|や|、|及び|および)", connector)
                and not re.search(r"(?:けど|けれど|でも|一方)", connector)
            ):
                segment_polarities[index] = segment_polarities[-1]
    claims = {
        _canonical_availability_period(match.group()): polarity
        for match, polarity in zip(matches, segment_polarities)
        if polarity
    }
    return claims


def _availability_period_matches(requested: str, known: str) -> bool:
    if requested == known:
        return True
    if requested in {"週末", "土日"}:
        return known in {"週末", "土日", "今週末", "来週末", "土曜", "日曜"}
    if requested in {"土曜", "日曜"}:
        return known in {requested, "土日", "週末"}
    if requested in {"今週", "来週"}:
        return known in {requested, f"{requested}末"}
    return False


def _availability_polarity(text: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", text or "")
    if re.search(
        r"(?:わけ(?:では|じゃ)ない|(?:とは|と|って)(?:言|い)って(?:い)?ない|"
        r"(?:とは|って)(?:言|い)えない|とは(?:限|かぎ)ら(?:ない|ず)|言い切れない|断定できない|"
        r"(?:聞いた|聞いて(?:い)?(?:る|た|ない|なかった)|聞かされ(?:た|て(?:い)?(?:る|た|ない|なかった))|"
        r"(?:とは|と|って)(?:言|い)われ(?:た|て(?:い)?(?:る|た|ない|なかった))|言ってた|言っていた|らしい|そうだ|と思う|と思います|可能性(?:が)?(?:ある|あります)|たぶん|多分|おそらく)|"
        r"かも(?:しれない)?|かな|気がする|無理じゃない)",
        normalized,
    ):
        return None
    polarity_patterns = (
        ("unavailable", re.compile(r"(?:空いてい(?:ない|ません)|空いて(?:ない|ません)|空いておらず|予定.{0,8}(?:あります|ある|入って(?:いる|ます|る)|埋まって(?:いる|ます|る))|都合.{0,5}悪い|行け(?:ない|ません)|暇(?:ではない|じゃない)|大丈夫(?:ではない|じゃない)|無理)")),
        ("available", re.compile(r"(?:空いて(?:います|いる|いて|ます|る)|予定.{0,8}(?:ありません|ない|なし|入っていない|入ってません)|都合.{0,5}(?:いい|よい|つく)|行け(?:ます|る)|大丈夫|暇)")),
    )
    matches = [
        (match.start(), polarity)
        for polarity, pattern in polarity_patterns
        for match in pattern.finditer(normalized)
    ]
    return max(matches)[1] if matches else None


def _fact_supports_schedule_question(fact: str, topic: str, counterpart_message: str = "") -> bool:
    normalized = unicodedata.normalize("NFKC", fact or "")
    if topic == "availability":
        question = unicodedata.normalize("NFKC", counterpart_message or "")
        requested_periods = _availability_periods(question)
        fact_periods = _availability_periods(normalized)
        if any(_is_specific_availability_period(period) for period in requested_periods) and not any(
            _availability_period_matches(requested, known)
            for requested in requested_periods for known in fact_periods
        ):
            return False
        return _availability_polarity(normalized) is not None
    if topic == "wake_time":
        return bool(re.search(r"(?:何時.{0,4}起き|[0-2]?[0-9]時.{0,4}起き|早起き|起床.{0,6}時間)", normalized))
    return False


def _is_recurring_weekday_claim(text: str, match: re.Match[str]) -> bool:
    recurrence = r"(?:毎週|週ごと|定期的(?:に)?|いつも|毎回|基本的(?:に)?)"
    prefix = text[max(0, match.start() - 8):match.start()]
    suffix = text[match.end():match.end() + 8]
    return bool(
        re.search(rf"{recurrence}(?:の|に|は)?$", prefix)
        or re.match(rf"^(?:は|も|が|なら)?{recurrence}", suffix)
    )


def _current_relative_schedule_fact(
    fact: str,
    created_at: str | None,
    reference_datetime: datetime,
) -> str:
    """Keep only schedule clauses whose relative dates still have a time anchor."""
    normalized = unicodedata.normalize("NFKC", fact or "")
    fact_date = None
    if created_at:
        try:
            fact_date = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date()
        except (TypeError, ValueError):
            fact_date = None

    clauses = re.split(r"(?:[。！？!?;；\n]+|けど|けれど|でも|一方で?|ただし)", normalized)
    current_clauses = []
    for clause in clauses:
        if not clause.strip():
            continue
        stale = False
        for match in _AVAILABILITY_PERIOD_RE.finditer(clause):
            period = _canonical_availability_period(match.group())
            is_relative = period in {"今日", "明日", "明後日", "今週", "今週末", "来週", "来週末"}
            is_weekday = bool(re.fullmatch(r"[月火水木金土日]曜", period))
            if not is_relative and not is_weekday:
                continue
            if is_weekday and _is_recurring_weekday_claim(clause, match):
                if fact_date is None:
                    stale = True
                    break
                continue
            if fact_date is None or fact_date != reference_datetime.date():
                stale = True
                break
        if not stale:
            current_clauses.append(clause.strip())
    return "。".join(current_clauses)


def _is_specific_availability_period(period: str) -> bool:
    return bool(
        period in {"来週末", "今週末", "来週", "今週", "明後日", "明日", "今日"}
        or re.fullmatch(r"[月火水木金土日]曜", period)
        or re.fullmatch(r"(?:20\d{2}年)?\d{1,2}月\d{1,2}日", period)
    )


def _is_bare_state_echo(counterpart_message: str, reply: str) -> bool:
    """Reject a short state restatement that substitutes for a direct response."""
    incoming = unicodedata.normalize("NFKC", counterpart_message or "")
    outgoing = unicodedata.normalize("NFKC", reply or "")
    state_terms = ("眠い", "疲れた", "へとへと", "だるい", "忙しい")
    for state in state_terms:
        if state not in incoming or state not in outgoing:
            continue
        remainder = re.sub(r"^(?:わかります|わかる|そうだよね|そうですね|ほんと|それは)[、,\s笑ｗw]*", "", outgoing)
        remainder = re.sub(r"(?:ですよね|だよね|だね|ですね|よね|よ|ね)?[笑ｗw！!。…‥]*$", "", remainder)
        remainder = re.sub(r"[\s、。！？!?,笑ｗw]+", "", remainder)
        if remainder == state:
            return True
    return False


def _personal_desire_topic(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    desire = re.search(r"(?:行きたく|行きたい|見たく|見たい|観たく|観たい|食べたく|食べたい|飲みたく|飲みたい|欲しく|欲しい|好き|嫌い|苦手|得意|興味(?:が)?(?:ある|あります))", normalized)
    if not desire:
        return ""
    prefix = normalized[:desire.start()]
    prefix = re.sub(r"^(?:僕|ぼく|私|わたし|俺|自分)(?:も|は|が)?", "", prefix)
    prefix = re.sub(r"(?:行ってきた|行ってきました|行った|見てきた|見てきました|観てきた|食べてきた|してきた|してきました|してた|していました|しました|した).*$", "", prefix)
    prefix = re.sub(r"^(?:昨日|今日|最近|この前|先日|今週|来週|週末|土日)+", "", prefix)
    return prefix.strip(" 、。はがをにへでとの")[-10:]


def _contact_activity_topic(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    prefix = re.split(
        r"(?:行ってきた|行ってきました|行った|行きました|見てきた|見てきました|観てきた|観てきました|"
        r"食べてきた|食べてきました|してきた|してきました|してた|していました|しました|した|好き)",
        normalized,
    )[0]
    prefix = re.sub(r"^(?:昨日|今日|最近|この前|先日|今週|来週|週末|土日)+", "", prefix)
    return prefix.strip(" 、。はがをにへでとの")[-10:]


def _has_unverified_personal_desire(
    counterpart_message: str,
    reply: str,
    known_self_facts: list[str] | None,
) -> bool:
    incoming_topic = _contact_activity_topic(counterpart_message)
    reply_topic = _personal_desire_topic(reply)
    # For implied-subject expressions (e.g. 「キャンプ行きたい」), only guard
    # when the desire concerns the current contact's topic. Explicit self-subjects
    # are guarded regardless of topic overlap.
    has_explicit_self = bool(re.search(r"(?:僕|ぼく|私|わたし|俺|自分)(?:も|は|が)", reply))
    has_explicit_desire = bool(re.search(
        r"(?:行きたく|行きたい|見たく|見たい|観たく|観たい|食べたく|食べたい|飲みたく|飲みたい|欲しく|欲しい|好き|嫌い|苦手|得意|興味(?:が)?(?:ある|あります))",
        unicodedata.normalize("NFKC", reply),
    ))
    if has_explicit_self and has_explicit_desire and not reply_topic:
        reply_topic = incoming_topic
    if not (has_explicit_self and has_explicit_desire) and not (
        incoming_topic and reply_topic and (incoming_topic in reply_topic or reply_topic in incoming_topic)
    ):
        return False
    if not reply_topic:
        return False
    fact_terms = r"(?:好き|嫌い|苦手|得意|興味|行きたい|見たい|観たい|食べたい|飲みたい|欲しい|飼いたい)"
    return not any(
        reply_topic in unicodedata.normalize("NFKC", fact or "")
        and re.search(fact_terms, unicodedata.normalize("NFKC", fact or ""))
        for fact in (known_self_facts or [])
        if fact
    )


def _has_unverified_personal_habit(reply: str, known_self_facts: list[str] | None) -> bool:
    normalized = unicodedata.normalize("NFKC", reply or "")
    if re.search(r"(?:よく|よくは)?(?:わかります|わかる|分かります|分かる)", normalized):
        return False
    claim = re.search(
        r"(?:僕|ぼく|私|わたし|俺|自分)(?:も|は|が)?[^。！？!?]{0,35}",
        normalized,
    )
    habit_signal = r"(?:よく|普段|いつも|たまに|時々|ときどき|つい|ふとした時|こと(?:が)?あります|こと(?:が)?ある|時(?:が)?あります|時(?:が)?ある|考えちゃ|なくすこと)"
    if not claim or not re.search(habit_signal, claim.group(0)):
        return False
    claim_core = re.sub(r"^(?:僕|ぼく|私|わたし|俺|自分)(?:も|は|が)?", "", claim.group(0))
    claim_core = re.sub(r"(?:よく|普段|いつも|たまに|時々|ときどき|つい|ふとした時)", "", claim_core)
    claim_core = re.sub(r"(?:こと(?:が)?あります|こと(?:が)?ある|時(?:が)?あります|時(?:が)?ある)", "", claim_core)
    claim_core = re.sub(r"(?:ます|ました|です|でした|よ|ね|笑|ｗ|w|！|!|。|…|‥|\.)+$", "", claim_core)
    claim_core = re.sub(r"[\s、。はがをにへでとも]", "", claim_core)
    if not claim_core:
        return False
    return not any(
        claim_core in re.sub(r"[\s、。はがをにへでとも]", "", unicodedata.normalize("NFKC", fact or ""))
        for fact in (known_self_facts or [])
        if fact
    )


def _is_private_question_for_unknown_experience(
    question_text: str,
    counterpart_message: str,
    known_self_facts: list[str] | None,
) -> bool:
    """Check that a private question actually asks about the unknown experience."""
    if not _needs_private_experience_confirmation(counterpart_message, known_self_facts):
        return False
    experience_question = _last_experience_question(counterpart_message)
    private_action = _experience_action(question_text)
    if not experience_question or not private_action:
        return False

    expected_action, question_match, question_clause = experience_question
    actual_action, private_match = private_action
    if expected_action != actual_action:
        return False

    expected_topic = _topic_before(question_clause, question_match.start())
    private_topic = _topic_before(question_text, private_match.start())
    if not _experience_topic_matches(
        expected_topic,
        private_topic,
        expected_action == "experience",
    ):
        return False

    return bool(re.search(
        r"(?:こと(?:が|は)?(?:ある|あります|ない|ありません)|経験(?:が)?(?:ある|あります|ない|ありません))",
        unicodedata.normalize("NFKC", question_text),
    ))


_UNRESOLVED_STATUS_QUERY = re.compile(
    r"(?:あれ|それ|その件|例の(?:話|件|やつ)?|あの件).{0,12}"
    r"(?:どうな(?:った|りました|ってる|ってます|っている)|どう(?:してる|なってる)|進捗|状況)",
)
_STATUS_ASSERTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("progress", re.compile(r"(?:順調に進んで|順調に進行|進行中|進んで(?:い)?(?:る|ます|います|るよ|ますよ)|進んでる)")),
    ("undecided", re.compile(r"(?:まだ|全然)?決まって(?:い)?(?:ない|なくて|ません|なかった)|未定")),
    ("deciding", re.compile(r"(?:これから|今から|これから先)?決める(?:感じ|予定|ところ)?|検討中")),
    ("decided", re.compile(r"(?:もう)?決ま(?:った|りました|ってる|っています)|決定した")),
    ("completed", re.compile(r"(?:もう)?(?:終わった|終わりました|完了した|完了しました|済んだ|済みました)")),
)


def _unresolved_status_query(message: str) -> bool:
    normalized = unicodedata.normalize("NFKC", message or "")
    return bool(_UNRESOLVED_STATUS_QUERY.search(normalized))


def _status_claims(text: str) -> tuple[list[str], list[str]]:
    """Return asserted statuses and status questions, preserving every claim.

    Japanese questions such as 「まだ決まってないんですか？」 contain the
    same lexical status pattern as an assertion, so classify them by the
    clause ending instead of treating the first matching word as a claim.
    """
    normalized = unicodedata.normalize("NFKC", text or "")
    assertions: list[str] = []
    questions: list[str] = []
    clauses = re.split(r"(?<=[。！？!?、,，])|(?:けれど|けど|だけど|でも)|[\r\n]", normalized)
    for clause in clauses:
        is_question = bool(re.search(
            r"(?:ですか|ますか|でしょうか|ですかね|ますかね|かな|かも|んだっけ|だっけ|の)?\s*[?？]$|"
            r"(?:ですかね|ますかね|ですか|ますか|でしょうか|かな|んだっけ|だっけ|の)\s*$",
            clause.strip(),
        ))
        for status, pattern in _STATUS_ASSERTION_PATTERNS:
            for _match in pattern.finditer(clause):
                (questions if is_question else assertions).append(status)
    return assertions, questions


_HARMLESS_STATUS_PREFACES = {
    "", "え、", "あ、", "うん、", "うーん、", "んー、", "そう、",
    "今のところ", "今のところ、", "たぶん", "たぶん、", "おそらく", "おそらく、",
}
_STATUS_REPLY_ENDING = re.compile(
    r"(?:かもしれないですね|かもしれないね|かもしれないですよ|かもしれないよ|かもしれないです|かもしれない|"
    r"と思いますよね|と思いますよ|と思うよね|と思うよ|と思います|と思う|"
    r"んですよね|んだよね|んですね|んですよ|んだよ|んだ|ですね|ですよ|です|ます|だよね|だよ|だね|よね|よ|ね|かな|かもね|かもよ|かも)?"
    r"(?:笑|w|W|😊|😂|😅|🙂|！|!|。|〜|~)*"
)


def _is_single_supported_status_reply(text: str, supported_status: str) -> bool:
    """既知の状態を一つだけ返す短文かを、候補全体で保守的に判定する。"""
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    status_pattern = next(
        (pattern for status, pattern in _STATUS_ASSERTION_PATTERNS if status == supported_status),
        None,
    )
    if status_pattern is None:
        return False
    matches = list(status_pattern.finditer(normalized))
    if len(matches) != 1:
        return False
    match = matches[0]
    prefix = normalized[:match.start()].strip()
    suffix = normalized[match.end():].strip()
    return prefix in _HARMLESS_STATUS_PREFACES and bool(_STATUS_REPLY_ENDING.fullmatch(suffix))


def _latest_prior_self_status(chat_history_text: str, counterpart_message: str) -> str | None:
    """Only use one explicit status in the immediately preceding self turn.

    Older status mentions may belong to another topic or have been superseded.
    Requiring the last pre-query turn to be self-authored prevents stale facts
    from grounding an ambiguous 「あれどうなった？」.
    """
    prior = _prior_history_before_latest_counterpart(chat_history_text, counterpart_message)
    lines = [line.strip() for line in prior.splitlines() if line.strip()]
    if not lines:
        return None
    match = re.match(r"^自分\s*:\s*(.*)$", lines[-1])
    if not match:
        return None
    assertions, questions = _status_claims(match.group(1))
    if questions or len(assertions) != 1:
        return None
    return assertions[0]


_COUNTERPART_CLARIFICATION = re.compile(
    r"(?:(?:え|あ)[、,]\s*|(?:あれ|それ)(?:って)?[、,]?\s*)?"
    r"(?:何のこと|何の話(?:のこと)?|どの話(?:のこと)?|どの件(?:のこと)?|何の件(?:のこと)?|何について|どれのこと)"
    r"(?:だった)?(?:ですか|ますか|でしたっけ|だったっけ|だっけ|かな)?[?？]?"
    r"(?:笑|w|W|😊|😂|😅|🙂)*"
)


def _is_short_counterpart_clarification(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text or "").strip()
    return (
        len(normalized) <= 45
        and bool(_COUNTERPART_CLARIFICATION.fullmatch(normalized))
        and bool(re.search(
            r"(?:[?？]|(?:ですか|ますか|でしたっけ|だったっけ|だっけ|かな))"
            r"(?:笑|w|W|😊|😂|😅|🙂)*$",
            normalized,
        ))
    )


def _prior_history_before_latest_counterpart(chat_history_text: str, counterpart_message: str) -> str:
    """Return transcript text before the latest matching counterpart message."""
    lines = (chat_history_text or "").splitlines()
    target = unicodedata.normalize("NFKC", counterpart_message or "").strip()
    matching_line = None
    for index, line in enumerate(lines):
        match = re.match(r"^\s*相手:\s*(.*)$", line)
        if match and unicodedata.normalize("NFKC", match.group(1)).strip() == target:
            matching_line = index
    if matching_line is None:
        return "\n".join(lines)
    return "\n".join(lines[:matching_line])


def _is_unresolved_reference_clarification_set(
    replies: list[str], mode: str, counterpart_message: str, chat_history_text: str
) -> bool:
    return (
        mode == "normal"
        and _unresolved_status_query(counterpart_message)
        and _latest_prior_self_status(chat_history_text, counterpart_message) is None
        and _last_experience_question(counterpart_message) is None
        and bool(replies)
        and all(_is_short_counterpart_clarification(reply) for reply in replies)
    )


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
            r"(?:です|ます|でした|ました|でしょう|ません|なんですね|ありますか|ですか|でしょうか|ですね)"
            r"(?:[。！!？?、\s笑ｗw]|$)"
        )
        for i, r in enumerate(replies, start=1):
            if keigo_end_pattern.search(r):
                violations.append(f"案{i}にタメ口指定と矛盾する敬語・丁寧語終止（です/ます/なんですね等）が含まれています。")
    elif tone == "keigo":
        # タメ口終止パターンの検査
        tame_end_pattern = re.compile(
            r"(?:だね|だよ|でしょ|じゃん|っけ|ない？|行こ)(?:[！!？?\s]|$)"
            r"|(?<!ですよ)(?<!ますよ)(?<!です)(?<!ます)(?:ね|よ)[！!？?]"
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
    last_self_message: str = "",
    counterpart_message: str = "",
    known_self_facts: list[str] | None = None,
    known_self_fact_timestamps: list[str | None] | None = None,
    chat_history_text: str = "",
    strategy_mode: str = "none",
    tapple_action: str | None = None,
    conversation_messages: list[dict[str, Any]] | None = None,
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

    if strategy_mode == "tapple":
        counterpart_text = counterpart_message or ""
        boundary_messages = conversation_messages or (
            [{"sender": "contact", "content": counterpart_text}]
            if counterpart_text
            else []
        )
        last_boundary_contact_index = next(
            (
                index
                for index in range(len(boundary_messages) - 1, -1, -1)
                if boundary_messages[index].get("sender") == "contact"
            ),
            -1,
        )
        has_unqualified_decline = bool(
            last_boundary_contact_index >= 0
            and _unresolved_tapple_decline_match(
                boundary_messages, last_boundary_contact_index
            )
        )
        counterproposal_match = _TAPPLE_COUNTERPROPOSAL_RE.search(counterpart_text)
        reported_third_party_text = re.split(
            r"(?:って|と)(?:言って|聞いて|言われ)", counterpart_text, maxsplit=1
        )[0]
        third_party_counterproposal = _TAPPLE_THIRD_PARTY_COUNTERPROPOSAL_RE.search(
            reported_third_party_text
        )
        first_person_counterproposal = _TAPPLE_FIRST_PERSON_COUNTERPROPOSAL_RE.search(
            counterpart_text
        )
        later_date_unavailability = bool(
            counterproposal_match
            and any(
                unavailable.start() >= counterproposal_match.end()
                for unavailable in _TAPPLE_DATE_UNAVAILABILITY_RE.finditer(counterpart_text)
            )
        )
        has_counterproposal = bool(
            counterproposal_match
            and not later_date_unavailability
            and not _has_tapple_explicit_hesitation(counterpart_text)
            and not _has_tapple_safety_concern(counterpart_text)
        )
        has_date_unavailability = bool(
            _TAPPLE_DATE_UNAVAILABILITY_RE.search(counterpart_text)
        )
        accepted_invitation_allows_scheduling = bool(
            _TAPPLE_ACCEPTED_INVITATION_RE.search(counterpart_text)
            and not _has_tapple_post_acceptance_hedge(counterpart_text)
            and not _has_tapple_explicit_hesitation(counterpart_text)
            and not _has_tapple_safety_concern(counterpart_text)
            and not _TAPPLE_THIRD_PARTY_INTEREST_RE.search(counterpart_text)
            and not has_date_unavailability
        )
        for i, rep in enumerate(replies, start=1):
            if _TAPPLE_UNVERIFIABLE_SAFETY_ASSURANCE_RE.search(rep):
                violations.append(
                    f"案{i}に実際の安全性を保証する表現があります。"
                    "安全を断定せず、相手の不安を受け止めてください。"
                )
            self_proposed_date = (
                first_person_counterproposal.group("date")
                if first_person_counterproposal
                else None
            )
            if self_proposed_date:
                self_proposed_date = self_proposed_date.replace("曜日", "曜").replace(
                    "別の日", "別日"
                )
            reply_uses_self_proposed_date = bool(
                self_proposed_date and self_proposed_date in rep
            )
            unresolved_hesitation_or_safety = (
                last_boundary_contact_index >= 0
                and _has_unresolved_tapple_safety_or_hesitation(
                    boundary_messages, last_boundary_contact_index
                )
            )
            scheduling_is_expected = (
                tapple_action == "continue"
                and not has_unqualified_decline
                and not unresolved_hesitation_or_safety
                and (
                    accepted_invitation_allows_scheduling
                    or (
                        has_counterproposal
                        and (
                            third_party_counterproposal is None
                            or reply_uses_self_proposed_date
                        )
                    )
                )
            )
            if _is_tapple_private_place_proposal(rep):
                violations.append(
                    f"案{i}に自宅・ホテル・個室などで会う提案があります。"
                    "初回の待ち合わせは人目のある公共の場所にしてください。"
                )
            if _TAPPLE_DISRESPECTFUL_REPLY_RE.search(rep):
                violations.append(
                    f"案{i}に相手を侮辱する表現があります。断りには敬意を保って返してください。"
                )
            if _is_tapple_contact_exchange_request(rep):
                violations.append(
                    f"案{i}に外部連絡先の交換や移動を促す表現があります。"
                    "連絡先交換を提案せず、タップル上で会話を続ける文面にしてください。"
            )
            has_reinvitation = bool(_TAPPLE_REINVITATION_RE.search(rep))
            has_scheduling_proposal = bool(_TAPPLE_SCHEDULING_PROPOSAL_RE.search(rep))
            has_unresolved_meeting_boundary = (
                has_unqualified_decline or unresolved_hesitation_or_safety
            )
            invite_is_allowed = (
                tapple_action == "invite"
                and not has_unqualified_decline
                and not unresolved_hesitation_or_safety
            )
            if (
                has_unresolved_meeting_boundary
                and _TAPPLE_RECONSIDERATION_PRESSURE_RE.search(rep)
            ):
                violations.append(
                    f"案{i}が会うことへの断り・迷い・安全面の懸念に対して考え直すよう求めています。"
                    "相手の意思を尊重し、説得せずに返してください。"
                )
            if has_reinvitation and not (
                invite_is_allowed or scheduling_is_expected
            ):
                violations.append(
                    f"案{i}に、会う誘いを返信文へ混ぜています。"
                    "誘い方は返信候補ではなく戦略欄で提案してください。"
                    "相手が断っている場合は、誘い直しや説得をせずに返してください。"
                )
            elif has_scheduling_proposal and not (
                invite_is_allowed or scheduling_is_expected
            ):
                violations.append(
                    f"案{i}に、会う誘いを返信文へ混ぜています。"
                    "誘い方は返信候補ではなく戦略欄で提案してください。"
                    "相手が断っている場合は、誘い直しや説得をせずに返してください。"
                )

    # [AI_QUESTION] タグの混入チェック
    for i, rep in enumerate(replies, start=1):
        if "[AI_QUESTION]" in rep:
            violations.append(f"案{i}にAI_QUESTIONタグ [AI_QUESTION] が混入しています。")

    # 1つの返信の3分割（fragmentation）チェック
    clarification_only_set = _is_unresolved_reference_clarification_set(
        replies, mode, counterpart_message, chat_history_text
    )
    if _is_fragmented_split(replies) and not clarification_only_set:
        violations.append("1つの返信が3分割されて出力されています。3案それぞれが単体で送信できる独立した完成品となるよう作成してください。")

    # トーン検査
    if tone:
        violations.extend(validate_tone_strict(replies, tone))

    # 追いメッセージ時の催促表現禁止チェック
    if mode == "followup":
        def normalize_for_echo_check(text: str) -> str:
            normalized = unicodedata.normalize("NFKC", text).casefold()
            return "".join(
                char for char in normalized
                if not char.isspace() and not unicodedata.category(char).startswith("P")
            )

        last_self_normalized = normalize_for_echo_check(last_self_message)
        if last_self_normalized:
            for i, rep in enumerate(replies, start=1):
                if normalize_for_echo_check(rep) == last_self_normalized:
                    violations.append(
                        f"案{i}が直近の自分のメッセージをそのまま繰り返しています。"
                        "話題の単なる再掲ではなく、新しい自然な反応にしてください。"
                    )

        pressure_keywords = ["返信まだ", "返事まだ", "返事待って", "返信待って", "既読スルー", "未読スルー", "返信ない", "返事ない", "無視", "忙しいですか？", "忙しい？"]
        for i, rep in enumerate(replies, start=1):
            if any(pk in rep for pk in pressure_keywords):
                violations.append(f"案{i}に催促や返信を問い詰める表現が含まれています。追いメッセージでは催促を避け、自然な口実や軽い話題で作成してください。")

    # Normal mode: verify claims against the last direct personal-experience question.
    if mode == "normal":
        preference_topics = _personal_preference_question_topics(counterpart_message)
        supported_preferences = {
            topic for topic in preference_topics
            if any(_fact_supports_preference(fact, topic) for fact in (known_self_facts or []) if fact)
        }
        unanswered_preferences = set(preference_topics) - supported_preferences
        if {"犬", "猫"}.issubset(preference_topics) and supported_preferences.intersection({"犬", "猫"}):
            unanswered_preferences.difference_update({"犬", "猫"})
        if preference_topics and unanswered_preferences:
            for i in range(1, len(replies) + 1):
                violations.append(
                    f"案{i}に本人の好みを確認できる情報がありません。"
                    "相手の直接質問に、未確認の嗜好を事実として答えないでください。"
                    "アプリ利用者に [AI_QUESTION] で確認してください。"
                )
        elif supported_preferences:
            for i, rep in enumerate(replies, start=1):
                claimed_preferences = _preference_claim_topics(rep, preference_topics)
                unsupported_preferences = {
                    topic for topic in claimed_preferences
                    if topic not in supported_preferences
                    or (
                        _preference_polarity(rep, topic)
                        and not any(
                            _preference_polarity(fact, topic) == _preference_polarity(rep, topic)
                            for fact in (known_self_facts or []) if fact
                        )
                    )
                }
                if unsupported_preferences:
                    violations.append(
                        f"案{i}に本人の好みを確認できる情報がありません。"
                        "確認済みの好みと異なる嗜好を本人の事実として答えないでください。"
                    )

        schedule_topic = _personal_schedule_question_topic(counterpart_message)
        schedule_reference_datetime = current_datetime or datetime.now(timezone.utc)
        schedule_fact_items = []
        for index, fact in enumerate(known_self_facts or []):
            if not fact:
                continue
            if known_self_fact_timestamps is None:
                # Backward-compatible callers provide facts as current assertions.
                created_at = schedule_reference_datetime.isoformat()
            else:
                created_at = (
                    known_self_fact_timestamps[index]
                    if index < len(known_self_fact_timestamps) else None
                )
            current_fact = _current_relative_schedule_fact(fact, created_at, schedule_reference_datetime)
            if current_fact:
                schedule_fact_items.append((current_fact, created_at))
        schedule_facts = [
            fact for fact, _created_at in schedule_fact_items
            if _fact_supports_schedule_question(fact, schedule_topic, counterpart_message)
        ] if schedule_topic else []
        requested_periods = _availability_periods(counterpart_message) if schedule_topic == "availability" else set()
        supported_periods = {
            period for period in requested_periods
            if any(
                _availability_period_matches(period, known_period)
                for fact, _created_at in schedule_fact_items
                for known_period in _availability_periods(fact)
            )
        }
        missing_requested_period = bool(requested_periods and supported_periods != requested_periods)
        weekend_question = bool(requested_periods.intersection({"週末", "土日"}))
        weekend_polarities = {
            polarity
            for fact in schedule_facts
            for fact_period, polarity in _availability_claims_by_period(fact).items()
            if any(_availability_period_matches(period, fact_period) for period in requested_periods)
        }
        ambiguous_weekend_facts = weekend_question and len(weekend_polarities) > 1
        if schedule_topic and (not schedule_facts or missing_requested_period or ambiguous_weekend_facts):
            for i in range(1, len(replies) + 1):
                violations.append(
                    f"案{i}に本人の予定・生活習慣を確認できる情報がありません。"
                    "予定や起床習慣を作らず、アプリ利用者に [AI_QUESTION] で確認してください。"
                )
        elif schedule_topic == "availability":
            known_polarities_by_period = {}
            for fact in schedule_facts:
                fact_claims = _availability_claims_by_period(fact)
                for requested_period in requested_periods:
                    for fact_period, polarity in fact_claims.items():
                        if _availability_period_matches(requested_period, fact_period):
                            known_polarities_by_period[requested_period] = polarity
            for i, rep in enumerate(replies, start=1):
                reply_claims = _availability_claims_by_period(rep)
                reply_polarity = _availability_polarity(rep)
                if (
                    len(requested_periods) > 1
                    and reply_polarity
                    and re.search(r"(?:どっちも|どちらも|両方)", unicodedata.normalize("NFKC", rep))
                ):
                    reply_claims.update({period: reply_polarity for period in requested_periods})
                contradicts_period = any(
                    known_polarities_by_period.get(period) != polarity
                    for period, polarity in reply_claims.items()
                    if period in requested_periods and period in known_polarities_by_period
                )
                if len(requested_periods) <= 1 and reply_polarity and known_polarities_by_period and set(known_polarities_by_period.values()) != {reply_polarity}:
                    contradicts_period = True
                if contradicts_period:
                    violations.append(
                        f"案{i}が確認済みの予定と逆の空き状況を答えています。本人の予定と矛盾する断定をせず、情報が不十分なら利用者に確認してください。"
                    )
                reply_periods = _availability_periods(rep)
                if (
                    any(_is_specific_availability_period(period) for period in requested_periods)
                    and reply_periods and not reply_periods.issubset(requested_periods)
                ):
                    violations.append(
                        f"案{i}が相手の質問と異なる日程について答えています。確認済みの予定にない別の日付を混ぜず、質問された日程だけに答えてください。"
                    )

        for i, rep in enumerate(replies, start=1):
            question_counts = naturalness.count_meaningful_questions(rep)
            emotionally_sensitive_share = bool(re.search(
                r"(?:落ち込|つら|辛い|悲し|不安|しんど|悩ん|自信なく|疲れた|へとへと)",
                unicodedata.normalize("NFKC", counterpart_message or ""),
            ))
            if counterpart_message and emotionally_sensitive_share and question_counts["informative"] > 1:
                violations.append(
                    f"案{i}は質問を重ねすぎています。質問は会話上必要なものを一つだけにし、相手の発言にまず自然に反応してください。"
                )
            normalized_share = unicodedata.normalize("NFKC", counterpart_message or "").strip(" 　。、.!！?？")
            bare_states = {"眠い", "疲れた", "へとへと", "だるい", "忙しい"}
            unrelated_schedule_question = bool(re.search(
                r"(?:明日|今日|今週|来週|仕事|勤務|早く|早い|何時|予定)",
                unicodedata.normalize("NFKC", rep),
            ))
            if normalized_share in bare_states and question_counts["informative"] and unrelated_schedule_question:
                violations.append(
                    f"案{i}は短い状態共有への不要な質問をしています。質問を足さず、短い気遣いで返してください。"
                )
            if _is_bare_state_echo(counterpart_message, rep):
                violations.append(
                    f"案{i}は相手の状態を言い換えただけで新しい反応がありません。"
                    "同じ状態を繰り返さず、自然な労いや気遣いを短く返してください。"
                )
            if counterpart_message and _has_unverified_personal_desire(counterpart_message, rep, known_self_facts):
                violations.append(
                    f"案{i}に本人の未確認の希望を追加しています。"
                    "相手の話題をきっかけに本人の好みや希望を作らず、確認できる話題への自然な反応に直してください。"
                )
            if counterpart_message and _has_unverified_personal_habit(rep, known_self_facts):
                violations.append(
                    f"案{i}に本人の未確認の習慣・傾向を追加しています。"
                    "本人の確認済み情報にない一人称の習慣や心理傾向を作らず、相手への共感に直してください。"
                )

        factual_context = unicodedata.normalize(
            "NFKC", f"{counterpart_message}\n{chat_history_text or ''}"
        )
        work_terms = ("仕事", "お仕事", "出勤", "勤務", "職場", "残業")
        work_is_grounded = any(term in factual_context for term in work_terms)
        time_off_is_grounded = any(term in factual_context for term in ("休み", "休日", "休暇"))
        # chat_history_text contains both speakers; only explicitly collected SELF facts
        # can ground a first-person claim.
        self_fact_context = unicodedata.normalize("NFKC", "\n".join(known_self_facts or []))
        for i, rep in enumerate(replies, start=1):
            if counterpart_message and not work_is_grounded and any(term in rep for term in work_terms):
                violations.append(
                    f"案{i}に仕事の状況を確認できる情報がありません。"
                    "会話にない勤務・職場の事情を相手の眠気や疲れから推測しないでください。"
                )

            if counterpart_message and not time_off_is_grounded and re.search(
                r"(?:お?休み|休日|休暇)(?:だった|なんですね|ですね|でしたね|なんですか)", rep
            ):
                violations.append(
                    f"案{i}に休日・休暇を確認できる情報がありません。"
                    "自由時間があったことから勤務状況や休日を推測しないでください。"
                )

            transient_self_claim = re.search(
                r"(?:僕|私|自分)(?:は|も|が)?[^。！!？?]{0,16}(?:今日|昨日|最近|さっき|今|この前)"
                r"[^。！!？?]{0,16}(?:バタバタ|忙し|仕事|出かけ|寝て|体調|疲れ|予定|行って|食べ|見て|買って)",
                unicodedata.normalize("NFKC", rep),
            )
            if counterpart_message and transient_self_claim:
                claim_terms = re.findall(r"バタバタ|忙し|仕事|出かけ|寝て|体調|疲れ|予定|行って|食べ|見て|買って", rep)
                if not any(term in self_fact_context for term in claim_terms):
                    violations.append(
                        f"案{i}に本人の近況を確認できる情報がありません。"
                        "会話履歴や本人情報にない今日の行動・状態を自己開示として追加しないでください。"
                    )

        if _unresolved_status_query(counterpart_message):
            supported_status = _latest_prior_self_status(chat_history_text, counterpart_message)
            for i, rep in enumerate(replies, start=1):
                asserted_statuses, _question_statuses = _status_claims(rep)
                unsupported_statuses = [
                    status for status in asserted_statuses if status != supported_status
                ]
                if (
                    unsupported_statuses
                    or len(asserted_statuses) > 1
                    or (
                        asserted_statuses
                        and not _is_single_supported_status_reply(rep, supported_status or "")
                    )
                ):
                    violations.append(
                        f"案{i}に状況を確認できる情報がありません。"
                        "直前の関連する会話から状態を確認できないため、"
                        "推測で断定せず、相手に短く確認してください。"
                    )
                elif not asserted_statuses and not _is_short_counterpart_clarification(rep):
                    violations.append(
                        f"案{i}は不明な参照先への確認になっていません。"
                        "相手に短く確認する質問だけを返信候補にしてください。"
                    )

        experience_question = _last_experience_question(counterpart_message)
        if experience_question:
            default_action, question_match, question_clause = experience_question
            topic = _topic_before(question_clause, question_match.start())
            for i, rep in enumerate(replies, start=1):
                claim = _experience_claim(rep, topic, default_action)
                if claim and not any(
                    _fact_supports_experience_claim(fact, claim)
                    for fact in (known_self_facts or [])
                    if fact
                ):
                    violations.append(
                        f"案{i}に本人の経験を確認できる情報がありません。"
                        "事実を作った返信をせず、アプリ利用者への確認を [AI_QUESTION]質問内容[/AI_QUESTION] で返してください。"
                    )

    # 「〜とのこと」「〜と拝見」等の機械的AI表現の禁止チェック
    robotic_keywords = ["とのこと", "と拝見", "と書かれてい", "とありました"]
    for i, rep in enumerate(replies, start=1):
        if any(rk in rep for rk in robotic_keywords):
            violations.append(
                f"案{i}に「〜とのこと」「〜と拝見」等の機械的で不自然なAI表現が含まれています。"
                f"自然な口語（『〜なんですね！』『〜いいですね！』等）に修正してください。"
            )
        if re.search(r"(?:こと|の)誰かに(?:共有|話し)", rep):
            violations.append(
                f"案{i}に助詞が抜けた不自然な表現があります。"
                "『ことを誰かに共有する』のように助詞を補い、自然な日本語に直してください。"
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

    # 参照先が曖昧な場合は確認文の意味が必然的に近くなるため、
    # すべてが安全な相手向け確認質問なら、重複率だけで候補を落とさない。
    # 候補間の重複・極端な高類似の検知
    for j in range(len(replies)):
        for k in range(j + 1, len(replies)):
            sim = _jaccard_similarity(replies[j], replies[k])
            if sim >= 0.85 and not clarification_only_set:
                violations.append(f"案{j+1}と案{k+1}の内容・表現が重複しています（類似度 {sim:.2f}）。異なる会話ルートを作成してください。")

    return violations


_SAFE_REFERENCE_CLARIFICATION_FALLBACKS = (
    "え、どの件だっけ？",
    "何の話だったっけ？",
    "あれって何のこと？",
)


def _is_reference_clarification_only_failure(violations: list[str]) -> bool:
    allowed_markers = (
        "状況を確認できる情報がありません",
        "不明な参照先への確認になっていません",
        "内容・表現が重複しています",
    )
    return bool(violations) and all(any(marker in violation for marker in allowed_markers) for violation in violations)


def _build_safe_reference_clarification_candidates(
    replies: list[str],
    expected_candidates: int,
    *,
    counterpart_message: str,
    known_self_facts: list[str] | None,
    chat_history_text: str,
    tone: str,
    condition: str,
    current_datetime: datetime | None,
    mode: str = "normal",
) -> list[str] | None:
    """曖昧なstatus照会に限り、安全な相手向け確認文で不足案を埋める。"""
    if (
        expected_candidates < 1
        or mode != "normal"
        or not _unresolved_status_query(counterpart_message)
        or _latest_prior_self_status(chat_history_text, counterpart_message) is not None
        or _last_experience_question(counterpart_message) is not None
    ):
        return None

    def is_safe_clarification(candidate: str) -> bool:
        normalized = unicodedata.normalize("NFKC", candidate).strip()
        if not _is_short_counterpart_clarification(normalized) or normalized in safe:
            return False
        if validate_candidate_replies(
            [normalized],
            expected_candidates=1,
            tone=tone,
            condition=condition,
            mode="normal",
            current_datetime=current_datetime,
            counterpart_message=counterpart_message,
            known_self_facts=known_self_facts,
            chat_history_text=chat_history_text,
        ):
            return False
        return True

    safe: list[str] = []
    for candidate in replies:
        normalized = unicodedata.normalize("NFKC", candidate).strip()
        if is_safe_clarification(normalized):
            safe.append(normalized)
    # 生成側から最低1案の安全な確認質問が出ている場合にだけ補う。
    # 1案もない完全な失敗では、通常の502/no-history動作を維持する。
    if not safe:
        return None
    if len(safe) >= expected_candidates:
        return safe[:expected_candidates]

    for candidate in _SAFE_REFERENCE_CLARIFICATION_FALLBACKS:
        normalized = unicodedata.normalize("NFKC", candidate).strip()
        if is_safe_clarification(normalized):
            safe.append(normalized)
        if len(safe) == expected_candidates:
            break
    return safe if safe else None


def _build_repair_messages(
    original_messages: list[dict[str, str]],
    raw_output: str,
    violations: list[str],
    candidates: int,
    strategy_mode: str = "none",
) -> list[dict[str, str]]:
    """修復用のメッセージリストを構築する。"""
    v_text = "\n".join(f"- {v}" for v in violations)
    requires_private_experience_confirmation = any(
        "本人の経験を確認できる情報がありません" in violation
        or "本人の好みを確認できる情報がありません" in violation
        or "本人の予定・生活習慣を確認できる情報がありません" in violation
        for violation in violations
    )
    requires_reference_clarification = any(
        "参照先が会話履歴から特定できません" in violation
        or "指示語の参照先が会話履歴から特定できません" in violation
        or "状況を確認できる情報がありません" in violation
        for violation in violations
    )
    if requires_private_experience_confirmation:
        output_instruction = (
            "本人の経験・好み・予定・生活習慣・体質が会話履歴や本人情報で確認できません。返信候補を作らず、"
            "アプリ利用者にだけ、聞かれた対象に絞った短い確認質問を [AI_QUESTION]質問内容[/AI_QUESTION] の形式で1つ出力してください。"
            "別の話題や追加質問を重ねないこと。"
            "相手に送る返信候補として確認質問を作らないこと。JSON repliesや説明文は出力しないこと。"
        )
    elif requires_reference_clarification:
        output_instruction = (
            f"参照先が不明なため、状況を推測せず、相手に送る短い確認質問を含む通常のJSON repliesを{candidates}案作ってください。"
            "アプリ利用者向けの質問タグは使わないこと。"
        )
    elif strategy_mode == "tapple":
        reply_slots = ", ".join(f'"案{i + 1}"' for i in range(candidates))
        output_instruction = (
            f"不備を修正して返信候補を必ず{candidates}件作成してください。"
            f'JSON形式: {{"replies":[{reply_slots}],"strategy":{{"action":"continue|clarify|invite|wait|stop",'
            '"rationale":"根拠に基づく短い説明","evidence":["相手発言からの完全一致抜粋"],'
            '"invite_example":"安全な公共の場所を使った低圧な誘い方の例"}}。'
            "actionでinviteを選ぶ場合はinvite_exampleを必ず埋め、返信候補とは別に短く低圧で断りやすい誘い方の例を1つ示してください。"
            "人目のある公共の場所を使い、連絡先交換を提案しないでください。"
            "会話にない具体的な店名や日時を作らず、共通の活動に合う公共の場所を一般的に示せない場合はinvite以外のactionを選んでください。"
            "invite以外のactionではinvite_exampleを必ずnullにしてください。"
            "戦略の根拠がない場合はstrategyを省略してかまいません。"
        )
    else:
        output_instruction = (
            f"すべての不備を修正し、独立した完成品として{candidates}案を作成し、"
            f"必ず JSON形式の {{\"replies\": [\"案1\", \"案2\", \"案3\"]}} で出力してください。"
        )
    repair_instruction = (
        f"前回の出力に以下の不備が検知されました。\n"
        f"【不備内容】\n"
        f"{v_text}\n\n"
        f"{output_instruction}\n"
        f"※Step 17: 壊れている部分だけ直すこと。問題ない部分はそのまま残し、"
        f"文章全体を書き直さないこと（書き直すとAIっぽい説明文になりやすい）。"
    )
    return [
        *original_messages,
        {"role": "assistant", "content": raw_output},
        {"role": "user", "content": repair_instruction},
    ]


def _rank_followup_candidates(
    scored_items: list[dict], counterpart_msg: str = ""
) -> list[dict]:
    """追いメッセージ案も、体験を連想させる語ではなく品質スコア順に並べる。"""
    for item in scored_items:
        item["mild_issues"] = naturalness.count_mild_issues(
            item["reply"], counterpart_msg
        )
    return sorted(
        scored_items,
        key=lambda item: (item["final"], -item.get("mild_issues", 0)),
        reverse=True,
    )


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
    last_self_msg = ""
    if messages:
        for m in reversed(messages):
            if m["sender"] == "contact":
                last_contact_msg = m["content"]
                last_contact_msg_id = m["id"]
                break
        for m in reversed(messages):
            if m["sender"] == "self":
                last_self_msg = m["content"]
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
    effective_tone = learning.style.infer_contact_tone(hierarchical_profile, tone)
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
    same_contact_gold_block = ""
    if hierarchical_profile["same_contact_gold_samples"] >= 3:
        same_contact_gold_block = learning.style.build_same_contact_gold_pairs_block(contact_id, limit=10)

    # 5.55 Step 11: 最近そのまま送信された生成返信の実例（最大5件。なければ空）
    same_contact_gold_count = hierarchical_profile["same_contact_gold_samples"]
    accepted_block = (
        learning.contrast.build_accepted_block(contact_id, limit=5)
        if same_contact_gold_count >= 3
        or (same_contact_gold_count == 0 and hierarchical_profile["gold_samples"] == 0)
        else ""
    )
    if accepted_block:
        same_contact_gold_block = (
            f"{same_contact_gold_block}\n{accepted_block}" if same_contact_gold_block else accepted_block
        )

    # 5.56 Step 18: 同一相手への返信距離感サマリー（短い抽象ブロック。実績なしなら空）
    relationship_block = learning.style.build_relationship_summary(contact_id)
    if relationship_block:
        same_contact_gold_block = (
            f"{same_contact_gold_block}\n{relationship_block}" if same_contact_gold_block else relationship_block
        )

    self_profile = database.get_user_profile()
    known_self_facts = [
        str(m["content"] or "").strip()
        for m in messages
        if m["sender"] == "self" and str(m["content"] or "").strip()
    ]
    known_self_fact_timestamps = [
        str(m["created_at"] or "") or None
        for m in messages
        if m["sender"] == "self" and str(m["content"] or "").strip()
    ]
    if self_profile.get("my_info", "").strip():
        known_self_facts.append(self_profile["my_info"].strip())
        known_self_fact_timestamps.append(None)
    user_knowledge_text = database.get_user_knowledge_text()
    if user_knowledge_text.strip():
        known_self_facts.append(user_knowledge_text.strip())
        known_self_fact_timestamps.append(None)
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
        tone=effective_tone,
        mode=mode,
        learned_policy_block=learned_policy_block,
        positive_pairs_block=positive_pairs_block,
        contrast_block=contrast_block,
        counterpart_style_block=counterpart_style_data["summary"],
        same_contact_gold_block=same_contact_gold_block,
        same_contact_gold_samples=hierarchical_profile["same_contact_gold_samples"],
        same_contact_gold_length_median=(
            hierarchical_profile["same_contact_blended_gold_profile"].char_median
            if hierarchical_profile["same_contact_gold_samples"] >= 5
            else None
        ),
        conversation_ledger=conversation_ledger,
        counterpart_length_tier=counterpart_length_tier,
        counterpart_length_chars=counterpart_length_chars,
        my_info=self_profile.get("my_info", ""),
        user_knowledge=user_knowledge_text,
    )

    return {
        "cfg": cfg,
        "effective_tone": effective_tone,
        "custom_knowledge": custom_knowledge,
        "system_prompt": system_prompt,
        "chat_text": chat_text,
        "chat_messages": [dict(message) for message in messages],
        "last_contact_msg": last_contact_msg,
        "last_contact_msg_id": last_contact_msg_id,
        "last_self_msg": last_self_msg,
        "known_self_facts": known_self_facts,
        "known_self_fact_timestamps": known_self_fact_timestamps,
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


def _needs_question_free_variety(
    *,
    replies: list[str],
    candidates: int,
    intent: str,
    has_unresolved_question: bool,
    condition: str,
    mode: str,
) -> bool:
    """Step 16 §22: 質問不要なのに全案質問つきの場合のみ True（soft repair 用）。

    - candidates > 1（単一候補の質問は正当）の場合のみ
    - intent が report/reaction/emotional_share で未解決質問なしが条件
    - condition で質問を求める場合・followup（役割固定）は対象外
    - 質問禁止ではなく、少なくとも1案の質問なし化を促す
    """
    if candidates <= 1 or mode == "followup":
        return False
    if intent not in ("report", "reaction", "emotional_share"):
        return False
    if has_unresolved_question:
        return False
    if any(k in (condition or "") for k in ("質問して", "質問あり", "質問入り", "質問を入れて")):
        return False
    if not replies:
        return False
    return all(
        naturalness.count_meaningful_questions(r)["informative"] >= 1 for r in replies
    )


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
        try:
            conn.close()
        except Exception:
            # commit後のclose失敗でbatch_idを返せなくなると、後続失敗時に終端化できない。
            logger.exception("Failed to close generation batch creation connection")


def _mark_batch_failed(batch_id: int) -> None:
    """失敗した生成バッチを終端状態にする。更新失敗で元の生成エラーは隠さない。"""
    conn = None
    try:
        conn = database.get_conn()
        conn.execute(
            "UPDATE generation_batches SET outcome = 'generation_failed' "
            "WHERE id = ? AND outcome = 'pending'",
            (batch_id,),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to mark generation batch as failed: batch_id=%s", batch_id)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.exception("Failed to close batch failure update connection: batch_id=%s", batch_id)


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
        "requested_tone": body.tone or "auto",
        "effective_tone": ctx.get("effective_tone") or "auto",
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
    batch_state: dict[str, int] = {}
    try:
        return _generate_with_batch_tracking(body, batch_state)
    except Exception:
        batch_id = batch_state.get("batch_id")
        if batch_id is not None:
            _mark_batch_failed(batch_id)
        raise


def _generate_with_batch_tracking(body: GenerateRequest, batch_state: dict[str, int]):
    ctx = _build_context(body.contact_id, body.condition, body.tone, body.mode)
    cfg = ctx["cfg"]
    provider = factory.get_provider(cfg["provider"], cfg["api_key"])
    active_provider = provider
    active_cfg = cfg
    using_fallback = False
    is_quota_model_pair = (
        cfg.get("provider") == "gemini"
        and cfg.get("model") == "gemini-3.5-flash-lite"
        and cfg.get("fallback_provider") == "gemini"
        and cfg.get("fallback_model") == "gemini-3.1-flash-lite"
    )
    secondary_api_key = (cfg.get("secondary_api_key") or "").strip()
    quota_attempts: list[tuple[Any, dict[str, Any], str]] = []
    configured_attempts = cfg.get("quota_attempts")
    attempt_start_index = cfg.get("quota_attempt_start_index", 0)
    if (
        isinstance(configured_attempts, list)
        and configured_attempts
        and isinstance(attempt_start_index, int)
        and 0 <= attempt_start_index < len(configured_attempts)
        and all(
            isinstance(attempt, dict)
            and attempt.get("provider") == "gemini"
            and isinstance(attempt.get("model"), str)
            and isinstance(attempt.get("api_key"), str)
            and attempt.get("api_key")
            and isinstance(attempt.get("account"), str)
            for attempt in configured_attempts
        )
    ):
        provider_by_key = {(cfg["provider"], cfg["api_key"]): provider}
        for attempt in configured_attempts:
            key = attempt["api_key"]
            provider_key = (attempt["provider"], key)
            attempt_provider = provider_by_key.get(provider_key)
            if attempt_provider is None:
                attempt_provider = factory.get_provider(*provider_key)
                provider_by_key[provider_key] = attempt_provider
            attempt_cfg = dict(
                cfg,
                provider=attempt["provider"],
                model=attempt["model"],
                api_key=key,
            )
            quota_attempts.append(
                (attempt_provider, attempt_cfg, attempt["account"])
            )
        active_provider, active_cfg, _account = quota_attempts[attempt_start_index]
    elif (
        is_quota_model_pair
        and secondary_api_key
        and secondary_api_key != (cfg.get("api_key") or "").strip()
    ):
        primary_fallback_cfg = dict(
            cfg,
            model=cfg["fallback_model"],
            api_key=cfg["api_key"],
        )
        secondary_provider = factory.get_provider("gemini", secondary_api_key)
        secondary_primary_cfg = dict(cfg, api_key=secondary_api_key)
        secondary_fallback_cfg = dict(
            secondary_primary_cfg,
            model=cfg["fallback_model"],
        )
        quota_attempts = [
            (provider, cfg, "primary"),
            (provider, primary_fallback_cfg, "primary"),
            (secondary_provider, secondary_primary_cfg, "secondary"),
            (secondary_provider, secondary_fallback_cfg, "secondary"),
        ]
    system_prompt = ctx["system_prompt"]
    chat_text = ctx["chat_text"]

    # バッチ作成
    batch_id = _create_or_update_batch(
        contact_id=body.contact_id,
        trigger_message_id=ctx.get("last_contact_msg_id"),
        condition=body.condition,
        revision_instruction=body.revision_instruction,
    )
    batch_state["batch_id"] = batch_id

    if body.revision_instruction.strip() or body.original_generated.strip():
        msgs = prompt.build_revision_messages(
            system_prompt=system_prompt,
            chat_history_text=chat_text,
            condition=body.condition,
            original_generated=body.original_generated,
            revision_instruction=body.revision_instruction,
            strategy_mode=body.strategy_mode,
            candidates=body.candidates,
        )
    else:
        msgs = prompt.build_initial_generation_messages(
            system_prompt=system_prompt,
            chat_history_text=chat_text,
            candidates=body.candidates,
            mode=body.mode,
            strategy_mode=body.strategy_mode,
        )

    def _call_ai(messages: list[dict[str, str]]) -> str:
        nonlocal active_provider, active_cfg, using_fallback

        def _invoke():
            return active_provider.generate(
                model=active_cfg["model"],
                messages=messages,
                temperature=active_cfg["temperature"],
                max_tokens=active_cfg["max_tokens"],
                json_mode=body.candidates > 1 or body.strategy_mode == "tapple",
            )

        try:
            return _invoke()
        except AIError as exc:
            if exc.code == "rate_limit" and quota_attempts:
                current_index = next(
                    (
                        index
                        for index, (_candidate_provider, candidate_cfg, _account) in enumerate(quota_attempts)
                        if candidate_cfg.get("api_key") == active_cfg.get("api_key")
                        and candidate_cfg.get("model") == active_cfg.get("model")
                    ),
                    -1,
                )
                if current_index < 0:
                    raise HTTPException(
                        status_code=502,
                        detail={"code": exc.code, "message": exc.message},
                    )
                last_quota_error = exc
                for next_provider, next_cfg, next_account in quota_attempts[current_index + 1 :]:
                    active_provider = next_provider
                    active_cfg = next_cfg
                    using_fallback = True
                    logger.warning(
                        "Gemini quota exhausted; trying account=%s model=%s",
                        next_account,
                        next_cfg["model"],
                    )
                    try:
                        return _invoke()
                    except AIError as next_exc:
                        if next_exc.code != "rate_limit":
                            logger.warning("Gemini fallback failed: code=%s", next_exc.code)
                            raise HTTPException(
                                status_code=502,
                                detail={"code": next_exc.code, "message": next_exc.message},
                            )
                        last_quota_error = next_exc

                logger.warning("Gemini quota fallbacks exhausted")
                raise HTTPException(
                    status_code=502,
                    detail={"code": last_quota_error.code, "message": last_quota_error.message},
                )

            # Production defaults switch from Gemini 3.5 Flash Lite to 3.1
            # Flash Lite as soon as the primary quota is exhausted. Keep the
            # generic retry/fallback policy unchanged for every other setup.
            if exc.code == "rate_limit" and is_quota_model_pair and not using_fallback:
                fb = factory.get_fallback(cfg)
                if fb is None:
                    raise HTTPException(
                        status_code=502,
                        detail={"code": exc.code, "message": exc.message},
                    )
                fb_provider, fb_cfg = fb
                active_provider = fb_provider
                active_cfg = fb_cfg
                using_fallback = True
                logger.warning(
                    "Gemini primary rate limit reached; switching to fallback model %s",
                    fb_cfg["model"],
                )
                try:
                    return _invoke()
                except AIError as fb_exc:
                    logger.warning("fallback failed: code=%s", fb_exc.code)
                    raise HTTPException(
                        status_code=502,
                        detail={"code": fb_exc.code, "message": fb_exc.message},
                    )

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
                fb = (
                    factory.get_fallback(cfg)
                    if not using_fallback and last_exc.code in ("rate_limit", "empty_response")
                    else None
                )
                if fb is None:
                    logger.warning("AI generation failed: code=%s", last_exc.code)
                    raise HTTPException(status_code=502, detail={"code": last_exc.code, "message": last_exc.message})
                fb_provider, fb_cfg = fb
                logger.warning("primary %s exhausted, trying fallback %s", last_exc.code, fb_cfg["provider"])
                active_provider = fb_provider
                active_cfg = fb_cfg
                using_fallback = True
                try:
                    return _invoke()
                except AIError as fb_exc:
                    logger.warning("fallback failed: code=%s", fb_exc.code)
                    raise HTTPException(status_code=502, detail={"code": fb_exc.code, "message": fb_exc.message})

    contact_name = ctx.get("pieces", {}).get("contact", {}).get("name", "")
    latest_counterpart_message = ctx.get("last_contact_msg", "")
    has_unresolved_status_query = _unresolved_status_query(latest_counterpart_message)
    known_self_facts = ctx.get("known_self_facts", [])
    needs_private_experience_confirmation = _needs_private_experience_confirmation(
        latest_counterpart_message,
        known_self_facts,
    )

    def may_return_private_question(question_text: str | None) -> bool:
        if not question_text:
            return False
        if not has_unresolved_status_query:
            return True
        return needs_private_experience_confirmation and _is_private_question_for_unknown_experience(
            question_text,
            latest_counterpart_message,
            known_self_facts,
        )

    MAX_ATTEMPTS = 3
    final_parsed_replies = None
    final_strategy_raw = None
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
        if may_return_private_question(question_text):
            return {"replies": [], "history_ids": [], "question": question_text}

        # 2. Strict Parse & Auto-Sanitize (勝手に自動修正して不備を解消)
        parsed_replies = _parse_replies_strict(
            raw, body.candidates, strategy_mode=body.strategy_mode
        )
        if parsed_replies:
            parsed_replies = [
                sanitize_reply_text(r, current_datetime=datetime.now(), contact_name=contact_name)
                for r in parsed_replies
            ]
            parsed_replies = [
                ensure_has_question(r, condition=body.condition, contact_name=contact_name)
                for r in parsed_replies
            ]

        tapple_strategy = (
            _parse_tapple_strategy(raw, ctx.get("chat_messages", []))
            if body.strategy_mode == "tapple"
            else None
        )

        violations = validate_candidate_replies(
            parsed_replies,
            body.candidates,
            tone=ctx.get("effective_tone") or body.tone,
            condition=body.condition,
            mode=body.mode,
            current_datetime=datetime.now(timezone.utc),
            last_self_message=ctx.get("last_self_msg", "") if body.mode == "followup" else "",
            counterpart_message=ctx.get("last_contact_msg", ""),
            known_self_facts=ctx.get("known_self_facts", []),
            known_self_fact_timestamps=ctx.get("known_self_fact_timestamps", []),
            chat_history_text=ctx.get("chat_text", ""),
            strategy_mode=body.strategy_mode,
            tapple_action=tapple_strategy.action if tapple_strategy else None,
            conversation_messages=ctx.get("chat_messages", []),
        )
        if body.strategy_mode == "tapple":
            violations.extend(_tapple_strategy_output_violations(tapple_strategy))

        # Step 16 §22: 初回のみ、質問不要なのに全案質問つきなら soft repair を促す。
        # repair 後の再検証は Hard のみ（質問なし化はベストエフォート）。
        if (
            attempt == 1
            and not violations
            and parsed_replies
            and len(parsed_replies) == body.candidates
        ):
            _ledger = ctx["pieces"].get("conversation_ledger", {}) or {}
            if _needs_question_free_variety(
                replies=parsed_replies,
                candidates=body.candidates,
                intent=(_ledger.get("counterpart_intent") or "report"),
                has_unresolved_question=bool(_ledger.get("unresolved_question")),
                condition=body.condition,
                mode=body.mode,
            ):
                violations = [
                    "3案すべてに相手への質問が含まれています。今回の会話では質問が不要なため、"
                    "少なくとも1案は質問なしの自然な返信（相槌・共感・一言・労い）にしてください。"
                    "（質問すること自体は禁止しません）"
                ]

        # 違反がなければ即合格
        if not violations and parsed_replies and len(parsed_replies) == body.candidates:
            final_parsed_replies = parsed_replies
            final_strategy_raw = raw
            break

        # 3. 違反がある場合は1回 Repair を試行
        if not parsed_replies:
            if question_text and has_unresolved_status_query:
                violations = [
                    "参照先が会話履歴から特定できません。状況を推測せず、相手に送る短い確認質問を通常のJSON repliesに含めてください。"
                ]
            else:
                violations = [f"出力が正しいJSON形式（{{\"replies\": [...]}}）または期待される{body.candidates}案の形式になっていません。"]
        logger.info("Attempt %d validation failed: %s. Attempting repair...", attempt, violations)

        repair_msgs = _build_repair_messages(
            msgs, raw, violations, body.candidates, strategy_mode=body.strategy_mode
        )
        try:
            repair_raw = _call_ai(repair_msgs)
            repair_question = _extract_ai_question(repair_raw)
            if may_return_private_question(repair_question):
                return {"replies": [], "history_ids": [], "question": repair_question}

            repair_parsed = _parse_replies_strict(
                repair_raw, body.candidates, strategy_mode=body.strategy_mode
            )
            if repair_parsed:
                repair_parsed = [
                    sanitize_reply_text(r, current_datetime=datetime.now(), contact_name=contact_name)
                    for r in repair_parsed
                ]
                repair_parsed = [
                    ensure_has_question(r, condition=body.condition, contact_name=contact_name)
                    for r in repair_parsed
                ]
            repair_strategy = (
                _parse_tapple_strategy(repair_raw, ctx.get("chat_messages", []))
                if body.strategy_mode == "tapple"
                else None
            )
            repair_strategy_matches_replies = True

            repair_violations = validate_candidate_replies(
                repair_parsed,
                body.candidates,
                tone=ctx.get("effective_tone") or body.tone,
                condition=body.condition,
                mode=body.mode,
                current_datetime=datetime.now(timezone.utc),
                last_self_message=ctx.get("last_self_msg", "") if body.mode == "followup" else "",
                counterpart_message=ctx.get("last_contact_msg", ""),
                known_self_facts=ctx.get("known_self_facts", []),
                known_self_fact_timestamps=ctx.get("known_self_fact_timestamps", []),
                chat_history_text=ctx.get("chat_text", ""),
                strategy_mode=body.strategy_mode,
                tapple_action=repair_strategy.action if repair_strategy else None,
                conversation_messages=ctx.get("chat_messages", []),
            )
            if body.strategy_mode == "tapple":
                repair_violations.extend(
                    _tapple_strategy_output_violations(repair_strategy)
                )
            if (
                repair_violations
                and _is_reference_clarification_only_failure(repair_violations)
            ):
                safe_clarifications = _build_safe_reference_clarification_candidates(
                    repair_parsed,
                    body.candidates,
                    counterpart_message=ctx.get("last_contact_msg", ""),
                    known_self_facts=ctx.get("known_self_facts", []),
                    chat_history_text=ctx.get("chat_text", ""),
                    tone=ctx.get("effective_tone") or body.tone,
                    condition=body.condition,
                    current_datetime=datetime.now(timezone.utc),
                    mode=body.mode,
                )
                if safe_clarifications:
                    repair_parsed = safe_clarifications
                    repair_strategy_matches_replies = False
                    repair_violations = validate_candidate_replies(
                        repair_parsed,
                        body.candidates,
                        tone=ctx.get("effective_tone") or body.tone,
                        condition=body.condition,
                        mode=body.mode,
                        current_datetime=datetime.now(timezone.utc),
                        last_self_message=ctx.get("last_self_msg", "") if body.mode == "followup" else "",
                        counterpart_message=ctx.get("last_contact_msg", ""),
                        known_self_facts=ctx.get("known_self_facts", []),
                        known_self_fact_timestamps=ctx.get("known_self_fact_timestamps", []),
                        chat_history_text=ctx.get("chat_text", ""),
                        strategy_mode=body.strategy_mode,
                        tapple_action=None,
                        conversation_messages=ctx.get("chat_messages", []),
                    )
            if not repair_violations and repair_parsed and len(repair_parsed) == body.candidates:
                final_parsed_replies = repair_parsed
                final_strategy_raw = repair_raw if repair_strategy_matches_replies else None
                break
            else:
                last_violations = repair_violations or violations
                logger.info("Attempt %d repair failed. Rejecting all candidates and retrying from clean state...", attempt)
        except Exception as exc:
            logger.warning("Attempt %d repair AI call failed: %s. Rejecting all and retrying clean...", attempt, exc)
            last_violations = violations

    # Hard validation を通過した候補がない場合、不正候補を成功扱いで返さない。
    if not final_parsed_replies:
        logger.warning("All retry attempts exhausted without valid candidates: %s", last_violations)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "candidate_validation_failed",
                "message": "生成結果が検証基準を満たしませんでした。条件を変えて再度お試しください。",
            },
        )

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
    # Step 17-R6 §3: 質問必要性（NECESSARY/OPTIONAL/FORCED）を事前判定。FORCED は軽く順位を下げる。
    _r6_intent = (ledger.get("counterpart_intent") or "report").strip() or "report"
    _r6_necessity = question_necessity(
        counterpart_msg,
        _r6_intent,
        has_unresolved_question=bool(ledger.get("unresolved_question")),
    )
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
        # Step 18 §18-19: 同一相手Goldのトーン適合を最下位項として加算（±0.02）。
        # Gold 3件未満は中立。Context/Human/Personal Gold より下位。
        tone_fit = learning.contrast.contact_tone_fit(r, body.contact_id)
        final = round(final + 0.04 * (tone_fit - 0.5), 3)
        # Step 18-R4: 同一相手Goldの文量は、質が近い候補の選択にだけ使う最弱の補助項（±0.01）。
        # 少数Goldでは中立。文脈・自然さ・本人の基本文体を上書きしない。
        contact_length_fit = learning.contrast.contact_length_fit(r, body.contact_id)
        final = round(final + 0.02 * (contact_length_fit - 0.5), 3)
        # Step 17-R6 §3: FORCED（不要な文脈での質問）は軽く順位を下げる。質問そのものは禁止しない。
        _r6_q = naturalness.count_meaningful_questions(r)
        _r6_forced = _r6_necessity == "unnecessary" and _r6_q["informative"] >= 1
        if _r6_forced:
            final = round(final - 0.02, 3)
        scored_items.append({
            "reply": r, "score": s_val, "details": s_details,
            "naturalness": nat["score"], "naturalness_detail": nat,
            "human_fit": human_fit, "sent_sim": sent_sim, "final": final,
            "question_forced": _r6_forced,
        })

    # 通常モードと追いメッセージの両方で品質スコアを優先する。
    if body.mode == "followup":
        ordered_items = _rank_followup_candidates(scored_items, counterpart_msg)
        sorted_replies = [item["reply"] for item in ordered_items]
        style_scores = [item["score"] for item in ordered_items]
        naturalness_scores = [item["naturalness"] for item in ordered_items]
        human_fit_scores = [item["human_fit"] for item in ordered_items]
        sent_sim_scores = [item["sent_sim"] for item in ordered_items]
        final_scores = [item["final"] for item in ordered_items]
    else:
        _apply_diversity_nudge(scored_items)
        # Step 15-R: 同点時は軽微 issue の少ない方を上位にする（スコア自体は不変）。
        # followup は役割スロット固定のため対象外。
        for item in scored_items:
            item["mild_issues"] = naturalness.count_mild_issues(item["reply"], counterpart_msg)
        scored_items.sort(key=lambda x: (x["final"], -x["mild_issues"]), reverse=True)
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
        provider=active_cfg["provider"],
        model=active_cfg["model"],
        condition=body.condition,
        replies=sorted_replies,
        revision_instruction=body.revision_instruction,
        original_generated=body.original_generated,
        tone=ctx.get("effective_tone") or body.tone,
        counterpart_message=ctx.get("last_contact_msg", ""),
        batch_id=batch_id,
    )

    # 6.5 自動評価の保存（人間評価列は NULL のまま。後から POST /api/evaluations で付与）
    try:
        _save_auto_evaluations(
            batch_id=batch_id,
            history_ids=history_ids,
            ordered_items=ordered_items,
            counterpart_intent=(ledger.get("counterpart_intent") or "report"),
        )
    except Exception:
        # 履歴保存済みの返信自体は利用可能。補助的な評価保存だけの失敗で
        # 生成を失敗扱いにすると、履歴があるのにbatchだけ失敗状態になる。
        logger.exception("Failed to save auto evaluations: batch_id=%s", batch_id)

    effective_tone = ctx.get("effective_tone") or body.tone or (
        "hybrid" if getattr(user_style_profile_data, "hybrid_ratio", 0.5) >= 0.5
        else ("keigo" if getattr(user_style_profile_data, "keigo_ratio", 0.0) >= 0.5 else "tame")
    )

    response = {
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
    if body.strategy_mode == "tapple":
        # Strategy metadata is advisory and independently validated; a malformed
        # or absent strategy never invalidates otherwise usable reply candidates.
        strategy = (
            _parse_tapple_strategy(final_strategy_raw, ctx.get("chat_messages", []))
            if final_strategy_raw is not None
            else None
        )
        if strategy is not None:
            response["strategy"] = strategy.model_dump()
    return response


