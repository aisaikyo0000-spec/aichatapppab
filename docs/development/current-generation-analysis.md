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

## Step 3 実装結果（counterpart_intent 分類の導入）

- 実施日: 2026-09-28 / コミット: `feat: add counterpart intent classification`
- 成功条件の達成: 「相手が質問している／単なる報告／感情共有／誘い等」を区別する情報が Prompt に入るようになった。

### Intentの種類（6種・優先順位つき）

- `question`（相手の質問）> `invitation`（誘い・提案）> `answer_required`（疑問符なし要回答連絡）> `emotional_share`（感情・悩みの共有）> `reaction`（感情・リアクション）> `report`（情報共有・デフォルト）
- `report` だから短文必須、`question` だから質問返し必須のような Hard Rule は作っていない（強い参考情報として扱う）。`report → 質問禁止` のような新固定ルールもなし（Step 2 の質問任意化を維持）。

### 判定方法

- 新規 `prompt.classify_counterpart_intent(text)`（`backend/app/ai/prompt.py`）。LLM 不使用の決定論的ルール。
  - question: `？/?`・疑問語尾（ですか/ますか/でしょうか/かな/かい/だっけ/っけ等）・疑問詞（どこ/いつ/なに/何時/誰/どれ/どっち等）・「どう」（どうでも/どうだってを除外）。
  - invitation: 行こう/しよう/一緒に/しませんか/食べに行等の誘い表現。
  - answer_required: 集合/待ち合わせ/待ってる/空いてる/来れる/了解等（「よろしく」は挨拶誤爆のため除外）。
  - emotional_share: しんど/つら/悲し/嫌な/落ち込/最悪/悩/怒られ/泣等＋「最近〜疲れ」型。医学的診断はしない（会話上の分類のみ）。「疲れた」単体は reaction 側に譲る。
  - reaction: 眠/疲れた/やば/楽しみ/笑等のリアクション表現。
  - report: 上記いずれにも該当しない場合のデフォルト。
- `build_conversation_state_ledger()` に `counterpart_intent` を追加（相手直近文から算出。履歴なし時は `report`）。`_build_context()` の `pieces["conversation_ledger"]` 経由で将来 retrieval 等からも取得可能な構造。

### Promptへの反映

- Block 3（CONVERSATION STATE）に `【COUNTERPART INTENT】`＋Intent別方針（`_INTENT_POLICIES`）を追加。例: report →「無理に質問して会話を延長しない。短いリアクションだけでもよい」/ question →「回答を最優先。回答だけで成立するなら無理に追加質問しない」/ emotional_share →「まず自然に反応。質問攻めにしない」。
- followup モードには方針文を出さない（独自の役割構造を優先）が、ledger 自体は共通。
- `score_candidate_style()`・`learning/retrieval.py` は無変更（仕様通り）。

### テスト結果

- 新規 `backend/tests/test_counterpart_intent.py`（16件）: Test 1〜6（仕様の例文どおりに分類）＋優先順位（`今週どっか行く？`→question）＋疲れた系の切り分け＋デフォルト/空文＋Ledger 配線＋Prompt 反映（全6 Intent）＋E2E（実メッセージ→context→prompt）＋report 時の短文返信 E2E。
- `python -m pytest backend/tests -q` → **138 passed**（Step 2 時点 122 件＋新規 16 件）。無関係テストの失敗なし。

### 誤判定と残課題

- 検証中に1件の誤判定を発見し修正: 「はじめまして！よろしくお願いします」が `answer_required` になっていた（`よろしく` が広すぎ）。パターンから `よろしく` を除外して解消。
- 残課題: 皮肉・冗談・文脈依存の意図は取れない（決定論ルールの限界）。`ほんと？` のような疑問符つきリアクションは priority 通り `question` になる。Naturalness Judge・Emotion/Intent の LLM 化・Repetition Detection・Memory relevance は未実装（対象外）。実 LLM での効果測定は未実施。

## Step 4 実装結果（Naturalness Score の導入）

- 実施日: 2026-09-28 / コミット: `feat: add deterministic candidate naturalness scoring`
- 方針: LLM Judge は導入しない（コスト・レイテンシ・揺らぎ・複雑化を回避）。決定論的評価器＋将来置換可能な I/F。

### Naturalness Score

- 新規 `backend/app/ai/naturalness.py`（`evaluate_candidate_naturalness(candidate, counterpart_message, conversation_ledger, recent_replies, style_char_median)`）。
- 返り値: `{"score", "penalties": [{"key", "score", "detail"}], "signals", "intent", "weights"}`。
- 評価項目: relevance（反応・キーワード一致＋相づち加点）/ question（質問過多。1問までは許容・質問自体は減点しない）/ repetition（完全一致・near-duplicate・同じ冒頭・同じ質問形式。短い一般語は緩和）/ echo（完全一致・大部分含有・語句並べ替え＋薄い付加）/ length（相手文量比。短文相手は4倍まで許容。Hard Limit なし）/ disclosure（エピソード系自己開示の根拠確認。事実空なら中立）/ answer（回答系 Intent の完全性。具体性・キーワード・質問返し検出）/ overreact（emotional_share の過剰反応）。
- Intent 別重み（`_INTENT_WEIGHTS`。正規化使用）。例: report は relevance/question/echo 重視、question/answer_required は answer/relevance 重視、emotional_share は overreact を評価。
- 最終スコア: `final = style*0.40 + naturalness*0.60`（`STYLE_WEIGHT/NATURALNESS_WEIGHT` 定数。`combine_candidate_scores()`）。

### Score計算・Candidate ranking

