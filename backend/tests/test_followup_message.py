"""追いメッセージ（再活性化・会話復活）生成機能のユニットテストおよびE2Eテスト。"""
import json
import re
import pytest
from app.ai import prompt
from app.routers import generation


def test_followup_system_prompt_structure():
    """追いメッセージモード時に催促禁止および3つの戦略アプローチ契約がプロンプトに含まれること。"""
    contact = {
        "name": "みお",
        "profile": "カフェ巡りとドライブが好きです！最近はパンケーキにハマってます🥞",
    }
    sysp = prompt.build_system_prompt(
        contact=contact,
        mode="followup",
        condition="カフェの話題で",
    )

    # 1. 追いメッセージディレクティブ
    assert "【追いメッセージ（再活性化・会話復活モード）】" in sysp
    assert "会話履歴から再開が自然と判断できる場合だけ話題をつなぎ" in sysp
    assert "明確な拒否や自然な終了がある場合は、新しい話題・質問で再開を迫らず" in sysp
    assert "催促の完全禁止" in sysp
    assert "返信まだ" in sysp or "催促・問い詰め表現は全案で完全禁止" in sysp

    # 2. OUTPUT CONTRACT 3戦略アプローチ
    assert "追いメッセージ出力契約" in sysp
    assert "【プロフィール・共有情報フック】" in sysp
    assert "【軽いリアクション】" in sysp
    assert "【相手の関心へのコメント】" in sysp
    assert "プロフィールだけで本人と関心・好みが共通しているとは扱わない" in sysp
    assert "未返信の質問を繰り返したり、最後の会話を単に言い換えたりしない" in sysp
    assert "プロフィールや過去の共有情報を使うかどうかは任意" in sysp
    assert "使える話題が一つなら、無理に別の話題を捏造して3案を水増ししない" in sysp
    assert "過去の話題を掘り返さず、完全に新しい切り口" not in sysp
    assert "話題を完全に切り替えて新しいフック" not in sysp
    assert "replies[0]" not in sysp
    assert "replies[1]" not in sysp
    assert "replies[2]" not in sysp


def test_followup_hard_invariant_rule_numbers_are_unique():
    """追いメッセージ用のHARD INVARIANTS内で番号を重複させない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "カフェ巡りが好きです"},
        mode="followup",
    )
    invariants = system_prompt.split("【HARD INVARIANTS】", 1)[1].split(
        "【USER INFO & KNOWLEDGE】", 1
    )[0]
    rule_numbers = re.findall(r"(?m)^\s*(\d+)\.", invariants)

    assert len(rule_numbers) == len(set(rule_numbers))


def test_followup_question_guidance_is_optional_and_low_pressure():
    """追いメッセージでは質問を禁じず、自然で答えやすい質問だけを許す。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "カフェ巡りとパンケーキが好きです"},
        mode="followup",
    )
    messages = prompt.build_initial_generation_messages(
        system_prompt=system_prompt,
        chat_history_text="相手: パンケーキ好きなんです\n自分: どんなお店に行くんですか？",
        mode="followup",
    )
    all_prompt_text = "\n".join(message["content"] for message in messages)

    assert "通常の「相手の発言に質問して会話を広げる」トーンは厳禁" not in all_prompt_text
    assert "会話を続けるためだけの質問や、返信負担になる質問は避ける" in all_prompt_text
    assert "確認済みの話題に自然につながり、短く答えやすい質問は必要な場合に限ってよい" in all_prompt_text
    assert "質問なしの短い反応も正当な選択肢" in all_prompt_text
    assert "0秒で返せるフック" not in all_prompt_text
    assert "思わず口を挟みたくなる仕掛け" not in all_prompt_text


