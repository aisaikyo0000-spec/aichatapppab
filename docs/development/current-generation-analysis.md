# 現行返信生成アーキテクチャ分析（Step 1: 調査のみ・コード変更なし）

- 調査日: 2026-09-27
- 対象リポジトリ: `aisaikyo0000-spec/aichatapp`（ローカル: `C:\Users\proje\Desktop\AIチャットアプリ`）
- 方針: コード改修なし。実装の実態（ファイル名・関数名つき）を記録する。
- バージョン表記の実態: `backend/app/config.py` では `APP_BUILD_VERSION = "learned-reply-v4.0"` / `PROMPT_VERSION = "v4.0"`。`backend/app/ai/prompt.py` の docstring は `Conversation-Learned Reply System v3.1`、`build_system_prompt()` の docstring は `v3.8` と混在。`backend/app/learning/__init__.py` は `Learning Package v3` と表記。依頼文の `learned-reply-v3.1` とコード上の表記にズレがあるため、本書では実ファイルの内容を正とする。

## 1. 現在の生成フロー（`POST /api/generate` 起点・実関数名つき）

### 1.1 エントリポイント〜バッチ〜コンテキスト構築

1. `POST /api/generate` → `backend/app/routers/generation.py: generate()`（`GenerateRequest`: `backend/app/schemas.py`）
   - リクエスト: `contact_id / condition / candidates(1-3) / revision_instruction / original_generated / tone(keigo|hybrid|tame) / mode(normal|followup)`。
2. `generation.py: _build_context(contact_id, condition, tone, mode)` が以下を順に実行する（`generation.py:1235-1371`）:
   1. `backend/app/ai/config.py: get_ai_config()`（全体設定: DB `settings` > `.env` > デフォルト）＋ `get_contact_ai_config(contact_id, ...)`（相手別上書き: `contact_settings`＋`contact_knowledge_files`）。
   2. `contacts / messages / contact_images` を取得。会話履歴全文を `backend/app/ai/prompt.py: format_chat_history(..., limit=0)` で整形（`generation.py:1264`。履歴は全件投入、件数制限なし）。
   3. `prompt.load_knowledge_texts("rules"/"references")`、`prompt.cap_learning(prompt.load_knowledge_pairs("training"))`（予算6000文字・`01`〜`10`接頭辞順）を取得するが、**現行の `build_system_prompt()` には `rules/references/learning_materials/training_examples` 引数が渡されていない**（互換用引数として残るのみ。実際に読まれるのは `condition/chat_history/learned_policy/positive_pairs/contrast/counterpart/gold/ledger/my_info/user_knowledge` 系）。`_load_training_examples()`（`generation.py:1213`）も `preview` 用に読まれるだけで生成プロンプトには未投入。
   4. `learning.corpus.build_turns_for_contact(contact_id)` で Turn 構築 → `learning.corpus.classify_phase(turn_index=total_turns, ...)` で現フェーズ判定（`generation.py:1275-1297`）。
   5. `learning.style.compute_hierarchical_profile(contact_id, current_phase)` → `learning.style.to_learned_policy_prompt(...)`（`generation.py:1300-1301`）。
   6. `learning.retrieval.retrieve_relevant_pairs(query_text=last_contact_turn, ...)`（limit=4）→ `to_positive_pairs_prompt_block(...)`（`generation.py:1304-1310`）。
   7. `learning.contrast.extract_contrast_examples(contact_id, limit=2)` → `to_contrast_prompt_block(..., max_items=2)`（`generation.py:1313-1314`）。
   8. `generation.py: analyze_counterpart_style(contact_id)`（相手スタイル要約）。
   9. `learning.style.build_same_contact_gold_pairs_block(contact_id, limit=10)`（同相手直近 Gold 原文、最大10件）。
   10. `database.get_user_profile()`（`my_info`）＋ `database.get_user_knowledge_text()`。
   11. `prompt.build_conversation_state_ledger(messages, condition)`（会話状態 Ledger 構築）。
   12. `prompt.build_system_prompt(...)` で 8 ブロックのシステムプロンプトを構築（`generation.py:1334-1348`）。
3. `generation.py: _create_or_update_batch(...)`（`generation.py:1374-1412`）で `generation_batches` に `outcome='pending'` で INSERT。再生成時（`revision_instruction` 非空）は直近の `pending/regenerated` バッチを `regenerated` に更新し、`parent_batch_id/attempt_no` を連鎖させる。
4. 初回は `prompt.build_initial_generation_messages(system_prompt, ...)`、修正再生成は `prompt.build_revision_messages(...)` で LLM 用メッセージ配列を構築。

### 1.2 LLM 呼び出し〜パース〜検証〜修復リトライ