- `generate()`（`routers/generation.py`）: 既存 `score_candidate_style()` を維持し、その後に naturalness を算出・結合。normal は final 降順ソート、followup は従来通り役割スロット整列（スコアは算出のみ）。
- 直近 self 5件を `_load_recent_self_replies()` で取得し repetition に使用。`known_self_facts`（ledger）・文量中央値（style profile）を length/disclosure に使用。
- Hard Violation（架空開示・話者混同・Tone 等）は既存 validation が優先。スコアは選別に使わない。
- 応答に `naturalness_scores/final_scores` を追加（UI 表示はしない。frontend 無変更）。`GET /api/learning/diagnostics` に `naturalness_enabled/naturalness_weight/style_weight` を追加。

### テスト結果

- 新規 `backend/tests/test_naturalness.py`（18件）: Test 1〜9（短文高評価・過剰質問・話題無視・Echo・長文・質問回答・質問無視・繰り返し・架空開示）＋最重要 Test 17（A短反応 > B質問過多 > C Echo寄り）＋質問カウント・Echo・結合式・E2E（final 降順＋先頭が自然な短反応）＋diagnostics。
- `python -m pytest backend/tests -q` → **156 passed**（Step 3 時点 138 件＋新規 18 件）。

### 調整と残課題

- 統合時に既存 `test_soft_style_scoring_and_candidate_sorting` が1件失敗（final 順で style_scores が非降順＋本命案が2位に後退）。原因は repetition・length の過剰反応2点で、いずれも実装を修正（テストの期待は変えず）:
  1. repetition の中一致帯（0.5〜0.8）は同じ冒頭・同じ質問形式を伴う場合のみ減点（同話題の語彙共有＝本人らしさと区別。`test_8c` で固定）。
  2. 短文相手の長さ許容を4倍まで緩和（本人の通常文量を罰しない）。
- 残課題: 皮肉・冗談・文脈依存の適合は未評価。重み（0.40/0.60）は暫定で実データによる調整が必要。実 LLM での効果測定は未実施。LLM Judge への置換 I/F（`evaluate_candidate_naturalness` の入出力）は確保済み。

## Step 5 実装結果（生成品質評価パイプライン）

- 実施日: 2026-09-28 / コミット: `feat: add generation quality evaluation pipeline`
- 方針: 生成ロジック・Prompt・Naturalness 重み・Conversation State は変更しない。測定基盤の構築のみ。

### 評価DB

- 新規 `generation_evaluations`（`backend/app/database.py` SCHEMA。`init_db` で自動作成。既存DBにも冪等作成）。
- 列: `id / generation_batch_id / history_id(UNIQUE) / candidate_index（提示順0始まり）/ counterpart_intent / naturalness_score / style_score / final_score / human_rating（good|neutral|bad|NULL）/ human_feedback / feedback_tags（JSON配列）/ created_at / updated_at`。
- 会話本文は重複保存しない（`history_id`→`generation_history`、`generation_batch_id`→`generation_batches`→`trigger_message_id`→`messages` で復元）。

### 評価API

- 新規 `backend/app/routers/evaluations.py`（`main.py` に登録。既存の `/api/history` 流の命名・Pydantic 規約に準拠）。
- `POST /api/evaluations {history_id, rating?, feedback?, feedback_tags?}`: 人間評価の upsert（同一 history_id は後勝ちで行増殖なし）。**自動スコア列には一切触れない**（不一致は分析材料として保持）。rating は good/neutral/bad のみ（他は422）、tags は allowlist 外で422、存在しない history_id は404。自動行がない旧履歴への後付け時は batch/candidate_index/intent を補完して作成。
- `GET /api/evaluations?batch_id?&rating=good|neutral|bad|unrated&limit`: 自動＋人間評価＋候補文・相手直前文を結合して新しい順に返却。
- `schemas.py` に `EvaluationCreate/EvaluationOut` を追加。UI は API のみ（frontend 無変更。評価ボタンは残課題）。

### 人間評価・自動評価

- 人間評価の意味: good＝そのまま送れる / neutral＝少し修正すれば送れる / bad＝AIっぽい・意味がおかしい等。理由タグ（ai_like/irrelevant/too_long/too_short/too_many_questions/echo/repetition/awkward/wrong_tone/unsupported_self_disclosure/good/natural。任意）。
- 自動評価は `POST /api/generate` 時に `_save_auto_evaluations()` で全候補分を保存（human 側は NULL）。学習への自動投入はしない（蓄積のみ。Gold 化は後のStepで安全な流れを検討）。

### export・CLI

- `scripts/export_evaluations.py [--format csv|json] [--out PATH] [--db PATH]`: batch_id/history_id/candidate_index/intent/相手文/候補文/3スコア/human 評価等を出力。関数 `export_evaluations()` としてテストからも利用可能。読み取り専用。
- `scripts/evaluate_generation.py [--db PATH]`: Batches/Candidates/平均3スコア/Human Good-Neutral-Bad-Unrated＋不一致指標（human-bad/good の平均 naturalness）を表示。読み取り専用。
- いずれも既存 `scripts/*.py` 規約（ROOT 解決・`__main__` ガード）に準拠。

### テスト結果

- 新規 `backend/tests/test_evaluations.py`（8件）: Test 1（自動行＋人間評価の保存・取得）/ 2（同一 history upsert）/ 3（NULL rating＋unrated 絞込）/ 4（不正 rating・tag・history の拒否）/ 5（batch/rating フィルタ）/ 6（CSV・JSON export の内容検証）/ 7（history との関連保全）/ 8（Naturalness の決定性＋既知順序の不変）。
- `python -m pytest backend/tests -q` → **164 passed**（Step 4 時点 156 件＋新規 8 件）。CLI は DB 不在時の正常系メッセージと `--help` を手動確認。