def test_followup_prompt_does_not_require_fabricated_self_disclosure():
    """追いメッセージでも、未確認の体験や行動を本人の事実として作らせない。"""
    contact = {"name": "みお", "profile": "カフェ巡りとドライブが好きです"}
    system_prompt = prompt.build_system_prompt(contact=contact, mode="followup")
    messages = prompt.build_initial_generation_messages(
        system_prompt=system_prompt,
        mode="followup",
    )
    all_prompt_text = "\n".join(message["content"] for message in messages)

    assert "確認できない訪問・飲食・予定・体験を作ってはいけません" in all_prompt_text
    assert "各案指定のフック" not in all_prompt_text
    assert "確認できない訪問・飲食・予定・体験を作ってはいけません" in all_prompt_text
    assert "プロフィール情報は相手の好みを理解する参考であり" in all_prompt_text
    assert "自分の行動・経験の根拠にはしません" in all_prompt_text
    assert "本人の好み・経験はSELFの履歴やGold実績で確認できる場合だけ使ってください" in all_prompt_text
    assert "未確認の近況や生活状況を推測して加えない" in all_prompt_text
    assert "実際に行った・食べた・試した" not in all_prompt_text
    assert "今日食べたハンバーグ" not in all_prompt_text


def test_followup_avoids_reopening_the_same_unanswered_topic():
    """直前の未返信質問を、言い換えや細分化した質問で再開しない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "パンケーキが好きです"},
        mode="followup",
    )
    messages = prompt.build_initial_generation_messages(
        system_prompt=system_prompt,
        chat_history_text="相手: パンケーキが好きです\n自分: どんなお店に行くんですか？",
        mode="followup",
    )
    all_prompt_text = "\n".join(message["content"] for message in messages)

    assert "未返信の質問と同じ情報を求める質問や、その細分化・言い換えも避ける" in all_prompt_text
    assert "パンケーキならふわふわ系としっかり系どっち派ですか？" not in all_prompt_text


def test_followup_profile_interests_do_not_imply_ownership_or_experience():
    """プロフィールの趣味から、所有・体験・細かな好みを推測しない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "映画と猫が好きです"},
        mode="followup",
    )
    messages = prompt.build_initial_generation_messages(system_prompt=system_prompt, mode="followup")
    all_prompt_text = "\n".join(message["content"] for message in messages)

    assert "プロフィールの好みから、所有・経験・具体的な選択傾向を推測しない" in all_prompt_text
    assert "プロフィールの好みから、所有・経験・具体的な選択傾向を推測しない" in all_prompt_text
    assert "趣味に触れること自体も返信の必須条件ではありません" in all_prompt_text
    assert "プロフィールに嗜好が書かれているだけなら、所有・飼育・経験・利用の有無を質問で確認しない" in all_prompt_text
    assert "プロフィール情報には短い感想として触れられる" in all_prompt_text
    assert "【共通の関心へのコメント】" not in all_prompt_text


def test_followup_elapsed_time_questions_require_history_confirmation():
    """経過を前提にした近況質問は、履歴で期間の終了が確認できる場合に限る。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "映画が好きです"},
        mode="followup",
    )
    messages = prompt.build_initial_generation_messages(
        system_prompt=system_prompt,
        chat_history_text="自分: また時間あるときに話そう！",
        mode="followup",
    )
    all_prompt_text = "\n".join(message["content"] for message in messages)

    assert "履歴にない出来事の完了・経過を推測しない" in all_prompt_text
    assert "その出来事の期間が実際に経過したと会話履歴から確認できるときに限る" in all_prompt_text


def test_followup_does_not_force_three_distinct_conversation_directions():
    """追いメッセージは候補間の人工的な話題差を要求せず、通常返信の契約は維持する。"""
    followup_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "映画と猫が好きです"},
        mode="followup",
    )
    followup_messages = prompt.build_initial_generation_messages(
        system_prompt=followup_prompt,
        mode="followup",
    )
    followup_text = "\n".join(message["content"] for message in followup_messages)

    assert "文脈に合う3案を作成します" in followup_text
    assert "異なる3つの会話展開で3案作成します" not in followup_text
    assert "候補の違いを作るために話題や反応を無理に変えず" in followup_text
    assert "同じ話題から自然に成立する複数案を作ってよい" in followup_text
    assert "長さは内容と本人Goldから決め、短い一文で十分なら短くする" in followup_text
    assert "候補間の違いは自然に作れる範囲でよく" in followup_text
    assert "一般的な敬語を足して本人らしさや相手との距離感を損なわない" in followup_text

    normal_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "映画と猫が好きです"},
        mode="normal",
    )
    assert "文脈に合う3案を作成します" in normal_prompt
    assert "異なる3つの会話展開で3案作成します" not in normal_prompt
    assert "長さは内容と本人Goldから決め、短い一文で十分なら短くする" in normal_prompt
    assert "2〜3行程度の自然な返信が基本" not in normal_prompt


def test_followup_does_not_echo_latest_self_message_or_invent_time_context():
    """自分の直近メッセージを反復せず、時刻や会話段階を作らない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "映画が好きです"},
        mode="followup",
    )
    messages = prompt.build_initial_generation_messages(
        system_prompt=system_prompt,
        chat_history_text="自分: 落ち着いたらまた話しましょう",
        mode="followup",
    )
    all_prompt_text = "\n".join(message["content"] for message in messages)

    assert "自分の直近メッセージの要約・言い換えをしない" in all_prompt_text
    assert "時刻・日付・曜日・相手の近況が履歴で確認できない限り" in all_prompt_text
    assert "こんばんは" not in all_prompt_text
    assert "週末はゆっくりできましたか" not in all_prompt_text
    assert "推測で足さない" in all_prompt_text
    assert "初回の挨拶や関係上必要な場面以外で定型句を足さない" in all_prompt_text