5. Provider 解決: `backend/app/ai/factory.py: get_provider(cfg["provider"], api_key)`。登録済みは `cerebras`（`backend/app/ai/cerebras.py`、既定 `gpt-oss-120b`）/ `nvidia`（`backend/app/ai/nvidia.py`）/ `gemini`（`backend/app/ai/gemini.py`。OpenAI 互換 endpoint、`MIN_MAX_TOKENS=2048` の下駄あり、`json_mode` 対応）。既定設定は `backend/app/config.py: DEFAULT_PROVIDER="cerebras"` ほか。
6. `generate()` 内 `_call_ai()`（`generation.py:1561-1603`）が `provider.generate(model, messages, temperature, max_tokens, json_mode=(candidates>1))` を呼ぶ。`AIError(code=empty_response|rate_limit)` のみ 2 回スリープリトライし、該当コード時のみ `factory.get_fallback(cfg)` を試す。それ以外は即 502。
7. 最大 3 試行ループ（`generation.py:1611-1692`）:
   1. `_extract_ai_question(raw)`（`generation.py:38`）が全文 `[AI_QUESTION]...[/AI_QUESTION]` の場合のみ質問抽出して早期 return（`replies=[]`）。
   2. `_parse_replies_strict(raw, candidates)`（`generation.py:49`）で厳格パース（JSON `replies|candidates|list` → `【案N】/案N:/N.` ラベル分割 → 空行ブロック分割。改行フォールバックは排除。件数不一致は失敗扱い）。
   3. `sanitize_reply_text(...)`（`generation.py:202`。季節矛盾・禁止カタカナ語・催促・AI 定型・話題逃げ・`とのこと/と拝見` 等の自動置換）→ `ensure_has_question(...)`（`generation.py:293`。質問なし案に `「{contact_name}さんは/最近どうですか？笑」` を自動付与。`condition` に「質問しない」系キーワードがある場合のみスキップ）。
   4. `validate_candidate_replies(...)`（`generation.py:307`。案数・空文・`[AI_QUESTION]` 混入・3分割・トーン矛盾・followup 催促・`とのこと/と拝見`・`ほかにも/ほかに/〜以外/もいいですけど`・質問必須・季節矛盾・案間 Jaccard 0.85 以上重複を検査）。
   5. 違反があれば `_build_repair_messages(...)` で repair 指示を 1 回投げて再パース・再検証。ダメなら破棄して次 attempt へ。
   6. 3 試行尽きても空にしないフォールバックあり（`generation.py:1695-1702`。最後のパース結果をそのまま採用するため、違反残存のまま返る可能性がある）。
8. 後処理: `prompt.strip_brackets()`（`「」『』【】` 除去）→ `prompt.format_one_sentence_per_line()`（1文1行化）。

### 1.3 スコアリング〜保存〜応答

9. Soft Style Scoring: `generation.py: score_candidate_style(reply, active_profile)`（`generation.py:951`。文体類似度 0.0〜1.0。内訳: 文字数 30%・文数 20%・トーン 20%・記号絵文字 15%・質問終了 15%。品質・事実性の判定ではない）。`normal` はスコア降順ソート、`followup` は `_align_followup_replies()`（`generation.py:433`。行動報告/軽快ツッコミ/体験共有のスロット整列）で順序固定。
10. `generation.py: _record_history(...)`（`generation.py:1415`）で候補ごとに `generation_history` へ 1 レコード INSERT（`batch_id/counterpart_message/tone` 付き）。`revised_text` は再生成時のみ元案文で埋まる。
11. 応答: `{replies, history_ids, style_scores, batch_id, build_version, prompt_version, requested_tone, effective_tone, tone_validation}`。Frontend（`frontend/src/components/GenerationPanel.tsx: generate()` → `frontend/src/api.ts: api.generate()`）が 3 案カード表示・コピー/送信・修正再生成を行う。

### 1.4 送信・評価とバッチ状態遷移

- AI 案送信: `POST /api/contacts/{id}/messages`（`backend/app/routers/messages.py: create_message()`）で `source='generated'`＋`generation_history_id` 必須、本文が `generated_text/revised_text` と完全一致の場合のみ採用。同一トランザクションで `generation_history.is_sent=1/is_adopted=1`、対応 `generation_batches.outcome='candidate_sent'`＋`selected_history_id` を更新。
- 手入力送信: `sender='self' + source='manual'` の場合、直近の `pending/regenerated` バッチを `outcome='manual_replaced'`＋`replacement_message_id` に更新（`messages.py:129-140`）。これが Contrast Learning の入口。
- 評価: `PATCH /api/history/{id}`（`backend/app/routers/history.py`）で `is_adopted/is_copied/is_sent/rating(good|neutral|bad)/rating_reason` を更新。`rating='bad'` は Corpus の negative 判定と `analyze_user_learned_style` の除外に効く。
- 診断: `GET /api/learning/diagnostics`（`generation.py:1492`。コーパス内訳・採用 tier・contrast 件数・`candidate_sent_rate/manual_replaced_rate/avg_attempts_to_send`）。プレビュー: `POST /api/generate/preview`（`generation.py:1461`。LLM を呼ばず投入内容を返す）。

## 2. 現在の Learning 構造（Corpus / Style / Retrieval / Contrast）

### 2.1 Corpus（`backend/app/learning/corpus.py`）

- `build_turns_for_contact(contact_id)`: 同一 sender の連続メッセージを 1 Turn に結合。Turn の `source` は末尾メッセージの `source` を代表値とする。時刻単独行のみ除去（`clean_chat_content`）。
- `extract_reply_pairs(contact_id=None)`: `contact Turn → 直後 self Turn` のみ教師ペア化。前後 2 Turn を `context_turns` として保持。ラベル規則:
  - `generation_history_id` が `rating='bad'` に紐づく → `negative`
  - `self Turn.source == 'manual'` → `gold`
  - `source == 'generated'` → `silver`
  - その他（`imported/legacy_unknown` 等）→ `bronze`
  - 除外: self/contact 同一文（sender 誤登録疑い → `excluded=True`＋`negative`扱い）、`{"replies"`/`[AI_QUESTION]` 混入は skip。