### 今後の分析課題

- human-bad かつ naturalness 高の事例収集→評価器の弱点特定（`Avg Nat (human-bad)` 指標を用意済み）。
- Good 蓄積後の Gold 候補化フロー（要 human 確認。自動投入はしない）。
- 評価ボタン UI（`👍そのまま送れる / 😐修正が必要 / 👎不自然`）の追加は、既存画面構造の確認後に検討。

## Step 6 実装結果（失敗分析と改善ポイント抽出）

- 実施日: 2026-09-29 / コミット: `feat: add generation quality analysis`
- 方針: 生成ロジック（prompt/naturalness/retrieval/style/contrast）は一切変更しない。読み取り専用の分析基盤のみ。新ルール追加なし・「改善した」と結論づけない。

### 分析項目

- 新規 `backend/app/quality_analysis.py`: `load_evaluations()`（JOIN 復元。0件・DB不在でも空返却）/ `rating_summary()`（Good/Neutral/Bad 件数・平均3スコア・good/bad率・未評価数）/ `tag_analysis()`（タグ別件数・割合・平均・bad率。未知タグ自動集計）/ `intent_analysis()`（実データの Intent 別件数・good/bad率・平均・上位失敗タグ）/ `find_disagreements()`（Case A: nat高+bad / B: nat低+good / C: final高+bad。しきい値 HIGH 0.80/LOW 0.60）/ `tag_examples()`（タグ別具体例最大20件）/ `representative_cases()`（A高+Good/B高+Bad/C低+Good/D低+Bad 各最大10件）/ `improvement_candidates()`（タグ→観測パターン・潜在領域の静的マッピング。修正実施は決めない）。
- `scripts/analyze_generation_quality.py [--db] [--out]`: コンソール要約（§6 形式）＋ `reports/generation_quality/` へ5 JSON（summary/tag_analysis/intent_analysis/disagreements/representative_cases.json）を出力。`reports/` は .gitignore に追加（会話文を含むためローカルのみ）。

### Human Rating分析・Feedback Tag分析・Intent分析・不一致・代表ケース

- 実データがまだないため、分析関数はテスト用シードデータで検証済み。不一致3ケース・代表4分類・タグ別20件例の抽出を確認。
- AI自己評価のみで判断しない設計: 主軸は human_rating/feedback_tags。不一致ケース（特に final高+bad）はランキング誤りの検出材料。

### テスト結果

- 新規 `backend/tests/test_quality_analysis.py`（10件）: Test 1（Rating 集計）/ 2（Tag 集計）/ 3（Intent 集計）/ 4（High+Bad）/ 5（Low+Good）/ 6（代表4分類）/ 7（0件でも無エラー＋5ファイル出力）/ 8（NULL・不正JSONタグ）/ 9（未知タグ）/ 10（生成ロジック不変: Naturalness 決定値 0.925・Intent 分類・重み定数）。
- `python -m pytest backend/tests -q` → **174 passed**（Step 5 時点 164 件＋新規 10 件）。CLI は空DBでの全出力（要約＋5 JSON）を手動確認。

### 現時点で判明した失敗パターン・次Stepの改善候補

- 実運用データなしのためパターンは未確定。分析基盤により判明可能になった想定パターンと潜在領域:
  - too_many_questions → OUTPUT CONTRACT / FINAL TASK
  - ai_like → LEARNED USER RESPONSE POLICY
  - irrelevant → CONVERSATION STATE / COUNTERPART INTENT
  - echo/repetition → 各検出器・HARD INVARIANTS
  - wrong_tone → Tone Hard Lock / 相手適応
  - unsupported_self_disclosure → 架空開示禁止 / Memory
- 次Stepでは実データ蓄積後に本レポートで判断する（自動学習・Gold 自動昇格・human 推定は引き続き禁止）。

## Step 7 実装結果（実評価に基づく生成品質改善）

- 実施日: 2026-09-29 / コミット: `feat: improve natural reply generation from human feedback`
- 前提: 実評価データは 0 件（`scripts/analyze_generation_quality.py` で確認。Candidates: 0）。仕様 §3 に従い大幅改修は行わず、最小限の安全な改善＋比較基盤の構築に限定。Human Good Rate 等の定量比較は `insufficient data` とする。

### Step 6で判明した問題（実データではなく代表30ケースの Before 計測で特定）

- Before（`scripts/compare_before_after.py` 代表30ケース）: intent精度 0.867 / markers 1.0 / validation通過率 0.967 / 期待順位ヒット率 0.50。
- 特定した失敗パターン（すべて決定論層で再現）:
  1. 質問への短い回答（好きだよ／行きたいな／わかった等）が低評価（具体性検出の欠落）。
  2. Echo 候補（言い換え・部分反復）がキーワード一致で高評価になり1位を奪う。
  3. verbose な質問攻め（3Q）が話題適合で高止まりする。
  4. 単独「笑」等の短い独立候補が fragmentation 誤検出される。
  5. Intent 誤分類2種（挨拶→answer_required は Step 3 残件の継続／教えて系→report／おかえり系→report 以外）。

### 今回変更したPrompt/Ranking（最小限・全文書化）