def test_followup_without_contact_name_does_not_create_placeholder_address():
    """相手名未登録時に「相手さん」等の仮の呼びかけを促さない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"profile": "映画が好きです"},
        mode="followup",
    )

    assert "相手のお名前: 未設定（名前で呼びかけない）" in system_prompt
    assert "相手のお名前: 相手さん" not in system_prompt
    assert "COUNTERPART WRITING STYLE】相手への適応" in system_prompt


def test_normal_prompt_without_confirmed_contact_name_never_calls_them_san():
    system_prompt = prompt.build_system_prompt(
        contact={"name": "相手", "profile": ""},
        mode="normal",
    )

    assert "相手のお名前: 未設定（名前で呼びかけない）" in system_prompt
    assert "相手さん" not in system_prompt
    assert "仮の名前や敬称を作らない" in system_prompt


def test_normal_prompt_does_not_include_new_followup_specific_constraints():
    """追いメッセージ向けの制約追加が通常返信に波及しない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "映画と猫が好きです"},
        mode="normal",
    )
    messages = prompt.build_initial_generation_messages(system_prompt=system_prompt, mode="normal")
    all_prompt_text = "\n".join(message["content"] for message in messages)

    assert "未返信の質問と同じ情報を求める質問や、その細分化・言い換えも避ける" not in all_prompt_text
    assert "プロフィールの好みから、所有・経験・具体的な選択傾向を推測しない" not in all_prompt_text
    assert "自分の直近メッセージの要約・言い換えをしない" not in all_prompt_text


def test_normal_prompt_clarifies_unresolved_references_without_inventing_status():
    """指示語の参照先が履歴にない場合、状況を作らず短く確認するよう促す。"""
    normal_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": ""},
        mode="normal",
        chat_history_text="相手: あれどうなった？",
    )
    followup_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": ""},
        mode="followup",
        chat_history_text="相手: あれどうなった？",
    )

    assert "参照先が会話履歴から特定できない指示語" in normal_prompt
    assert "短く確認する質問" in normal_prompt
    assert "まだ決まっていない" in normal_prompt
    assert "参照先が会話履歴から特定できない指示語" not in followup_prompt


def test_unresolved_status_reference_uses_sendable_reply_candidates_not_ai_question():
    """「それどうなった？」は相手へ送る確認文として返し、UI向けタグに逃げない。"""
    history = "相手: あれどうなった？"
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": ""},
        mode="normal",
        chat_history_text=history,
    )
    messages = prompt.build_initial_generation_messages(
        system_prompt=system_prompt,
        chat_history_text=history,
        mode="normal",
    )
    complete_prompt = "\n".join(message["content"] for message in messages)

    # This case is a missing conversational referent, not a request for a
    # private clarification from the app user. It must remain normal JSON replies.
    assert "相手に送る返信候補" in complete_prompt
    assert "『何のことだった？』のように短く確認する質問" in complete_prompt
    assert "このケースでは [AI_QUESTION] を出力しない" in complete_prompt
    assert 'JSON形式の {"replies": ["案1の返信文章"' in messages[1]["content"]