- `classify_phase()`: キーワード優先の決定論ルール（opening → scheduling → getting_to_know → light_reaction → turn_index<=2 は opening → ongoing）。意味理解ではなく文字列マッチ。
- `extract_same_contact_manual_gold_pairs(contact_id, limit=10)`: 同相手 Gold の直近 N 件。
- `get_corpus_statistics()`: gold/silver/bronze/negative/excluded 件数を集計。
- 注意: `generation.py` 内にも旧来の類似実装 `build_user_reply_pairs()` / `analyze_user_learned_style()` / `retrieve_relevant_reply_pairs()` / `load_user_approved_reply_examples()` が残存しているが、**現行 `_build_context()` が実際に使うのは `learning.corpus/style/retrieval/contrast` 側**（旧 `retrieve_relevant_reply_pairs` は生成フローから未参照。`analyze_user_learned_style` は `analyze_counterpart_style` と共存し、style 系は `learning.style` が正系）。

### 2.2 Style（`backend/app/learning/style.py`）

- `compute_style_metrics(texts)`: 文字数・行数・文数の中央値＋IQR、keigo/hybrid/tame 排他判定比率、`。/！/？/笑・w`/絵文字平均・頻出絵文字・`さん付け`率・一人称（僕/俺/自分）を集計。
- `compute_hierarchical_profile(contact_id, current_phase)`: 採用 tier を以下で決定。
  1. `same_contact_recent_manual_gold`（同相手直近 Gold 1 件以上。直近5件の統計）
  2. `same_contact_all_manual_gold`（同相手全 Gold 1 件以上）
  3. `contact_specific`（同相手全 self 3 件以上。Silver/legacy 混じり）
  4. `global_manual_gold`（全体 Gold 5 件以上）
  5. `phase_specific`（同 phase 5 件以上）
  6. `global`（全体 self）
- `to_learned_policy_prompt(...)`: 採用 tier・文量中央値/IQR・トーン記述・`。/笑/質問率`/絵文字・「相手の発言に直接共感し、その話題そのものを直接深掘りする質問で展開」「`〜とのこと/拝見/ほかに/ほかにも/〜以外/〇〇もいいですけど` は実績ゼロのため完全禁止」等のポリシー文を生成。**構造的な会話戦略ではなく、文体統計＋禁止例＋深掘り質問の推奨が中心**。
- `build_same_contact_gold_pairs_block(contact_id, limit=10)`: 同相手の `相手→自分(手入力正解)` 原文ペアを最大10件そのまま載せる。**本人らしさへの影響が最も直接的**（逐語模倣の材料）。ただしランキング・ relevance 選別はなく直近順の素朴な掲載。

### 2.3 Retrieval（`backend/app/learning/retrieval.py`）

- `score_pair_relevance(query, pair, contact_id, phase)`: 文字 bi-gram Jaccard×0.4 ＋ トピック一致ボーナス（最大0.5）＋ source 重み（gold 1.0/silver 0.6/bronze 0.3 ×0.3）＋ 同相手 +0.2 ＋ phase 一致 +0.15/不一致 -0.20。`excluded/negative` は -1.0 で排除。
- `retrieve_relevant_pairs(query=last_contact_turn, limit=4)`: クエリ文が `contact_turn` に部分一致するペアは leave-one-out で除外。スコア降順＋ self 文重複排除で最大4件。**クエリは相手の直近 Turn テキストのみ**（condition・会話全体要約・感情・意図は使わない）。
- `to_positive_pairs_prompt_block(...)`: `実例N [LABEL/同相手or他相手/Phase]` 形式で `相手→自分` 原文を掲載。「最優先の文体・展開の教師として参照」と指示するが、**どの点を真似すべきか（語尾だけか、質問構造までか）の粒度指定はない**。

### 2.4 Contrast（`backend/app/learning/contrast.py`）

- `extract_contrast_examples(contact_id, limit=5)`: `generation_batches.outcome='manual_replaced'` かつ `replacement_message_id` の本文があるバッチを直近順に取得。各バッチの `generation_history.generated_text` 群を「棄却案」、`replacement` 本文を「採用手入力」とする。**使われるのは直近2件まで**（`_build_context` で `limit=2`、`to_contrast_prompt_block(max_items=2)`）。
- `_summarize_difference(rejected, chosen)`: 自動要約は 4 軸のみ（長さ差±30文字・質問有無の反転・`笑` の有無・`。` の有無）。それ以外は「より自然で本人らしい口語表現への書き換え」の定型文。**トークンレベルの差分・失敗パターン分類（オウム返し/質問過多/共感過多等）・重要度重みはなし**。
- プロンプト掲載は `事例N: 相手発言(80字) / 避けた生成案の傾向(要約1行) / 実際に送った自然な返信(全文)` のみ。**棄却案の原文自体は載らない**ため、LLM は「何がダメだったか」を要約越しにしか知れない。

## 3. 現在の Prompt 構造（AI へ実際に渡るもの）

`prompt.build_system_prompt()`（`backend/app/ai/prompt.py:312`）は 8 ブロック構成。`build_initial_generation_messages()` の user 指示と合わせると以下になる。