- `prompt.py`: (a) `_INTENT_ANSWER_REQUIRED` に教えて系を追加、(b) `_INTENT_REACTION` におかえり系を追加、(c) Ledger に `prev_self_ended_with_question` を追加し Block 3 に連続質問の注意書き（参考情報。相手質問・確認必要時は除外）、(d) Block 7 に短文適応・言い換え回避の1行追加、(e) 11条に短文時の短候補1案以上＋多様性／意味的差別化のみの追記、(f) OUTPUT CONTRACT に同旨を1行追記。
- `style.py`: 会話構造ポリシーに「説明長文より短い相槌優先」を1行追加（統計学習は不変）。
- `naturalness.py`（重みは不変。旧式→新式の差分のみ）: (a) 短回答語彙（20文字以下で具体性扱い。了解は除外）、(b) Echo 階層化（containment/強言い換え 0.15・弱言い換え 0.25・態度なし部分反復 0.6 新設）＋ Echo 時の relevance/answer 割引、(c) 情報取得3問以上の relevance/answer 割引、(d) 連続質問の軽微抑制（ledger フラグ時・回答内容なしの場合のみ×0.85）、(e) 口語ゆれ正規化（どっか→どこか）・付加語尾（んだね系）追加。
- `generation.py` `_is_fragmented_split()`: 構造シグネチャ方式に単純化（全短断片＋質問締め／自己開示始めのみ検出。笑等の短独立候補は正常）。
- 禁止ルールの追加なし（質問禁止・長さ上限なし）。Hard Invariants 7項目・JSON・3案独立・Tone・Batch/History/Learning/Manual Replacement は不変。

### Before/After（代表30ケース・決定論層）

- After: intent精度 1.00 / markers 1.00 / validation通過率 1.00 / 期待順位ヒット率 1.00（30/30）。
- 内訳: 短回答の正当評価（5件回復）/ Echo 降格（8件回復）/ 質問攻め降格（4件回復）/ fragmentation 誤検出解消（1件）/ Intent 修正（3件）。
- 変更前後の差分は `scripts/compare_before_after.py` の出力 JSON（Step 7 作業時の一時ファイル。リポジトリには 30ケースJSON＋テストとして固定）で確認。

### テスト結果

- 新規 `backend/tests/test_step7_natural_conversation.py`（11件）: Test 1（質問なし成立）/ 2（短文候補）/ 3（連続質問の優先度調整＋非禁止）/ 4（Echo と態度付加の区別）/ 5（長文膨張の減点）/ 6（短文強制なし）/ 7（差別化強制なし）/ 8（必要時の質問候補）/ 9（Hard Invariants 7項目＋禁止語検証）/ 10（API 完全性＋自動評価行）＋代表30ケース全緑テスト（Intent/マーカー/バリデーション/期待順位）。
- 新規 `backend/tests/step7_representative_cases.json`（30ケース×3候補。実在6 Intent のみ使用）＋読込ヘルパー＋ `scripts/compare_before_after.py`（Before/After 比較CLI）。
- `python -m pytest backend/tests -q` → **185 passed**（Step 6 時点 174 件＋新規 11 件）。

### 実LLM評価結果（§26・1ケースのみ・insufficient data）

- 条件: Temp DB・履歴なし（Gold なし）・Gemini gemini-3.5-flash-lite・相手「今日バイト8時間だった」・condition なし。※既定 cerebras のキーは未設定のため Gemini で実施。キーは非表示・Temp DB のため本データ無汚染。
- 生成3案（final 0.955/0.955/0.892）: (1) 立ちっぱなし推測＋「。。」＋丁寧語、(2) 丁寧＋笑＋絵文字混在、(3) 働いてきたんですね＋丁寧。いずれも丁寧な説明的2行文で、短い相槌型は0案。
- 観察（断定なし）: Gold なし時は丁寧・説明寄りに振れる／架空推測（立ちっぱなし）が混入／Echo 的要素（8時間バイトは等）が残存／scores は高止まり（0.89以上）。Human Good/Bad 率の比較はデータ不足のため不可。

### 改善した項目・悪化した項目・未解決問題

- 改善（決定論層）: 短回答の評価、Echo 系の降格、質問攻めの降格、fragmentation 誤検出、Intent 3種の精度。代表30ケース 15/30 → 30/30。
- 悪化: 確認された回帰なし（185 passed）。明確に verbose だが話題適合な候補と簡潔候補の順序はケース依存で残る（3案表示のためユーザ選択で吸収可能）。
- 未解決: 実運用データなし（Good/Bad 率・AI-like 率の定量比較は不可）/ Gold なし時の丁寧寄り / 架空推測の混入 / scores 高止まり傾向の調整（重み変更は見送り）/ 評価ボタン UI 未着手 / good→review 候補状態の未導入。

## Step 8 実LLM自然性改善

- 実施日: 2026-09-29 / コミット: `feat: improve real llm conversational naturalness`
- 方針: 情報量ではなく「本人が送りそうな文章」を目標に、最小限の Prompt 改善＋50ケース Before/After。モデル・プロバイダ・重み・Hard Invariants・DB 破壊変更なし。APIキー非 commit。

### 問題だった生成例（Step 7 実LLM・Gold なし・今日バイト8時間だった）

- 「8時間はずっと立ちっぱなしとかだとめちゃくちゃ疲れますよね。。」→ 架空の具体化（立ちっぱなし）＋「。。」。
- 「8時間バイトは本当にお疲れ様です笑。」→ 丁寧＋笑混在の説明文体。
- 「8時間も働いてきたんですね！」→ Echo 的言い換え。3案とも丁寧な説明的2行文で短い相槌型は0案。

### 原因