def test_normal_prompt_separates_private_experience_confirmation_from_counterpart_reference():
    """本人しか答えられない経験は利用者確認、指示語の参照先は相手への確認に分ける。"""
    history = "相手: 寿司って行ったことある？\n相手: あれどうなった？"
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": ""}, mode="normal", chat_history_text=history,
    )
    messages = prompt.build_initial_generation_messages(
        system_prompt=system_prompt, chat_history_text=history, mode="normal",
    )
    full_text = "\n".join(m["content"] for m in messages)

    assert "本人の未確認の経験を尋ねる場合" in full_text
    assert "[AI_QUESTION]質問内容[/AI_QUESTION] のみ" in full_text
    assert "アプリ利用者への確認" in full_text
    assert "参照先が会話履歴から特定できない指示語" in full_text
    assert "相手に送る返信候補" in full_text
    assert "このケースでは [AI_QUESTION] を出力しない" in full_text


def test_followup_topic_rules_distinguish_repetition_from_reusing_known_information():
    """未返信質問の反復は避けつつ、確認済み情報の別角度利用は許容する。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "カフェ巡りが好きです"},
        mode="followup",
    )
    messages = prompt.build_initial_generation_messages(
        system_prompt=system_prompt,
        chat_history_text="自分: カフェよく行かれるんですか？",
        mode="followup",
    )
    system_text = messages[0]["content"]
    user_text = messages[1]["content"]

    for text in (system_text, user_text):
        assert "未返信の質問を繰り返したり、最後の会話を単に言い換えたりしない" in text
        assert "プロフィールや過去の共有情報を使うかどうかは任意" in text
        assert "使える話題が一つなら、無理に別の話題を捏造して3案を水増ししない" in text
        assert "未確認の近況や生活状況を推測して" in text

    # 支持される使い方の具体例も残し、上記の反復禁止と混同しない。
    assert "カフェ巡り好きなんですね" in system_text
    assert "プロフィールや過去の共有情報を使うかどうかは任意" in system_text
    assert "同じ話題を質問表現だけ変えて複数案にしない" in system_text
    assert "質問の有無だけで候補を差別化しない" in system_text


def test_followup_profile_hook_is_optional_and_does_not_force_questions():
    """プロフィールの話題や質問を使わない短い自然反応も候補にできる。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "カフェ巡りとパンケーキが好きです"},
        mode="followup",
    )
    messages = prompt.build_initial_generation_messages(
        system_prompt=system_prompt,
        chat_history_text="自分: 最近どうですか？",
        mode="followup",
    )
    all_prompt_text = "\n".join(message["content"] for message in messages)

    assert "プロフィールや過去の共有情報を使うかどうかは任意" in all_prompt_text
    assert "プロフィールや共有済みの話題をフックにすること" not in all_prompt_text
    assert "質問の有無だけで候補を差別化しない" in all_prompt_text
    assert "原則1案までを目安" not in all_prompt_text
    assert "複数案に含めてかまいません" in all_prompt_text
    assert "短い一文だけで自然に成立するなら、そのまま返してよい" in all_prompt_text
    assert "1文1行改行の絶対遵守" not in all_prompt_text
    assert "改行は読みやすさに応じて使うこと" in all_prompt_text