1. `ROLE & CURRENT DIRECTIVE / REPLY DIRECTIVE`: 季節（`get_current_season_info`）、`condition`（Priority 1。ただし「自然な返信にすること」を理由に指示を変更・省略するな、というメタ指示つき）、tone Hard Lock（tame/keigo/hybrid）、followup 専用指令（古い話題の蒸し返し禁止・催促禁止・新フック必須）。
2. `HARD INVARIANTS / CORE RULES`（16条＋本人情報）: さん付け・話者分離・架空自己開示禁止・既出重複禁止・不自然カタカナ語禁止・MULTI-TOPIC（複数話題は絞る）・未知事項は `[AI_QUESTION]` のみ出力・1文1行改行・**質問必須（9条。各案必ず1つ以上の質問。`condition` に質問不要の明示がある場合のみ免除）**・履歴参照・3段構成フロー（normal: `反応→自己開示→質問` 2〜4行 / followup: 新フック 2〜3行）・文体再現・AI 定型禁止・季節矛盾禁止（受動）・**話題深掘り固定（14条。`ほかにも/ほかに/〜以外/もいいですけど` 禁止＋相手の話題そのものを深掘りせよ）**・**`とのこと/と拝見` 禁止（15条）**・`何か→なにか`（16条）・`my_info/user_knowledge/SELF プロフィール`。
3. `CHAT HISTORY / CONTACT & CHAT HISTORY` ＋ `CONVERSATION STATE & CONTEXT CONTINUATION`: 相手名・清掃済みプロフィール・Ledger（最優先返答対象＝相手直近メッセージ全文、直近質問、既出質問リスト最大10件＋再質問厳禁）・**会話履歴全文**（`limit=0`）。
4. `LEARNED USER RESPONSE POLICY` ＋ `SAME-CONTACT RECENT GOLD REPLIES`: Style 統計ポリシー＋同相手 Gold 原文（最大10ペア）。本人らしさの主材料。
5. `POSITIVE REPLY PAIRS / RETRIEVED USER REPLY PAIRS`: 検索 4 件の原文ペア。なければ `(該当なし - LEARNED USER RESPONSE POLICY を基準に作成)`。
6. `RELEVANT CONTRAST`: 直近 manual_replaced 最大2件の要約＋採用文。なければブロック自体なし。
7. `COUNTERPART STYLE ADAPTATION`: `analyze_counterpart_style` 要約（口調・文量・絵文字・笑/！/質問率＋「20〜30%適応・オウム返し禁止」）。データ不足時は定型文。
8. `OUTPUT CONTRACT / FINAL TASK`: normal は独立3案・切り口相違・1文1行・質問必須・機械表現禁止・`なにか` 表記・JSON `{"replies": [...]}` のみ。followup は `replies[0]=行動報告 / [1]=軽快ツッコミ / [2]=体験共有` の役割固定＋催促禁止＋2〜3行。
- user 側最終指示（`build_initial_generation_messages`）は上記の再掲＋「文体・語尾・記号を最優先で忠実に再現」「架空開示禁止」「1返信の3分割禁止」「JSON のみ」。
- 評価: **「自然に話せ」という方向の指示は多いが、構造的な会話情報は Ledger の7項目＋履歴全文＋統計ポリシーに留まる**。相手の意図・感情・会話目標・未解決ループ・直近の応答パターン等の明示的スロットは存在しない（詳細は §4）。

## 4. 現在の Conversation State（Ledger の実態）

実装: `prompt.build_conversation_state_ledger(messages, condition)`（`backend/app/ai/prompt.py:238-289`）。決定論的・LLM 不使用。返却キーは以下7つのみ。

| 要求項目 | 実態 |
|---|---|
| `current_topic` | あり。ただし相手直近メッセージの先頭60文字をそのまま貼るだけ（要約・正規化なし）。履歴なし時は `"会話開始"`。 |
| `conversation_goal` | **なし**（仲を深める/デート打診/盛り上げ等の目標スロットなし）。 |
| `counterpart_intent` | **なし**（情報共有/質問/誘い/雑談等の意図分類なし。`unresolved_question` が質問行の有無だけを見る）。 |
| `counterpart_emotion` | **なし**（感情・温度感の推定なし。`analyze_counterpart_style` は文体統計のみ）。 |
| `open_loops` | **なし**（未回収の話題・約束・持ち越し事項の追跡なし）。 |
| `recent_intents` | **なし**（直近ターンの意図履歴なし）。 |
| `recent_questions` | 部分的。`already_asked_questions` として自分が過去に送った疑問文を最大10件保持（正規化は空白・`?？!！` 除去のみ）。相手からの質問履歴・回答済み判定はなし。 |
| `recent_response_patterns` | **なし**（自分の直近返信の型・語尾・質問パターンの記録なし。繰り返し検出の材料がない）。 |

- 既存キー: `known_contact_facts`（相手の直近10通を素朴に列挙。事実抽出ではなく原文貼り付け）、`known_self_facts`（自分の直近10通の原文列挙）、`already_asked_questions`（上記）、`current_topic`、`unresolved_question`（直近相手文の最初の疑問文行のみ）、`last_contact_message`（全文）、`latest_user_intent`（`condition` 素通し、空なら `"自然な返信"`）。
- プロンプト反映は `build_system_prompt` 内の Block 3 のみ（最優先返答対象・要回答質問・既出質問リスト）。**重複質問の防止はここのみ**で、語尾・共感・質問型の繰り返し防止機構はない。