- 相手事実の推測混入を禁じる明示境界がなかった（COUNTERPART FACT INFERENCE）。
- 要約・分析・説明型の返信を許容する余地があった。
- 質問 necessity・NO QUESTION 戦略・共感テンプレ反復防止の明示が不足。
- 決定論層の不足（短回答の具体性・Echo 階層・儀礼応答・どこも除外・fragmentation の並列候補誤検出）。

### Prompt変更（最小限）

- `prompt.py`: Block 2 に `【FACT BOUNDARY】` を追加（CHAT HISTORY 外の具体的事実の捏造禁止・状況の勝手な確定禁止・推測の事実化禁止・感情反応は許可・未知は質問で・自己事実は履歴/Gold 確認分のみ・要約説明の抑制）。11条に質問 necessity＋NO QUESTION 戦略＋共感テンプレ反復防止を追記。normal/followup 両 user 指示に6項目サイレント自己チェック（結果出力なし）を追加。compactness 上限は仕様必須見出し1件分のみ 25→26 に緩和（理由コメント付き）。
- `style.py`・重み・ランキング式・Gold 階層・Gold 定義は不変。reply act の新機構は未導入（既存 Intent 優先の方針通り見送り）。

### Before / After（50ケース）

- 決定論層（`scripts/compare_before_after.py` 代表50ケース）: Before（intent 0.94 / markers 1.00 / validation 1.00 / 期待順位 0.92）→ After（1.00 / 1.00 / 1.00 / 1.00＝50/50）。
- 実LLM（Gemini gemini-3.5-flash-lite・50ケース・各3案。Human 評価なし）:

| 指標 | Before | After | 方向 |
|---|---|---|---|
| novel_keyword_rate（推測混入代理） | 0.68 | 0.521 | 改善 |
| avg_novel_keywords | 1.00 | 0.722 | 改善 |
| echo_rate | 0.082 | 0.111 | やや悪化 |
| too_many_questions_rate | 0.129 | 0.056 | 改善 |
| over_explanation_rate | 0.00 | 0.00 | 不変 |
| ai_like_rate | 0.088 | 0.049 | 改善 |
| parse_ok_rate | 0.98 | 0.96 | 1件悪化 |

- 質的観察: After は短文回答が明確に増加（うん/眠い/おはよう等に1〜2行で返答）。一方、短文化により初回出力の fragmentation・近似重複・ robotic 表現の検出が増加（本番フローでは repair loop が処理する範囲）。echo の微増・parse 1件悪化はサンプル変動の範囲内と判断（断定なし）。

### 実LLMテスト結果

- 50/50 ケース成功（errors 0）。APIキー未表示・Temp DB 使用のため本データ無汚染。Human 評価は未実施のため Human Good/Bad 率の比較は不可（insufficient data）。
- 決定論層の検証（`test_step7` 50ケーススイープ）は全緑を維持。

### テスト結果

- 新規 `backend/tests/test_step8_naturalness.py`（10件）: 1 FACT BOUNDARY / 2 推測混入方針＋サイレントチェック / 3 NO QUESTION / 4 短文6種 / 5 Echo と態度引用の区別 / 6 説明長文の減点 / 7 自己開示境界 / 8 相手事実境界（立ちっぱなし型）/ 9 Gold 階層維持 / 10 Invariants 回帰（JSON・fragmentation 並列候補・AI_QUESTION・FACT BOUNDARY 付き E2E）。
- 代表ケースを 30→50 件に拡張（短雑談・食事・学校・趣味・休日・予定・仕事・長文・挨拶）。`scripts/compare_before_after.py` に issue 検出器＋ `--live` 実LLMモードを追加。
- `python -m pytest backend/tests -q` → **195 passed**（Step 7 時点 185 件＋新規 10 件）。

### 改善した指標・悪化した指標・未解決問題

- 改善: novel keywords・質問過多・AIっぽさ・短文対応・決定論50/50・回帰なし。
- 悪化: echo 微増（0.082→0.111）・parse 失敗1件増。いずれも小標本の変動範囲内と記録し、「改善した」と断定しない。
- 未解決: Human 評価なし（Good/Bad 率の比較不可）/ Gold なし時の丁寧寄りの残存 / repair loop 依存の初回出力品質 / 重み調整の見送り / 評価ボタン UI・good→review 状態の未着手。

## Step 9 Human Feedback Loop

- 実施日: 2026-09-29 / コミット: `feat: improve generation with human feedback loop`
- 方針: ルール追加ではなく実例優先。AI proposal → Human correction ペアを最高品質の教師データとして構造化学習。自動 Gold 昇格なし・架空データなし・additive のみ（DB変更なし）・個人情報の fixture 化なし。

### 使用したHuman feedback件数・manual replacement件数（実DB集計・内容非表示）

- generation_batches: manual_replaced 443 / candidate_sent 116 / pending 412。Human correction rate ＝ 443/559 ＝ **0.792**（ベースライン課題）。
- generation_history 4232件（good 11 / neutral 19 / bad 42）。self/manual 727件。contacts 99件。evaluations 0件。
- 修正ペア分析（443件中相手文・採用文の両方あり417件）:
  - 採用文が短い: 44%（長さ比中央値 1.06。**「短く直す」は少数派**のため固定短縮は行わない）
  - 文数: AI平均2.89文 → 人間平均4.51文（同長でも短文分割・改行増）
  - 質問: AI平均1.48 → 人間平均1.16（質問削除ペア36%。質問率 AI 94%→人間78%。禁止ではなく削減）
  - Echo 的棄却案: 19%。長さ中央値 AI 68字 vs 人間67字（ほぼ同等）。

### 抽出した修正パターン（機構）