def test_followup_one_line_reaction_is_allowed_and_length_is_only_a_guideline():
    """追いメッセージで一文一行の短い反応を、長さルールで不当に排除しない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "カフェ巡りが好きです"},
        mode="followup",
    )

    followup_contract = system_prompt.split("【OUTPUT CONTRACT】", 1)[1].split(
        "【USER INFO & KNOWLEDGE】", 1
    )[0]
    assert "長さは会話に合わせて自由に選び、短い一文だけで自然に成立するならそのまま返す" in followup_contract
    assert "2〜3行にすること" not in followup_contract
    assert "必ず1文ごとに改行" not in followup_contract
    assert "短い一文だけで自然に成立するならそのまま返す" in system_prompt


def test_normal_prompt_does_not_include_followup_only_topic_rules():
    """通常返信に追いメッセージ固有の再活性化指示を混入させない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "カフェ巡りが好きです"},
        mode="normal",
    )
    assert "1文1行改行の絶対遵守" not in system_prompt
    assert "改行は読みやすさに応じて使うこと" in system_prompt
    messages = prompt.build_initial_generation_messages(system_prompt=system_prompt, mode="normal")
    all_prompt_text = "\n".join(message["content"] for message in messages)

    assert "追いメッセージ出力契約" not in all_prompt_text
    assert "追いメッセージ生成命令" not in all_prompt_text
    assert "未返信の質問を繰り返したり、最後の会話を単に言い換えたりしない" not in all_prompt_text
    assert "1文1行改行の絶対遵守" not in all_prompt_text