## 5. 現在の Manual Replacement Learning（差分学習の到達点）

フロー（実装済み）:

```text
AI生成（_record_history: history複数件＋batch pending）
 ↓
ユーザーが3案を捨てて手入力送信（messages.create_message: source='manual'）
 ↓
直近 pending/regenerated バッチ → outcome='manual_replaced'＋replacement_message_id（messages.py:129-140）
 ↓
次回生成時に extract_contrast_examples(limit=2) で取得
 ↓
_summarize_difference（長さ/質問/笑/句点の4軸）＋採用文全文を RELEVANT CONTRAST として prompt へ（最大2件）
 ↓
同時に手入力文は messages.source='manual' として Corpus の gold に昇格し、Style/Retrieval/Gold実例にも反映
```

- 到達点: 「捨てられた案群」と「採用された手入力文」のペア保存・プロンプト再提示の器はある。手入力文は Gold として恒久的に再利用される。
- 未到達点（差分を学べていない部分）:
  - 棄却案の原文がプロンプトに載らず、要約1行（4軸＋定型文）に圧縮されるため、**「AIが何を間違えたか」の具体が LLM に伝わらない**。
  - 要約軸が固定4軸で、問題リスト A〜I（オウム返し/共感過多/話の広げ過ぎ/長さ不整合/繰り返し等）を分類できない。
  - `rating='bad'` の history は Corpus で `negative` 扱い・Retrieval から除外されるが、**生成時の忌避例としてプロンプトに載らない**（除外するだけで教訓化しない）。
  - `rating_reason` は `database.get_learned_preferences()` で集計される関数があるが、**現行 `_build_context()` から呼ばれておらずプロンプトに未投入**（到達不能コードに近い）。
  - 置換理由（修正指示・どこが気に入らなかったか）の記録欄がなく、`revision_instruction` は再生成フロー用で manual_replaced には付随しない。

## 6. 自然性に関する問題点（優先度つき・未修正）

### P0（最重要）

- **P0-1 質問の強制が毎回質問・話の広げ過ぎを構造的に生む**: `CORE RULES 9条`＋`OUTPUT CONTRACT 4条`＋`validate_candidate_replies` の質問必須検査（`generation.py:380-386`）＋ `ensure_has_question` の自動付与（`generation.py:293-304`。フォールバック文は `「{contact_name}さんは/最近どうですか？笑」` の定型）が三重に質問を強制する。情報共有だけの相手（質問なし・短文）にも必ず質問が付くため、問題 B がデフォルト動作になる。`condition` に「質問しない」系の明示がない限り質問なし返信は生成・通過できない。
- **P0-2 3段構成の固定が過剰な付加情報を生む**: normal の `flow_rule`（`prompt.py:406`）が `反応→自己開示→質問` を全案に要求する。相手が「今日ラーメン食べた」の一言でも、自己開示1文＋質問1文が必須となり問題 C（`どこの？何ラーメン？私も好き` 型の膨張）を誘発する。短文への短文返し・相づちで終える選択肢がプロンプト上に存在しない。
- **P0-3 繰り返し・オウム返しを検出する機構がない**: 案間重複は Jaccard 0.85（`generation.py:402-406`）のみで、①相手文と生成文の類似（オウム返し・問題 E）②直近の自分の送信文と生成文の類似（問題 H）③共感冒頭の定型反復（問題 F）の検査がない。Ledger に `recent_response_patterns` がなく、`ensure_has_question` のフォールバック質問文も毎回同型のため、繰り返しが蓄積する。

### P1（重要）

- **P1-1 AI っぽさの禁止リストが狭い**: Hard な禁止は `とのこと/と拝見/と書かれてい`・`ほかにも/ほかに/〜以外/もいいですけど`・季節矛盾・催促・`リフレッシュ` 等に限定（`validate_candidate_replies`＋`sanitize_reply_text`）。問題 A（`そうなんですね！/それは大変でしたね/ちなみに〜どうですか？`）、問題 F（`お疲れ様でした/わかります` 冒頭の毎回反復）を止める頻度制御・冒頭パターン検査がない。`sanitize` は置換後に再検証しない `ensure_has_question` との順序依存もある。
- **P1-2 共感＋質問の同型反復**: Style ポリシー（`style.py:298`）が「直接共感＋その話題の直接深掘り質問」を一律推奨し、Retrieval の上位例も同型に寄る。相手の意図（報告/相談/雑談）別の応答型の切り替え指示がなく、問題 D（噛み合わない）・F（過剰共感）を生みやすい。
- **P1-3 長さの不整合**: Style の文量規範は自分の履歴中央値/IQR のみで、**相手の直近文量との相対適応がない**（相手が 10 文字でも中央値 60 文字の 3 段構成を出す）。`score_candidate_style` の文字数項も履歴適合を見るだけで、相手文とのバランスを見ない。問題 G の直接原因。
- **P1-4 Retrieval が表層類似のみ**: bi-gram Jaccard＋キーワード一致＋phase/source ボーナスで、意図・感情・会話段階の意味的適合を見ない。`query=相手直近 Turn のみ` のため、皮肉・冗談・落ち込んだ報告等の取り違え（問題 D）を防げない。
- **P1-5 Contrast の教訓が弱い**: §2.4 の通り要約4軸＋最大2件＋棄却原文なし。manual_replaced が溜まっても「何を直されたか」の学習が粗く、手修正ループが収束しにくい。