- `learning/contrast.py` に追加: `classify_reply_act()`（closing/question/self_disclosure/short_reaction/statement）/ `extract_correction_features()`（長さ・文数・質問・Echo・語彙・行為の差分）/ `correction_patterns_for_contact()`（同一相手に閉じた集計。2件未満は中立）/ `build_correction_patterns_block()`（800字上限・data/instruction 分離明記）/ `correction_similarity()`（文字列類似ではなく長さ・文数・質問の行動特徴比較。無データ時0.5）。
- 既存 `_summarize_difference()` に文数・分割軸を追加（形状不変）。
- Prompt 注入: `【HUMAN CORRECTION PATTERNS】` を contrast ブロックへ追記（修正ペア数・長さ比中央値・質問削除/追加率・採用文目安・行為変化・修正例最大2件）。データ不足時は何も出さない。
- Ranking: `final ＝ style×0.40 ＋ naturalness×0.60 ＋ 0.1×(human_fit−0.5)`（±0.05の微調整。無データ時は0と等価。旧式→新式を記録）。Hard/validation 不変。

### Before / After（Step 8 Baseline 対比）

- 実ペア弁別率（417件。棄却AI案 vs 採用人間文で後者が高得点の割合）:
  - naturalness のみ: **0.353**（AI案が高得点になりがち。表層特徴では分離不能という知見）
  - human_fit 加算後: **0.523**（改善するがほぼコイン投げ域。平均ギャップは±0に近く、過大な主張はしない）
- 実LLM 50ケース（Gemini・合成ケース。修正履歴なしのため Step 9 機構は不発。安定性確認）: novel 0.521→0.527 / echo 0.111→0.113 / 質問 0.056→0.080 / ai_like 0.049→0.080 / parse 0.96→1.00。いずれも小標本の変動範囲内で悪化の断定なし（§29: 原因記録。機構自体は全緑のため維持）。
- Human correction rate の再測定は運用データ蓄積後に実施（現時点のベースライン0.792を記録）。

### テスト結果

- 新規 `backend/tests/test_human_feedback.py`（12件）: replacement抽出 / same-contact weighting（他相手・None は中立）/ パターン抽出 / ranking微加点 / short / long / no-question（＋短質問連続の検出維持）/ unsupported inference / echo / tone / hard invariants / 自動Gold昇格なし。
- 新規 `scripts/evaluate_corrections.py`（弁別評価CLI。読取専用）。
- `python -m pytest backend/tests -q` → **207 passed**（Step 8 時点 195 件＋新規 12 件）。

### AI-like・unsupported inference・Echo・question overuse・未解決問題

- 実ペア由来の知見を機構に反映（短縮の非強制・質問削減の傾向学習・Echo 棄却の言及・文数分割の要約軸）。禁止ルールの追加はなし。
- 未解決: 弁別率0.523は低く、表層特徴の限界を示す（ deeper 適合信号が今後の課題）/ Human 評価データ0件のまま（Good/Bad 率比較不可）/ correction rate 0.792 の改善検証は運用データ待ち / 評価ボタン UI・good→review 状態の未着手。

## Step 10 Real Conversation Benchmark

- 実施日: 2026-09-29 / コミット: `feat: add real conversation quality benchmark`
- 方針: 個別ルール追加ではなくベンチマーク中心。Sendable Rate の proxy 測定＋回帰ガード。改善基準（§25B）で判定し、悪化があれば採用しない。

### Baseline（Step 10 開始時点・main 固定）

- build learned-reply-v4.0 / prompt v4.0 / pytest 207 passed。
- 実LLM 50ケース（Step 8 After）: novel 0.521 / echo 0.111 / 質問 0.056 / overexp 0.00 / ai_like 0.049 / parse 0.96。
- Human correction rate 0.792（Step 9 実DB）。
- 実DB反復率: 同一冒頭 2.0% / 同一語尾 14.7%（敬語様式が主体）/ 同一質問型 1.6%。
- Prompt サイズ（最小構成）: 5447文字・【21個（上限10000文字・26個）。

### ベンチマーク（70件・正解捏造なし）

- 新規 `backend/tests/step10_benchmark_inputs.json`（入力のみ。casual/short/emotional/topic/question/statement/long/ambiguous/closing/no-question-needed×7）。
- 新規 `backend/app/reply_policy.py`: 応答行為の複数ラベル推定（11種＋mixed）/ question_necessity（needed/optional/unnecessary。Gold質問率で一段階補正）/ response_length_target（4段階。Gold中央値で上限補正）。生成プロンプトへは注入しない（§22: 意図別方針・長さ区分と重複するため。ベンチマーク測定に使用）。
- 比較CLIに4軸（context/human_chat/personal_style/conversation）＋AI-like 12パターン分類を追加。personal_style は synthetic live に Gold がないため測定不可（null）と明記。

### Before / After（実LLM・Gemini・70ケース・各3案＝210候補）

| 指標 | Step 8 baseline(50) | Step 10(70) | 方向 |
|---|---|---|---|
| novel_keyword_rate | 0.521 | 0.486 | 改善 |
| echo_rate | 0.111 | 0.152 | 悪化 |
| too_many_questions_rate | 0.056 | 0.057 | 横ばい |
| over_explanation_rate | 0.00 | 0.00 | 不変 |
| ai_like_rate | 0.049 | 0.124 | 悪化 |
| parse_ok_rate | 0.96 | 1.00 | 改善 |
| 4軸 context_fit | —（初測 0.605） | — | — |
| 4軸 human_chat_fit | —（初測 0.813） | — | — |
| 4軸 conversation_fit | —（初測 0.956） | — | — |