def test_followup_prompt_does_not_force_topic_deepening_or_new_questions():
    """追いメッセージでは話題の深掘りや新しい質問を必須にしない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "カフェ巡りが好きです"},
        mode="followup",
        conversation_ledger={"already_asked_questions": ["どこのカフェが好きですか？"]},
    )

    assert "話題を無理に広げず、質問は直近の話題に沿い会話上の意味がある場合に限って使う" in system_prompt
    assert "必ず相手が出した話題そのもの" not in system_prompt
    assert "相手の発言を踏まえた新しい自然な質問を用意すること" not in system_prompt
    assert "新しい質問は無理に用意せず、直近の会話に沿い会話上の意味がある場合に限り自然に入れてよい" in system_prompt


def test_normal_prompt_does_not_turn_old_questions_into_a_new_question_mandate():
    """既出質問は再質問防止にだけ使い、新しい質問も必須にしない。"""
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": "カフェ巡りが好きです"},
        mode="normal",
        conversation_ledger={"already_asked_questions": ["どこのカフェが好きですか？"]},
    )

    assert "【REPLY CONTENT CHOICE】" in system_prompt
    assert "会話を続けるためだけの質問は加えない" in system_prompt
    assert "質問なしで自然に終えるならそこで終え" in system_prompt
    assert "相手の発言を踏まえた新しい自然な質問を用意すること" not in system_prompt
    assert "この一覧は質問を増やす指示ではない" in system_prompt
    assert "原則1案までを目安" not in system_prompt
    assert "複数案に含めてかまいません" not in system_prompt
    messages = prompt.build_initial_generation_messages(system_prompt=system_prompt)
    full_prompt = "\n".join(message["content"] for message in messages)
    assert "質問する場合も相手が出した話題そのものを深掘りして広げること" not in full_prompt


def test_candidate_count_does_not_force_short_reply_mix_or_reaction_diversity():
    system_prompt = prompt.build_system_prompt(
        contact={"name": "みお", "profile": ""},
        mode="normal",
    )

    assert "短い候補を必ず1案以上含める" not in system_prompt
    assert "候補群の反応の焦点を変えること" not in system_prompt
    assert "短い入力というだけで内容のある状態共有を一言に縮めず" in system_prompt
    assert "自然に完結するなら短く返す" in system_prompt


def test_followup_candidates_are_ordered_by_quality_not_experience_keywords():
    """追いメッセージの候補順は料理・体験語の有無で固定しない。"""
    candidates = [
        {"reply": "プロフィールの話", "final": 0.4},
        {"reply": "一番自然な返信", "final": 0.9},
        {"reply": "短いリアクション", "final": 0.7},
    ]

    ordered = generation._rank_followup_candidates(candidates)

    assert [item["reply"] for item in ordered] == [
        "一番自然な返信",
        "短いリアクション",
        "プロフィールの話",
    ]


def test_followup_candidate_ties_prefer_fewer_mild_issues(monkeypatch):
    """同点時は軽微な問題が少ない案を先にする。"""
    mild_issue_counts = {"自然な返信": 0, "少し不自然な返信": 1}
    monkeypatch.setattr(
        generation.naturalness,
        "count_mild_issues",
        lambda reply, counterpart_msg: mild_issue_counts[reply],
    )
    candidates = [
        {"reply": "少し不自然な返信", "final": 0.8},
        {"reply": "自然な返信", "final": 0.8},
    ]

    ordered = generation._rank_followup_candidates(candidates, "相手の発言")

    assert [item["reply"] for item in ordered] == [
        "自然な返信",
        "少し不自然な返信",
    ]


def test_followup_validation_pressure_keywords():
    """追いメッセージモード時に催促・問い詰め表現が含まれる案をバリデーションで検知すること。"""
    # 正常な追いメッセージ案
    valid_followups = [
        "みおさんカフェ巡り好きなんですね笑\nパンケーキならふわふわ系としっかり系どっち派ですか？",
        "パンケーキも好きなんですね！ふわふわ系って見た目もかわいいですよね笑",
        "ドライブ好きなのいいですね！\n景色見ながら走るのって気分転換になりそうです笑",
    ]
    violations = generation.validate_candidate_replies(valid_followups, expected_candidates=3, mode="followup")
    assert violations == []

    # 催促表現が含まれる案
    invalid_followups = [
        "返信まだですか？忙しいですか？",
        "パンケーキとクレープならどっち派ですか？笑",
        "既読スルー悲しいです！返事待ってます！",
    ]
    violations = generation.validate_candidate_replies(invalid_followups, expected_candidates=3, mode="followup")
    assert len(violations) >= 2
    assert any("催促や返信を問い詰める表現" in v for v in violations)


def test_e2e_generate_followup_mode(client, monkeypatch):
    """E2Eで mode='followup' を指定して生成APIが正常に動作すること。"""
    cid = client.post("/api/contacts", json={"name": "追いテスト相手", "profile": "旅行とカフェ"}).json()["id"]
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "contact", "content": "はじめまして！よろしくお願いします"})
    client.post(f"/api/contacts/{cid}/messages", json={"sender": "self", "content": "はじめまして！\nカフェよく行かれるんですか？", "source": "manual"})

    class FakeProvider:
        name = "fake"

        def generate(self, *, model, messages, temperature, max_tokens, json_mode=False):
            all_str = "".join(str(m) for m in messages)
            assert "追いメッセージ" in all_str
            assert "プロフィール・共有情報フック" in all_str
            assert "相手の関心へのコメント" in all_str
            assert "本人と関心・好みが共通しているとは扱わない" in all_str

            return json.dumps({
                "replies": [
                    "みおさんカフェ巡り好きなんですね笑\nパンケーキならふわふわ系としっかり系どっち派ですか？",
                    "パンケーキも好きなんですね！ふわふわ系って見た目もかわいいですよね笑",
                    "ドライブ好きなのいいですね！\n高速と下道ならどっちを走るのが好きですか？",
                ]
            })

        def available_models(self):
            return []

    validator_calls = []
    original_validator = generation.validate_candidate_replies

    def capture_validator_call(*args, **kwargs):
        validator_calls.append(kwargs.get("last_self_message"))
        return original_validator(*args, **kwargs)

    monkeypatch.setattr(generation, "validate_candidate_replies", capture_validator_call)
    monkeypatch.setattr("app.routers.generation.factory.get_provider", lambda *a, **k: FakeProvider())
    monkeypatch.setattr("app.routers.generation.get_ai_config", lambda: {
        "provider": "fake",
        "model": "fake-model",
        "api_key": "x",
        "temperature": 0.8,
        "max_tokens": 512,
        "history_limit": 50,
    })

    r = client.post("/api/generate", json={"contact_id": cid, "mode": "followup", "candidates": 3})
    assert r.status_code == 200
    data = r.json()
    assert len(data["replies"]) == 3
    assert validator_calls and all(
        message == "はじめまして！\nカフェよく行かれるんですか？"
        for message in validator_calls
    )
    assert set(data["replies"]) == {
        "みおさんカフェ巡り好きなんですね笑\nパンケーキならふわふわ系としっかり系どっち派ですか？",
        "パンケーキも好きなんですね！ふわふわ系って見た目もかわいいですよね笑",
        "ドライブ好きなのいいですね！\n高速と下道ならどっちを走るのが好きですか？",
    }