### P2（改善候補）

- **P2-1 Memory の無差別投入**: `my_info/user_knowledge/SELF プロフィール/相手プロフィール/履歴全文` が毎回全量投入され、関連度ゲーティングがない。問題 I（覚えているアピールのための不自然想起）を助長する。`get_user_knowledge_text()` の破綻メモ除外はキーワード方式のみ。
- **P2-2 Phase 分類の粗さ**: `classify_phase` はキーワード先勝ち（挨拶語が含まれるだけで opening 等）。フェーズ誤判定が Retrieval の phase bonus と Style の phase profile に波及する。
- **P2-3 同相手 Gold の無選別**: 直近10件の素朴掲載で、現在の話題との関連選別・重みづけがない。古い話題の語尾が混ざると不自然な模倣を誘う。
- **P2-4 検証・修復が形式中心**: `validate_candidate_replies` は形式・禁止語・質問有無・重複のみで、自然さ・噛み合い・意図適合の評価がない。Repair も形式違反時のみで、品質改善ループではない。`score_candidate_style` は文体のみで意味を見ず、しかもフィルタではなく並べ替えのみ。
- **P2-5 `knowledge/rules・references・training` の生成プロンプト未投入**: `_build_context` で読み込むが `build_system_prompt` に渡していない（`generation.py:1334-1348`）。`knowledge/training/03_質問の価値判定と質問なし返信.txt` 等の「質問しない勇気」の教材があっても生成に効いていない可能性が高い。`training_examples` テーブル・`load_user_approved_reply_examples` も同様に生成フロー未使用。
- **P2-6 followup の役割固定の硬直性**: `_align_followup_replies` のキーワード分類（冬眠/夏眠/パフェ/ラーメン等）がハードコードで、話題とずれると不自然な整列になる。

## 7. 改修候補（将来導入時の拡張点・実装はしない）

| 候補 | 現在の拡張点（ファイル・関数） | 備考 |
|---|---|---|
| Conversation State 拡充 | `prompt.build_conversation_state_ledger`（`ai/prompt.py:238`）の返却 dict 拡張＋ `build_system_prompt` Block 3 の描画拡張。`recent_response_patterns` 用に直近 self 文の取得（`messages` テーブル読取は既存流用）が必要 | `conversation_goal/counterpart_intent+emotion/open_loops/recent_intents` は新規推論（決定論 or 軽量 LLM）が必要。現状は質問行検出程度しかない |
| Response Intent（応答型選択） | `prompt.py: flow_rule`（`ai/prompt.py:403-407`）の分岐化＋ `build_initial_generation_messages` の user 指示。Ledger の intent を入力に使う | 「共感のみ/質問あり/切り上げ/提案」等の型をまず定義し、P0-1/P0-2 の強制（9条・flow_rule・`ensure_has_question`）と整合させる必要あり |
| Naturalness Judge（噛み合い判定） | `generation.validate_candidate_replies`（`routers/generation.py:307`）の後段に新設、または Repair とは別の品質ゲートとして追加 | 現行 validation は形式のみ。意味系は新規。`score_candidate_style` とは別軸（文体でなく会話適合）にする |
| Question Overuse（質問過多検出） | `ensure_has_question`（`generation.py:293`）の条件拡張＋ `validate_candidate_replies` の質問必須検査（`:380-386`）の緩和（相手意図・直近質問密度・`already_asked_questions` を見る） | P0-1 の核心。`condition` のキーワード免除だけでなく Ledger 駆動の免除が必要。`knowledge/training/03_*` の再接続もここ |
| Repetition Detection（繰り返し検出） | `validate_candidate_replies` に新規検査: ①相手直近文との類似 ②直近 self N 件との類似 ③冒頭共感パターン頻度。`_jaccard_similarity`（`generation.py:140`）は流用可 | Ledger に `recent_response_patterns` を持たせると安定する。`ensure_has_question` の定型フォールバックも要是正 |
| Memory Usage（想起の関連度制御） | `_build_context`（`generation.py:1235`）での `my_info/user_knowledge/相手プロフィール` の投入前フィルタ＋ `build_system_prompt` Block 2/3 の描画条件化 | 現状は全量投入。クエリ（相手直近＋intent）との関連度で出し分けする |
| Candidate Ranking（意味順位付け） | `score_candidate_style`（`generation.py:951`）の後段に意味スコアを追加、またはソートキー（`generation.py:1729`）を複合化 | 現行は文体のみ。噛み合い・意図適合・新規性（直近との距離）を加える |
| Self-Revision（自己修正ループ） | `generate()` の repair 機構（`generation.py:1653-1692`）を形式修復から品質改善に拡張、または Judge→再生成の新ループ | 現行 Repair は形式違反時のみ。品質起因の再生成は未定義 |
| Manual Replacement Contrast Learning（差分学習の強化） | `contrast._summarize_difference`（`learning/contrast.py:26`）の軸拡張（失敗分類・トークン差分）＋ `to_contrast_prompt_block` への棄却案原文（短縮形）の追加＋ `limit/max_items` の見直し | `rating_reason`（`database.get_learned_preferences`）のプロンプト再接続も併せて検討。現状未投入 |
| その他（到達不能資産の再接続） | `database.get_learned_preferences()`、`generation.retrieve_relevant_reply_pairs()`、`generation.load_user_approved_reply_examples()`、`training_examples` テーブル、`knowledge/rules・references・training` の `build_system_prompt` への引き渡し | 削除ではなく再接続か廃止判断が必要。P2-5 と同根 |