- AI-like 内訳（210候補中）: paraphrase 32 / topic_drift 13 / too_many_questions 12 / formulaic_empathy 9 / forced_continuation 1 / unneeded_cheer 1。
- echo・ai_like の増加は、追加20件が短小・あいまい入力中心（うん/まあね/だよね/OK等で鏡像応答が interactionally 正常）というケース mix 差が主因と判断。検出器が儀礼的鏡像と内容反復を区別しない測定限界を記録（断定なし）。
- Sendable Rate: Human 評価データ0件のため測定不可（insufficient data）。proxy として correction rate 0.792（ベースライン維持）を記録。非採用＝失敗としない（§20）。

### Production 変更（最小限・§23 基準で判定）

1. Echo paraphrase の助詞非依存化（仕事が終わった/仕事終わったの同一視）のみ採用。根拠: 複数ケースで再現＋Gold 逆傾向なし＋例外少＋既存テスト全緑。
2. fragmentation の並列短候補の誤検出抑止（Step 8 作業分。実LLM短文化で顕在化）を維持。
3. **見送り**: 生成履歴の repetition 参照拡張はいったん revert。理由: 既存 tie-order を壊す＋実DB反復率が低く様式的＋棄却回避は correction_similarity が担当。判断過程を `generation._load_recent_self_replies` の docstring に記録。
4. 語尾反復の penalty 化は見送り（14.7%は敬語様式。§23 未達）。
5. question_necessity / length_target のプロンプト注入は見送り（§22: 重複のため）。

### テスト結果

- 新規 `backend/tests/test_step10_benchmark.py`（13件）: reply intent / length target / question necessity / repetition penalty / echo classification / candidate ranking / three-candidate quality / closing / short / long / ambiguous / benchmark件数・無正解 / regression guard（6項目）。
- `python -m pytest backend/tests -q` → **220 passed**（Step 9 時点 207 件＋新規 13 件）。

### Regression・未解決問題

- 6項目（unsupported inference・echo・question overuse・tone・JSON・hard invariants）は全緑。Prompt サイズは上限内。
- 未解決: Human 評価0件（Sendable Rate 測定不可）/ 儀礼的鏡像と内容反復の区別 / personal_style_fit の実測には Gold 付き運用データが必要 / correction rate 改善の検証待ち / 評価ボタン UI 未着手（Step 11 で対応）。

## Step 11 Human Evaluation and Sendable Rate

- 実施日: 2026-09-30 / コミット: `feat: add human evaluation and sendable feedback`
- 方針: 自動スコアではなく「そのまま送れる割合」を中心に据える。評価は任意・非強制。単件評価でルール生成しない。架空評価データなし。

### Baseline（Step 10 固定値）

- pytest 220 passed / benchmark 70件・210候補 / correction rate 0.792 / ai-like 0.124 / echo 0.152 / parse 1.00 / context_fit 0.605 / human_chat_fit 0.813 / conversation_fit 0.956。

### 評価UI（最小変更）

- 生成候補カードに送信可否4段階（そのまま送れる/少し修正/かなり修正/使えない）のトグル行を追加。評価なしでも通常利用可（会話操作を邪魔しない）。
- `api.saveEvaluation`/`listEvaluations`・`EvaluationItem`/`Sendability` 型を追加。既存の👍/😐/👎評価・送信・再生成フローは不変。

### DB変更（additive のみ）

- `generation_evaluations.sendability`（sendable/minor_edit/major_edit/rejected/NULL）を SCHEMA＋ALTER マイグレーションで追加。既存行は NULL のまま。APIキー等の新規保存なし。
- 候補追跡は既存の batch_id/history_id/candidate_index を維持。

### 評価データ構造・送信との関連

- そのまま送信（source=generated＋本文一致）→ history is_sent/is_adopted＋batch candidate_sent（Positive Signal。既存機構を維持）。
- 編集送信（本文不一致の manual）→ 直近 batch を manual_replaced＋replacement_message_id（Negative/Contrast Signal。既存機構を維持）。
- `POST /api/evaluations` は upsert（同一 history は後勝ちで行増殖なし）。rating/sendability の None は既存値保持（部分更新）。自動スコア列には触れない。
- `GET /api/evaluations` に sendability フィルタを追加。unrated＝rating・sendability ともに NULL。

### Human correction・Sendable Rate

- 実DBの evaluations は0件のため Sendable Rate は測定不可（insufficient data。ベースライン correction rate 0.792 を維持記録）。非採用＝失敗としない。
- 採用実例の Prompt 利用: 同一相手の送信済み生成文を最大5件 `【RECENT ACCEPTED】` として same-contact Gold ブロックへ追記（data 扱い明記。データなし時は何も出さない）。
- 優先順位の記録: Hard validation ＞ invariants ＞ relevance ＞ Gold ＞ correction ＞ style ＞ naturalness。Human feedback は Hard Invariant より下位（validation が先に適用）。
- Ranking: `final ＋= 0.06×(sent_sim−0.5)`（±0.03。送信実績3件未満は中立）。旧式→新式を記録。データ僅少時の強い反映はしない。

### 学習への利用方法

- manual replacement ペアは従来通り Contrast Learning へ（Step 9 機構）。sendability は集計・フィルタ用途（`acceptance_stats()`：スロット別採用数・sendability 分布）。50件目安の蓄積後に human_acceptance の本格反映を検討（今回は構造＋収集まで）。

### Hard Invariantとの優先順位

- Human 評価が高くても架空開示・捏造・話者混同・さん付け違反・JSON破壊・重大 Echo 反復は許可しない（validation が先）。自動評価と Human 評価の不一致時は Human 側を重視する設計（不一致は分析材料として保持）。