## 8. 既存テストの状況（自然性評価の有無）

- 実行: `python -m pytest backend/tests -q` → **111 passed**（約14.5秒。警告2件は `main.py:54` の `on_event` 非推奨のみ）。
- テスト構成（全10ファイル）: `test_api.py`（974行。CRUD・生成モック・バリデーション・sanitize・質問必須等の総合）/ `test_learning_first_e2e.py`（source 追跡・Few-shot 検索・Soft scoring 順序）/ `test_learning_v3.py`（Turn 結合・Phase・Batch ライフサイクル・Contrast・Retrieval・Diagnostics）/ `test_validation_and_repair.py`（時刻行除去・厳格パース・repair）/ `test_v3_1_regression.py`（fragmentation・tone Hard Lock・batch_id・health）/ `test_mandatory_question_and_ledger.py`（質問必須・Ledger・履歴参照）/ `test_followup_message.py`（追いメッセ・催促禁止・3役割）/ `test_prompt_scenarios.py` / `test_twenty_scenarios.py`（20+ シナリオのプロンプト含有検査）/ `test_like_bot.py`。
- 何をテストしているか: 形式・構造・禁止語・質問必須・トーン矛盾・重複（案間 0.85）・Turn 結合・Phase・Batch 遷移・Contrast 抽出・Retrieval 順序・Style スコア順序・プロンプトの文字列含有。
- 何をテストしていないか（自然性評価の欠落）:
  - AI っぽさ（定型共感・`そうなんですね` 系）の検出テストなし。
  - 質問過多の検出テストなし（逆に「質問必須」のテストのみ）。
  - 繰り返し（直近送信との類似・冒頭パターン反復）のテストなし。
  - オウム返し（相手文との類似）のテストなし。
  - 長さ適応（相手文量に対する相対長）のテストなし。
  - Memory 想起の妥当性テストなし。
  - 会話の噛み合い（意図・感情適合）の評価テストなし。
  - `score_candidate_style` は順序のみで、意味品質のテストなし。

## Step 2 実装結果（質問任意化・3段構成固定の撤廃）

- 実施日: 2026-09-28 / コミット: `feat: remove mandatory question and rigid reply structure`
- 成功条件の達成: 「質問しない返信」は正式な正常系になった。「反応→自己開示→質問」の毎回強制はなくなった。

### 変更したファイル

- `backend/app/routers/generation.py`
  - `ensure_has_question()`（旧293行付近）: 定型質問の自動付与を廃止し、無条件で原文を返す no-op 化。後方互換のため関数・シグネチャ・呼び出し箇所は残す。
  - `validate_candidate_replies()`: 質問必須バリデーション（旧380〜386行）を削除し、docstring の Hard 項目からも除外。`condition` 引数は互換のため残す。
  - `_build_context()`: 相手直近 Turn（fallback は直近メッセージ）の文字数から長さ区分を算出し、`prompt.build_system_prompt()` へ `counterpart_length_tier / counterpart_length_chars` として渡す。`pieces` にも同キーを追加。
- `backend/app/ai/prompt.py`
  - 新規 `classify_message_length(text)`: short 0〜20文字 / medium 21〜80文字 / long 81文字以上。
  - `build_system_prompt()` に `counterpart_length_tier / counterpart_length_chars` 引数を追加。Block 7（COUNTERPART STYLE ADAPTATION）末尾に `【COUNTERPART MESSAGE LENGTH】` を描画（区分別の傾向指示つき。Hard Limit ではない旨を明記）。
  - HARD INVARIANTS 9条: 「質問必須（絶対ルール）」→「質問は任意」（質問なし短文も正常系。相手の明確な質問には回答すること＋既出質問の繰り返し禁止は維持）。
  - 11条（normal）: 「反応→自己開示→質問」の3段構成固定を撤廃し、短いリアクション/共感/回答だけ/リアクション＋一言/質問あり等からの自由選択に変更。短文には短い返信を優先。
  - OUTPUT CONTRACT（normal）: 見出しを「質問任意」に変更し、4条を質問任意＋3案構成の機械的固定禁止（3案とも短いことも許可）に書き換え。
  - `build_initial_generation_messages()`（normal）: 質問・自己開示・話題拡張は毎回必須ではない旨、短い返信例、相手質問への回答指示を追加。
  - Block 5: 検索実例は文体・語尾・テンポの参考であり、同じ会話構造の再現指示ではない旨を追記（実例に質問があっても今回は質問不要）。
  - followup 系（Block 1 指令・Block 8 役割固定・`_align_followup_replies`・催促禁止）は無変更。
- `backend/app/learning/style.py`
  - `to_learned_policy_prompt()`: 「直接共感＋直接深掘り質問で展開」の一律推奨を、「自然な反応を最優先し、必要な場合だけ質問・深掘り・自己開示。質問しない返信・短い返信も正常」に変更。文体統計の学習自体は維持。
- `backend/app/learning/retrieval.py`: ランキングロジック無変更（仕様通り）。

### 質問必須をどこで撤廃したか

1. `generation.ensure_has_question()` → no-op 化（自動付与の廃止）。
2. `generation.validate_candidate_replies()` → 質問有無の検査ブロック削除。
3. `prompt.py` 9条・OUTPUT CONTRACT 4条・見出し・user 指示 → 質問任意に書き換え。
4. `style.py` ポリシー文 → 深掘り質問の一律推奨を撤廃。

### 3段構成固定をどこで撤廃したか

1. `prompt.py` 11条（normal の `flow_rule`）→ 長さ・構造の自由選択に書き換え。
2. OUTPUT CONTRACT 1条に 3案構成の機械的固定禁止を追記（3案とも短いことを許可）。
3. `test` 側で `"3段構成" / "反応→自己開示→質問" / "質問を省いた案は不正"` がプロンプトに含まれないことを検証。

### テスト変更

- `backend/tests/test_mandatory_question_and_ledger.py`: 質問必須前提の3テストを質問任意仕様に書き換え（ファイル名は履歴継続のため維持）。E2E は質問なし短文が改変されず返ることを検証。
- `backend/tests/test_validation_and_repair.py`: 「違反5: 質問なし」を「質問なしは正常系（違反ではない）」に変更。
- `backend/tests/test_question_optional.py`（新規）: Test A（質問なし validation 通過）/ B（`ensure_has_question` 無改変）/ C（直接質問への回答は非ブロック＋プロンプトに要回答指示）/ D（短文向けに3段構成強制なし＋長さブロックあり）/ E（3案とも質問なしで通過）＋長さ区分の境界値テスト＋生成確認ケース1〜4（モック Provider による E2E）＋ `_build_context` 配線テスト。

### pytest結果

- `python -m pytest backend/tests -q` → **122 passed**（変更前 111 件＋新規 11 件。警告2件は従来通りの `on_event` 非推奨のみ）。
- 仕様変更に伴う失敗は2件のみで、いずれもテスト側期待値の問題として修正（followup 由来の見出し残存「質問必須」1箇所を実装側で「質問任意」に修正、単文返信の改行前提を撤廃）。無関係テストの失敗なし。

### 実際の生成確認結果（モック Provider・LLM 外部呼び出しなし）

- ケース1（相手「眠い」）: 3案とも短文のまま返却。定型質問の付与なし。全案30文字以内。
- ケース2（相手「今日バイト8時間だった」）: 「それはきついな」等の質問なし短文がそのまま成立。
- ケース3（相手「明日何時にする？」）: 「14時くらいで大丈夫」等の回答のみ案が validation を通過。
- ケース4（相手「今日ラーメン食べた」）: 質問なし案が通過し、「どこの？何ラーメン？」型の質問攻め強制がないことを確認。

### 残っている問題（次Step以降）

- Naturalness Judge / Emotion・Intent 分類 / Repetition Detection / Memory relevance は未実装（仕様通り対象外）。
- `score_candidate_style` の質問終了項（15%）は残存しており、質問あり案が微加点されやすい。文体ソートのみで品質フィルタではないため実害は小さいが、将来的な見直し対象。
- 長さ適応は傾向情報のみで、LLM が無視すれば膨張は起こり得る。実 LLM での効果測定は未実施（今回はモック確認のみ）。
- 維持したもの: HARD INVARIANTS（話者分離・架空自己開示禁止・さん付け・オウム返し禁止等）、JSON・3案独立、Tone Hard Lock、Batch/History/Learning/Manual Replacement フロー、followup 構造・催促禁止。

## 9. Frontend・DB・周辺の補足（生成フローに関わる範囲）

- Frontend: `GenerationPanel.tsx: generate()` が `condition/revision_instruction(original=案全文)/tone/mode` を送り 3 案カード化。`ChatArea.tsx` は AI 案送信を `source='generated'+historyId`、手入力を `source='manual'` で送る（＝Contrast の分岐点）。`HistoryModal` で rating 付与。`PracticePanel`（練習モード）は生成フローと別系統。
- DB（`backend/app/database.py: SCHEMA`）: `contacts` / `messages(source, generation_history_id)` / `generation_history(rating/rating_reason/tone/counterpart_message/batch_id)` / `generation_batches(outcome: pending|regenerated|candidate_sent|manual_replaced, parent_batch_id, attempt_no, selected_history_id, replacement_message_id, trigger_message_id)` / `training_examples/sessions/revisions` / `providers/settings` / `knowledge_files/contact_knowledge_files` / `contact_settings` / `user_profile(my_info 含む)` / `user_knowledge`。`init_db` 内に legacy 移行・knowledge 再作成等のマイグレーションあり。
- AI 設定解決: `ai/config.py`（全体 DB > `.env` > 既定 `cerebras/gpt-oss-120b/0.8/1024/50`）＋相手別上書き。API Key は Backend のみ保持。
- knowledge/training 資産: `knowledge/rules`（01〜08）・`references`（2件）・`training`（01〜10、質問なし返信・NG例等）。ただし §6 P2-5 の通り生成プロンプトへの未投入が疑われる（`preview` では参照表示される）。
- `docs/ARCHITECTURE.md` 等の既存 docs は旧構成（rules 投入ありき）の記述で、現行 `_build_context` との差異がある。

---

*本書は調査成果物であり、コード変更は含まない。次ステップへの進行はユーザーの指示待ちとする。*