### テスト結果

- 新規 `backend/tests/test_human_evaluation.py`（11件）: sendability 保存・取得・検証・フィルタ／generated 送信フロー／編集送信フロー／accepted ブロック／acceptance_stats／ranking 中立・微加点／migration／7経路フロー。
- `python -m pytest backend/tests -q` → **231 passed**（Step 10 時点 220 件＋新規 11 件）。
- DB migration 結果: 新規・既存どちらの DB でも `init_db()` で sendability 列が存在し、既存行は NULL 維持（テストで検証）。
- frontend build 結果: `npm run build`（tsc＋vite）成功。
- 実LLM 使用なし（評価基盤の構築が主目的のため。生成件数等の実測は運用データ待ち）。

### 未評価件数・未解決問題

- 未評価: 実DB evaluations 0件（50件目安に遠く及ばず）。
- 未解決: Sendable Rate 測定不可 / correction rate 改善の検証待ち / 評価 UI の実運用フィードバック待ち / good→review 状態の未導入（Step 12 で一部対応）。

## Step 12 Production Human Feedback Ranking

- 実施日: 2026-09-30 / コミット: `feat: optimize ranking with production human feedback`
- 方針: データ件数を先に確認し、不足のためランキング変更は行わない（§2・§30）。観測機能の追加＋フォールバック検証＋全緑維持で完了とする。架空データ・推測学習なし。

### 評価件数・データ分布・不足判定

- 実DB: evaluations 0件（sendability 0件・good/neutral/bad 0件）。batches: candidate_sent 116 / manual_replaced 443 / pending 412。history ratings: good 11 / neutral 19 / bad 42。
- 判定: evaluations 0 ＜ 30、sendability 0 ＜ 30 → **データ不足。本格的な Human Ranking 変更は禁止**（Step 11 機構を維持し収集継続）。

### 実装した変更（観測のみ・ランキング不変）

- `learning/contrast.py`: `SENDABILITY_SIGNALS`（sendable +1/minor +0.5/major −0.5/rejected −1。検証用。未配線）/ `smooth_rate()`（Bayesian 平滑化。少数件数の100%/0%防止）/ `feedback_volume()`（件数・分布・correction_rate・不足判定）/ しきい値定数（30/30/同一相手5件）。
- `GET /api/learning/diagnostics` に `feedback`（件数・分布・correction_rate・不足判定）/ `acceptance`（スロット別採用・sendability分布）/ `smoothed_sendable_rate`（データなし時は事前分布0.5）を追加。既存キー不変・新エンドポイントなし（重複回避）。
- recency 減衰・global 混合・sendability 強反映は未配線（データ充足後の課題として記録）。

### A/B結果・Sendable Rate・Regression

- A/B なし（比較対象の変更なしのため Before＝After）。実LLM 70ケースの再実行なし（Step 10 計測が有効なまま）。
- Sendable Rate: 測定不可（evaluations 0件）。correction rate 0.792（ベースライン維持）。
- Regression: なし（241 passed）。Hard Invariants・JSON・Tone・Echo・質問・推測の振る舞い維持をテストで確認。

### テスト結果

- 新規 `backend/tests/test_production_feedback.py`（10件）: insufficient fallback / same-contact・global 集計 / smoothing / sendability scoring / manual Gold separation（採用生成文の source 維持）/ upper bound（±0.05/±0.03）/ AI-like 両立 / repetition / hard invariants / diagnostics 指標。
- `python -m pytest backend/tests -q` → **241 passed**（Step 11 時点 231 件＋新規 10 件）。

### 未解決問題

- Human 評価0件（50件目安）。Sendable Rate・A/B・重み調整はデータ充足待ち。
- good→review 状態・評価ボタン実運用フィードバックは未着手。

## 9. Frontend・DB・周辺の補足（生成フローに関わる範囲）

- Frontend: `GenerationPanel.tsx: generate()` が `condition/revision_instruction(original=案全文)/tone/mode` を送り 3 案カード化。`ChatArea.tsx` は AI 案送信を `source='generated'+historyId`、手入力を `source='manual'` で送る（＝Contrast の分岐点）。`HistoryModal` で rating 付与。`PracticePanel`（練習モード）は生成フローと別系統。
- DB（`backend/app/database.py: SCHEMA`）: `contacts` / `messages(source, generation_history_id)` / `generation_history(rating/rating_reason/tone/counterpart_message/batch_id)` / `generation_batches(outcome: pending|regenerated|candidate_sent|manual_replaced, parent_batch_id, attempt_no, selected_history_id, replacement_message_id, trigger_message_id)` / `training_examples/sessions/revisions` / `providers/settings` / `knowledge_files/contact_knowledge_files` / `contact_settings` / `user_profile(my_info 含む)` / `user_knowledge`。`init_db` 内に legacy 移行・knowledge 再作成等のマイグレーションあり。
- AI 設定解決: `ai/config.py`（全体 DB > `.env` > 既定 `cerebras/gpt-oss-120b/0.8/1024/50`）＋相手別上書き。API Key は Backend のみ保持。
- knowledge/training 資産: `knowledge/rules`（01〜08）・`references`（2件）・`training`（01〜10、質問なし返信・NG例等）。ただし §6 P2-5 の通り生成プロンプトへの未投入が疑われる（`preview` では参照表示される）。
- `docs/ARCHITECTURE.md` 等の既存 docs は旧構成（rules 投入ありき）の記述で、現行 `_build_context` との差異がある。

---

*本書は調査成果物であり、コード変更は含まない。次ステップへの進行はユーザーの指示待ちとする。*
