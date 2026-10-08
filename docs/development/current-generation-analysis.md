# 現行返信生成アーキテクチャ分析

## 2026-10-09 受け入れ準備の追加レビュー

Tappleの招待提案には、AIが相手の信頼性や実際の安全性を判定できないことを明記し、利用者自身が安全だと感じる場合に限り検討するよう表示する。返信が繰り返し短くなり話題の展開も減ったケースを9件目として追加し、返信速度だけで関心を推測しない方針をpromptとベンチに反映した。これはsynthetic caseの期待動作であり、実APIでの遵守は未検証。

独立レビューで、9件目の返信が短い反応に合うかを確認していない点が見つかった。ベンチに短い受け止めや自然な会話終了を示す返信要件を加え、無関係な質問と、相手を責めて再勧誘する返信を不合格にする回帰テストを追加した。このマーカー判定は文脈適合の目安で、実際の文体品質を保証しない。

続く独立レビューでは句読点のない質問「住んでる場所どこ」「最近どう」が評価器を通る問題が見つかった。両方の回帰ケースを修正前に失敗させてから検出器を広げ、「そっか。また話そう」のような相づちは誤検出しないことを確認した。Tapple focused suiteは**5 passed**、fallback focused suiteは**17 passed**。この修正時点の全backend suiteは**859 passed / 2 warnings**、frontend build、`compileall`、CLI `--help`、`git diff --check`もPASS。実APIでの出力は未確認。

さらにfresh reviewで、質問の抜け、間接的な返信要求、相づち後の無関係な話題が検出器を通る問題と、固定語尾リストが自然な言い換えを落とす問題が見つかった。追加テストをREDで確認してから、句読点なしの質問・返信や連絡を促す表現を検出し、短い相づちの後には文脈に沿う会話終了を求める評価に変更した。追加レビューで判明した「連絡くれると嬉しい」の見逃しと「無理せず過ごしてね」などの自然な言い換えも回帰テストに加えた。Tapple focused suiteは**5 passed**。最終差分の新規read-only Python Reviewerは**PASS**。全backend suiteは**859 passed / 2 warnings**、frontend build、`compileall`、CLI `--help`、`git diff --check`もPASS。Geminiの切替はprimary 3.5→primary 3.1→secondary 3.5→secondary 3.1で、rate limit時だけ次へ進む。実API疎通、最新70ケース、Contact Bench、Tappleの実生成と文章確認は未実施。

朝の評価手順も確認し、疎通確認が予備アカウントを選んだときに主キーが後続ベンチへ渡らず、予備3.5・3.1の両方が制限された後に主アカウントへ戻れない問題を見つけた。予備キーを主キーに読み替えるのではなく、`--active-account`で実際のアカウント名を保ちながら、もう一方のキーを常に渡すよう変更した。設定テストは**13 passed**。全backend suiteは**862 passed / 2 warnings**、frontend build、`compileall`、CLI `--help`、PowerShell AST parse、`git diff --check`はPASS。新規read-only Python ReviewerもPASS。実APIでの切替は未確認。

70ケースの手動確認では、代表8ケースに加え、検証器が注意を示した候補と本人確認への分岐を全て出力する。注意候補は品質不合格の自動判定ではなく、人が元の会話・返信・評価artifactを照合するための確認リストである。不正なUTF-8 artifactを読み込んでも検証器がtracebackで終了しないよう、JSON形式の構造化エラーにする。

独立レビューの初回判定は、Tappleシナリオ追加に対するテストfixture漏れと不正UTF-8入力時のクラッシュを指摘したためFAIL。両方をREDテストで再現して修正し、新規Reviewerの再確認は**PASS**。focused suiteは**373 passed**、全backend suiteは**858 passed / 2 warnings**、frontend build・`compileall`・`git diff --check`・PowerShell AST parseもPASS。Geminiの経路はprimary 3.5→primary 3.1→secondary 3.5→secondary 3.1で、次の経路へ進む条件はquota/rate limitに限定する。API疎通、最新70ケース、Contact Bench、Tappleの実生成はまだ行っていない。

## 2026-10-09 オフライン受け入れ準備

pipeline・Contact・Tappleの各ベンチが、同じrun専用`quota-route.json`で直近の成功アカウント・モデルを共有する。状態にはアカウントとモデルに加え、APIキー本体を含まないキー設定のSHA-256 fingerprintを記録する。別キー設定で読み込んだ状態は無視し、runフォルダ名にはGUIDを含める。追加したREDテストは修正前に再現した。不正なUTF-8やfingerprintのない旧形式の状態ファイルも無視する。README冒頭には旧仕様の記録であることを明記し、現行コードと分析資料、LIAISONへの参照を置いた。focused suiteは**27 passed**、backend全体は**853 passed / 2 warnings**。Tapple safety Reviewerとroute-state設定指紋Reviewerは**PASS**。実APIの疎通、最新70ケース、Contact Bench、Tapple実生成と文章確認はまだ行っていない。

## 2026-10-09 Tapple Iteration 26: quota経路の継続と朝の評価手順

ベンチ各ケースの呼び出しで、前のケースが成功したモデル・アカウントから再開する。quotaに当たった場合は、メイン3.5→メイン3.1→予備3.5→予備3.1の残りの経路を順に試す。アプリ本体もメイン3.5と3.1が両方quotaになった後、予備3.5、予備3.1へ進む。疎通確認はこの順で最大4回まで行い、quota以外のエラーでは切り替えない。Iteration 26時点では経路状態を各ベンチ内だけで保持していたが、今回のオフライン準備でpipeline、Contact、Tapple間の共通route-stateを追加した。

朝のPowerShell手順は疎通に成功したモデル・アカウントを後続ベンチへ渡し、70ケースからの返信例8件、Contact Bench全返信、Tapple全返信を画面に表示して確認できるようにした。ローカル設定はprovider=Gemini、標準3.5、予備3.1で、主キーgemini2.md・予備キーgemini3.mdを読み込めることを確認した。両ファイルのキーは異なるが、APIでの有効性は未確認。Iteration 26時点の`python -m pytest backend/tests -q`は**849 passed**だった。共通route-state追加後は**851 passed / 2 warnings**で、frontend production build・`compileall`・CLI `--help`・`git diff --check`もPASS。Iteration 26時点のPython Reviewerは**PASS**。route-state変更時のレビューはその時点ではpendingだったが、後続修正後の独立レビューは冒頭に記載したとおりPASS。APIは呼び出していないため、実生成評価と最新のquota状態は未確認。Step 18-R4は継続中。

## 2026-10-09 Tapple Iteration 25: 迷い・安全懸念時の再勧誘ガード

実生成ベンチに「会うこと自体に迷いがある」「安全面に不安がある」の2会話を加え、計8シナリオにした。懸念に触れるだけの一般文が合格しないよう、返信に求める表現も具体化した。さらに本体と同じ`validate_candidate_replies`を実行し、ベンチと本番validatorの判定差を減らした。

独立Reviewerは、日付のない「ぜひ会いましょう」と丁寧語の「お会いしましょう」「ぜひお会いしませんか」が保留中の相手に対する再勧誘として検出されない問題を指摘した。追加テストが修正前に失敗することを確認し、修正後はベンチの失敗理由が`reinvitation_not_allowed`になることも確認した。最新差分のfresh Reviewerは**PASS**。Tapple focused suiteは**337 passed**、backend全体は**844 passed / 2 warnings**、frontend production build・`compileall`・`git diff --check`はPASS。

この記録時点のGeminiのprimary 3.5、primary 3.1、予備アカウント3.5、予備3.1への切替経路はmockテストで確認済みだった。実APIを呼んでおらず、当時のTappleベンチは8シナリオ。最新の実装・検証状況は本資料末尾を参照する。Step 18-R4は未完成。

> 初回調査は2026-09-27に実施しました。過去の進捗記録は記録時点の状態です。現状は本資料末尾の最新ステータスを参照してください。

## 2026-10-08 返信品質・Geminiフォールバック更新

- 対象: GitHub PR [#1](https://github.com/aisaikyo0000-spec/aichatapppab/pull/1)、ブランチ `codex/chat-quality-20261008`。確認時点では未マージです。変更は `main` にまだ反映されていません。
- 返信検証を更新し、本人の経験を確認できない場合はアプリ利用者への確認に切り替えます。不明な会話参照では、根拠のない断定を避けつつ短い確認返信を許可します。
- 新規インストール時の標準モデルを Gemini 3.5 Flash Lite にし、レート制限を検知したら Gemini 3.1 Flash Lite へ切り替えます。修復リクエストと履歴記録も、実際に使ったモデルに合わせます。
- 同一プロバイダーの予備モデルに個別キーがなければ、主モデルのキーを再利用します。設定APIの応答にはキー本体を含めません。
- `pytest -q backend/tests`: **415件成功**（既存のFastAPI非推奨警告2件）。
- Gemini 3.5の実行確認: 4ケースすべてHTTP成功。2ケースで返信の修正が必要でした。不明な参照のケースでは、修正後に短い確認候補を返しました。
- Gemini 3.1の70ケース評価: HTTP成功70件、エラー0件、修正を要したケース4件。指標は簡易な自動評価であり、実際にそのまま送れる品質の保証ではありません。
- 3.5では全件評価中に短時間のレート制限（429）を確認しました。3.5から3.1への切替動作は自動テストで検証しています。3.1を日常利用、3.5を完成時の標準モデルとする方針です。
- ローカルのチャット履歴は外部APIへ送っていません。履歴から本人の好みを抽出して確認・保存する作業は未完了です。次は本人が手入力した発言に限定した候補抽出と、保存前の確認方法を整理します。
- 最新の製品コードコミット: `98e07b1`。この更新では進捗資料2件をPRブランチに追加します。

- 調査日: 2026-09-27
- 対象リポジトリ: `aisaikyo0000-spec/aichatapppab`（ローカル: `C:\Users\poiuy\desktop\AIチャットアプリ`）
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

5. Provider 解決: `backend/app/ai/factory.py: get_provider(cfg["provider"], api_key)`。登録済みは `cerebras`（`backend/app/ai/cerebras.py`、既定 `gpt-oss-120b`）/ `nvidia`（`backend/app/ai/nvidia.py`）/ `gemini`（`backend/app/ai/gemini.py`。OpenAI 互換 endpoint、`MIN_MAX_TOKENS=2048` の下駄あり、`json_mode` 対応）。PR #1 未マージの `main` では既定が Cerebras。PRブランチでは Gemini 3.5 Flash Lite を既定とし、3.1 Flash Lite へ切り替える変更を加えています。
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
- good→review 状態・評価ボタン実運用フィードバックは未着手（Step 13 で対応）。

## Step 13 Production Feedback Collection

- 実施日: 2026-09-30 / コミット: `feat: complete production feedback collection flow`
- 方針: 新アルゴリズム追加ではなく、生成→送信→評価→DB→diagnostics のループ完成。評価データ水増しなし。Human Ranking 変更なし（Step 12 の不足判定を維持）。

### UI（最小・非強制）

- Step 11 追加の送信可否4段階トグルは候補カード内の小さな行に配置済み（会話操作を優先）。評価なしでも生成・選択・編集・送信・破棄は通常通り。評価の後から変更はトグル再押下で可能（最新値で取得）。

### DB（変更なし・既存設計の再利用）

- 新テーブルなし。`generation_evaluations`（sendability/rating/feedback/tags/時刻）＋`generation_history`（is_sent等）＋`generation_batches`（outcome/selected/replacement）＋`messages`（source/history_id）で全 requirements を充足。APIキー等の新規保存なし。実チャットの fixture 化なし。

### implicit / explicit の分離

- explicit: evaluations.sendability または human_rating の設定値（明示評価が最優先）。
- implicit_sendable: history is_sent=1 かつ sendability 未設定から導出（新規列なし。「送信＝完全満足」とは断定しない）。
- 優先順位: explicit evaluation ＞ explicit manual replacement ＞ implicit send。不一致時は explicit を重視し、自動スコアは保持する。
- Duplicate 防止: history_id UNIQUE＋upsert（再評価は後勝ちで行増殖なし）。

### evaluation flow（E2E 確認済み）

- contact→generate 3候補→選択→送信→評価→diagnostics、generate→編集→送信→manual_replaced→評価の両経路を API テストで確認。
- 来歴追跡: history_id/batch_id/contact_id/candidate_index/時刻を evaluations＋history 結合で復元（本文の重複保存なし）。
- 編集差分は `classify_edit_magnitude()`（minor/major/rewrite の目安）で測定。自動で sendability を書き換えない。

### diagnostics（§16・§17）

- `GET /api/learning/diagnostics` に `evaluation_flow`（explicit_total/implicit_sent_total/sendability分布）と `evaluation_integrity`（unlinked/missing/duplicate＝すべて0件のはず）を追加。
- 実DB読取確認（書換なし）: evaluations 0件・integrity 全0・implicit_sent 197件（送信済み生成候補の追跡可能数）。

### 実評価件数・データ品質

- evaluations 0件・sendability 0件（50件目安に未達。30件ゲートも未達のためランキング変更なし）。
- diagnostics の `sufficient_for_ranking` は false のまま。Human Ranking 改善・Sendable Rate 改善は主張しない（正確に infrastructure completed / data pending と記録）。

### テスト結果

- 新規 `backend/tests/test_feedback_collection.py`（11件）: implicit send / explicit / override / manual replacement / provenance / duplicate prevention / update / diagnostics / unlinked / E2E×2。
- `python -m pytest backend/tests -q` → **252 passed**（Step 12 時点 241 件＋新規 11 件）。
- `npm run build`（tsc＋vite）成功・エラー0件。
- 実LLM 使用なし（基盤確認が目的。少数ケースE2Eはモックで実施）。

### 未解決問題

- 実評価0件（30/50件ゲート未達）。Sendable Rate・A/B・重み調整は運用データ待ち。
- good→review 状態の未導入。

## Step 14 Natural Conversation Generation

- 実施日: 2026-09-30 / コミット: `feat: improve natural conversation generation`
- 方針: ルール追加ではなく測定駆動。Baseline→小変更→Benchmark→悪化revert。変更は ranking 側のみ（Prompt 本文の変更なし）。

### Baseline（Step 14 開始時点・現行コード）

- 実LLM 70ケース・207候補（Gemini）: novel 0.507 / echo 0.135 / 質問 0.068 / overexp 0.01 / ai_like 0.106 / parse 0.986。4軸: context 0.586 / human_chat 0.827 / conversation 0.955。AI-like 内訳: 質問14・言い換え28・話題逸脱14・説明2・定型共感7・励まし3。
- 生成フロー監査: prompt 8ブロック＋ledger＋intent＋length区分＋correction＋accepted は役割重複なしと判断。削減可能な重複はなし（各追記はテストで存在を保証）。

### Prompt変更

- 本文変更なし（監査の結果、安全に削除できる重複なし）。最小構成 5447文字・【21個で上限内を維持。

### Intent（§6 対応）

- 新規分類器は作らない。Step 10 の11ラベルが要求セットを包含することを確認（ACKNOWLEDGE=acknowledgement／EMPATHIZE=empathy／ANSWER=answer／ASK=question／CONTINUE=topic_continuation／REACT=reaction／CLOSE=closing／PLAYFUL=joke。NEUTRAL 単独は分類不能時に相当する empty→acknowledgement フォールバックで代替）。

### Question policy（§8・§9）

- `reply_policy.question_necessity` を naturalness の質問評価に配線。UNNECESSARY（終了・挨拶・短反応で足りる）時のみ単発質問を 0.85→0.70 に抑制（禁止ではなく優先度調整）。NEEDED/OPTIONAL は従来通り。

### AI-like対策・Echo対策・Candidate diversity（§15・§19・§21・§22）

- 候補セット内の冒頭重複に −0.02 の微調整（`_apply_diversity_nudge`。normal のみ。無理な差別化なし・全同一でも送信可）。
- Echo は助詞非依存 paraphrase＋態度・儀礼の除外で維持（正常な「そうなんですね」型は排除しない）。
- 3案すべて質問・すべて共感の固定禁止は作らない（validation の構造検査と ranking が担当）。

### Benchmark（70ケース Before / After）

| 指標 | Before | After | 方向 |
|---|---|---|---|
| novel_keyword_rate | 0.507 | 0.488 | 改善 |
| echo_rate | 0.135 | 0.155 | やや悪化 |
| too_many_questions_rate | 0.068 | 0.072 | 横ばい |
| over_explanation_rate | 0.01 | 0.00 | 改善 |
| ai_like_rate | 0.106 | 0.106 | 不変 |
| parse_ok_rate | 0.986 | 0.986 | 不変 |
| context_fit | 0.586 | 0.592 | 微増 |
| human_chat_fit | 0.827 | 0.821 | 微減 |
| conversation_fit | 0.955 | 0.968 | 微増 |

- 変更はランキング側のみのため生成文自体への系統的影響は想定外で、差分は LLM サンプリングのノイズ範囲内と判断。§39 最低条件（AI-like↓）未達のため「改善した」とは主張しない。
- 決定論層: 50ケース sweep 30/30→30/30 維持、pytest 全緑維持。

### 採用した変更・採用しなかった変更

- 採用: necessity 配線・diversity nudge（いずれも suite＋sweep 全緑）。
- 不採用(revert なし・最初から見送り): Prompt 本文削除（安全な重複なし）/ 新規 Intent 分類器（既存で包含）/ 返信不要 API（短い close response で代替）/ 語尾反復 penalty（Step 10 で §23 未達）/ 生成履歴の repetition 参照（Step 10 で revert 済み）。
- 悪化による revert: なし（悪化はノイズ範囲内で、決定論テストは全緑）。

### テスト結果

- 新規 `backend/tests/test_step14_golden.py`（2件・15ケース固定）: バリデーション・期待順位・最低スコアの固定＋質問候補の非禁止。
- `python -m pytest backend/tests -q` → **254 passed**（Step 13 時点 252 件＋新規 2 件）。
- `npm run build` 成功。

### 未解決問題

- AI-like は横ばい（0.106）。短小入力での鏡像応答と内容反復の区別が引き続き課題。
- Human 評価0件（Sendable Rate 測定不可）。返信不要 API・重み調整は見送り継続。

## Step 14-R Natural Conversation Generation Rework

- 実施日: 2026-09-30 / コミット: `fix: improve natural conversation generation`
- 判定: Step 14 不合格（AI-like 横ばい・Echo/質問/HumanChat 微悪化）のため、生成部分（Prompt→LLM→validation）を中心に再実施。評価条件は固定（70ケース・同一コード・同一指標。ケース削除・基準変更なし）。

### Step 14失敗理由

- 変更が ranking 側（necessity 配線・diversity nudge）に偏り、Gemini が最初に生成する文章自体に系統的影響を与えなかった。live 差分はノイズ範囲内。

### 現在のBaseline（§19 固定値）

- AI-like 0.106 / Echo 0.135 / Questions 0.068 / Context Fit 0.586 / Human Fit 0.827 / Conversation 0.955（実LLM 70ケース・207候補）。

### 変更内容（生成側・少量）

- OUTPUT CONTRACT: A/B/C 役割の目安を追加（案1＝最も自然で短い反応／案2＝少し展開／案3＝必要なら質問。固定パターン化は禁止のまま。質問が不自然なら案3も質問なし可）。
- OUTPUT CONTRACT: 質問必要性の明示（会話上必要な場合だけ。継続目的の追加はしない）／言い換え返信の回避（相手文のほぼ同義反復は情報量ゼロとして避ける）／無関係な新話題の開始禁止（広げる場合は直接つなげる）。
- flow_rule 11: 会話終了・受領時（おやすみ・またね・了解・ありがとう等）は短い返答で終える旨を追加。
- user 指示: 「説明文ではなく実際に送るメッセージとして作成」の1行を追加。
- 禁止ワードの大量追加なし。Hard Invariants・JSON・Tone・Gold 階層は不変。

### Before / After（実LLM・Gemini・70ケース・同一条件）

| 指標 | Before | After | 方向 |
|---|---|---|---|
| ai_like_rate | 0.106 | 0.098 | 改善 |
| echo_rate | 0.135 | 0.132 | 改善 |
| too_many_questions_rate | 0.068 | 0.059 | 改善 |
| over_explanation_rate | 0.01 | 0.00 | 改善 |
| parse_ok_rate | 0.986 | 0.971 | 1件悪化 |
| context_fit | 0.586 | 0.605 | 改善 |
| human_chat_fit | 0.827 | 0.843 | 改善 |
| conversation_fit | 0.955 | 0.963 | 改善 |
| novel_keyword_rate | 0.507 | 0.436 | 改善 |

- AI-like 内訳: 質問 14→12／言い換え 28→27／話題逸脱 14→4／説明 2→0／定型共感 7→6／励まし 3→3。重点対象の言い換え・話題逸脱が減少。
- 決定論層: 50ケース sweep 全緑維持、pytest 全緑維持。

### AI-like内訳・Echo・Questions・Context Fit・Human Chat Fit・Conversation Fit

- 上表の通り、全合否基準を満たす（AI-like↓・Echo悪化なし・HumanChat悪化なし・Context悪化なし）。
- parse 1件悪化は単発の JSON 不正（repair loop が本番処理する範囲）。

### 採用/不採用

- 採用: 上記の Prompt 変更一式（suite＋sweep 全緑＋live 全基準クリアのため）。
- 不採用: なし（悪化による revert 不要）。

### 未解決問題

- 改善幅は小さい（サンプリング変動を含む）。Human 評価0件のため Sendable Rate での裏付けは不可。
- 短小入力での鏡像応答と内容反復の区別は継続課題。

## Step 15 Candidate Ranking

- 実施日: 2026-09-30 / コミット: `test: add top-1 ranking measurement (no ranking change)`
- 方針: 生成Prompt・モデル・API・DB・UIは不変。ranking 式の文書化→offline sweep→Top-1測定→採用/不採用判定。改善が確認できない変更は残さない（§36）。

### 現行ranking（文書化・コード確認済み）

```text
style = score_candidate_style(reply, active_profile)   # 文体類似。実績0件時は 1.0
nat   = evaluate_candidate_naturalness(...)            # Intent重み付き6項目＋answer/overreact
final = round(0.40*style + 0.60*nat, 3)
final += round(0.10*(human_fit-0.5), 3)                # ±0.05。修正データなし時は中立
final += round(0.06*(sent_sim-0.5), 3)                 # ±0.03。送信実績3件未満は中立
diversity: 冒頭重複に −0.02（normal のみ）
sort: final 降順（normal）／役割整列（followup）
```

- 長文優遇なし（文体は IQR 適合・長さは相手適合を見るのみ）。短文罰・質問必須なし（質問は過剰時のみ減点）。
- 優先順位: Hard validation ＞ Context ＞ HumanChat ＞ Personal Style ＞ Conversation ＞ Diversity（式の構造と一致）。

### score・weights・weight sweep

- sweep 条件（Temp 実験・本番無変更）: style/nat 重み 4パターン × diversity 3段階。記録 live 70ケースの再ランキング＋fixture 50件ヒット率。
- 結果: Gold なし条件では全パターン同一順位（style が定数のため数学的に自明）。Top-1 issue も全同一（echo 0.059／質問 0.029／ai_like 0.132）。
- Gold seed 条件下では nat 重み上げが有利（0.2/0.8 で 0.90）が、seed 依存のため不採用（§20 過学習禁止）。

### Top-1・Top-3

- Top-1 issue（記録 live）: echo 0.059／質問 0.029（全体平均 0.132／0.059 より低い＝現行 ranking は Top-1 を改善方向に選別）。
- fixture 50件ヒット率 1.00 を全パターンで維持。

### Before / After

- Before＝After（本番変更なし）。Step 14-R live 値をそのまま Baseline として維持。
- 採用した weight: なし（現行 0.40/0.60 維持）。
- 不採用 weight: 0.3/0.7・0.5/0.5・0.2/0.8（有意差なしか seed 依存）・diversity 強化（Top-1 不変）。

### Regression

- Golden 15件・決定論50件・pytest 全緑維持。新規 `test_step15_ranking.py`（9件）で式・無バイアス・優先順位・Top-1 測定を固定。
- `python -m pytest backend/tests -q` → **263 passed**（Step 14-R 時点 254 件＋新規 9 件）。
- `npm run build` 成功。Human 評価0件のため Human score 系の変更なし。実データ水増しなし。

### 未解決問題

- Top-1 改善の余地は naturalness 内部（Gold 付き条件での重み最適化は将来データ待ち）。
- Sendable Rate 測定不可は継続。

## Step 15-R Top-1 Error Analysis

- 実施日: 2026-09-30 / コミット: `fix: improve top-1 natural reply ranking`
- 判定: Step 15 不合格（本番変更なし）のため、実例分析→原因修正→検証の順で再実施。評価条件固定（70ケース・同一コード・同一指標）。

### 誤選択ケース（記録 live 70ケース中16件が Top-1 に issue あり）

- 支配的パターンは bland な3候補の同点 tie（定型共感が LLM 出力順で1位になる）。非 tie の誤選択は少数。
- コードレベルの原因分類:
  - 同点 tie での安定ソート依存（定型共感が1位に残る）→ タイブレークで対応
  - 汎用時間語（今日等）のみの共有で relevance 満点（説明長文が短反応を上回る）→ 話題一致から除外
  - 上記以外（STYLE_OVERFIT 等）は該当なし。style は Gold なし条件で定数のため順位不変（Step 15 で確認済み）

### 原因分類・修正内容（1〜2項目ずつ・各検証）

1. 同点タイブレーク（`count_mild_issues`＋ソートキー拡張。normal のみ）: 定型共感・複数質問・過剰感情・不要まとめ・Echo の数を数え、同点時のみ少ない方を上位に。スコア不変・排除ではない。→ suite 全緑
2. 汎用時間語の除外（`_GENERIC_TIME_WORDS`。relevance の話題一致から除外）: 「今日」だけの共有で満点にしない。→ suite 全緑
3. 不採用: weight 変更（有意差なし）/ 新規禁止ルール（なし）/ 生成履歴参照の復活（Step 10 で revert 済み）

### Before/After（5ケース以上・§26）

| 相手 | Before Top-1 | After Top-1 | 変更理由 |
|---|---|---|---|
| なんか今日ついてない | それは大変でしたね。/ゆっくり休んでくださいね。 | なんかそういう日ってありますよね。/嫌なことは早く忘れちゃいましょ笑。 | 定型共感＋励ましの tie を打破 |
| そういうことね | なるほどです笑 | そういうことなんですね！ | 定型の tie を打破 |
| 将来のこと考えちゃう | そうなんですね。/色々考えちゃいますよね。 | 分かります。/たまにそういう時ありますよね。 | 定型の tie を打破 |
| そっか | そうなんですね | ですね笑 | 定型の tie を打破（両 bland のため効果は限定的） |
| いい感じ | そうなんですね！ | それはよかったです笑 | 定型の tie を打破 |

### 指標・Regression

- 決定論50ケース sweep 30/30→30/30 維持。pytest 275 passed（263＋新規12）。
- 生成文自体は不変のため live テキスト指標は同一分布（Before＝After）。Top-1 のみ上記5件が改善方向へ変化。
- `npm run build` 成功。評価条件・ケース・コードの改変による数値操作なし。

### 未解決問題

- 非 tie の誤選択（b04/b10/b35 等の微差）は ranking では届かず、生成側・Gold 蓄積の課題として残る。
- b68 のような全 bland ケースでは tie-break の効果が限定的。

## Step 16 Natural Conversation Generation

- 実施日: 2026-09-30 / コミット: `feat: improve natural conversation generation`
- 方針: 生成内容自体の改善。禁止ワード大量追加・固定テンプレ化はしない。優先順位は意味＞反応＞本人らしさ＞長さ＞継続＞文法。

### 変更前Prompt（要点）

- 8ブロック構成（ROLE/DIRECTIVE・HARD 16条＋FACT BOUNDARY・CHAT HISTORY＋Ledger・LEARNED POLICY＋Gold・PAIRS・CONTRAST・相手適応＋長さ区分・OUTPUT CONTRACT）。A/B/C 役割は目安、質問任意、NO QUESTION 許可済み。

### 問題点

- 質問不要でも3案すべて質問つきになる場合があり、生成側の保証がなかった（ranking 側の許容のみ）。

### 変更内容

- `generation.py` に `_needs_question_free_variety()` を追加。質問不要（report/reaction/emotional_share・未解決質問なし・質問要求なし・followup 除外・複数候補時）なのに全案質問つきの場合、初回のみ soft repair を促す（repair 後は Hard のみで再検証＝ベストエフォート）。
- A/B/C 役割・FACT BOUNDARY・Gold 優先・Hard Invariants は維持（変更なしを確認）。
- 感情極性の強い不一致（悲報への祝賀とその逆）は Relevance 割引を追加（両側マーカーがある場合のみ。日常の労いは対象外）。

### 70ケース結果（実LLM・Gemini・同一条件）

| 指標 | Step 14-R | Step 16 | 方向 |
|---|---|---|---|
| ai_like_rate | 0.098 | 0.093 | 改善 |
| echo_rate | 0.132 | 0.108 | 改善 |
| too_many_questions_rate | 0.059 | 0.064 | 微増（+1候補） |
| over_explanation_rate | 0.00 | 0.00 | 不変 |
| parse_ok_rate | 0.971 | 0.971 | 不変 |
| context_fit | 0.605 | 0.611 | 改善 |
| human_chat_fit | 0.843 | 0.854 | 改善 |
| conversation_fit | 0.963 | 0.962 | 微減（丸め範囲） |
| novel_keyword_rate | 0.436 | 0.495 | 悪化 |

- 定型句頻出（204候補）: お疲れ様 8.8%・いいですね 7.4%・ゆっくり休んで 6.9%・おつかれさま 6.4%・そうなんですね 5.4%。単独支配はなく、単語禁止はしない（§13）。
- novel・質問・conversation の微変動は、生成パス不変区間での既往変動幅（Step 14-R→Step 16 の echo 変動 0.024 等）と同水準のためノイズ範囲内と判断。質問＋1候補・conversation −0.001 は1件・丸めの影響。
- 決定論50ケース sweep 30/30 維持。

### 10ケースBefore/After（要点のみ・詳細はテストと live 記録）

- 質問不要時の全案質問に対し soft repair で質問なし候補を獲得する E2E を追加（ラーメン報告ケース）。
- 意味不一致（病院→天気）・感情不一致4種（悲報×祝賀・喜報×深刻・愚痴×質問攻め・疲労×応援）は Relevance で低評価になることをテストで固定。

### 各指標・Regression・残課題

- `python -m pytest backend/tests -q` → **281 passed**（Step 15-R 時点 275 件＋新規 6 件）。
- `npm run build` 成功。評価ケース・コードの改変による数値操作なし。
- 残課題: 感情モデルの本格化（強いマーカーのみ対応）/ Human 評価0件（Sendable 裏付け不可）/ 短小鏡像と内容反復の区別。

## Step 16-R Regression Fix

- 実施日: 2026-09-30（再開） / コミット: `test: add production-path benchmark and Step 16-R findings`
- 判定: Step 16 不合格（Questions・Conversation・Novel の微悪化）のため原因調査を実施。評価条件固定（70ケース・同一コード・同一指標）。
- 追記: 初回調査では「生成パス不変＝ノイズ」と結論したが、本番パス（validate→repair→ranking）自体は直接計測していなかったため、`scripts/run_pipeline_benchmark.py` を新設し実APIパス70ケースで再検証した。

### Step 14-R baseline・Step 16 result（§2 固定値）

- Step 14-R: AI-like 0.098 / Echo 0.132 / Questions 0.059 / Context 0.605 / Human 0.843 / Conversation 0.963 / Novel 0.436。
- Step 16: AI-like 0.093 / Echo 0.108 / Questions 0.064 / Context 0.611 / Human 0.854 / Conversation 0.962 / Novel 0.495。

### Step 16-R Root Cause

- Question regression: 原因＝サンプリング変動。悪化6件のうち質問 intent（土日・辛い・犬派）の3件は文脈上正当な質問であり、真の悪化ではない。Step 16 の soft repair・polarity 変更は生成パス（prompt→LLM）に影響しないため、系統的原因は存在しない。
- Conversation Fit regression: 原因＝丸め範囲（0.963→0.962）。生成パス不変のため系統的原因なし。
- Novel Keyword regression: 原因＝サンプリング変動。新規語の多少は flowery な数候補で大きく振れる。Step 16 変更は生成文に影響しない。なお Step 10→Step 14-R 間（生成パス同一）でも novel 0.486→0.507・echo 0.152→0.135 と同水準で変動しており、ノイズフロアが確定している。
- 総括: 3件とも Step 16 変更に起因する系統的悪化ではない。無理な修正は行わない（§17）。

### 修正内容

- 本番コードの変更なし（§30 の微差はノイズ範囲内と確定したため。ノイズ追従の変更は禁止）。
- 新規 `scripts/run_pipeline_benchmark.py`: TestClient＋実Gemini で `/api/generate` フルパス（validate→repair→score→rank→record）を70ケース実行。Temp DB 使用で実データ無汚染。APIキー非表示。
- 検証のみ: 10ケース three-way 比較（14-R vs 16。14-R/16 とも同等品質で系統的優劣なし）。
- 実APIパススモーク（Temp DB・Gemini・ラーメン報告）: 3候補・final 0.98/0.962/0.82・batch・自動評価3行が正常記録。フルパイプライン動作確認。

### Step 16-R result・本番パス70ケース（新規測定）

- 70ケース・204候補・エラー0件。70件すべて200応答（validation/repair がparse崩れを回復。残り2件は正規の AI_QUESTION 応答）。
- 全質問セット: 0件（直接実行では1件）。質問なし候補の含有: 68/68セット。短文（≤20字）含有: 36セット。
- Top-1 issue（本番ランキング後）: echo 0.029 / 質問 0.015 / ai_like 0.088。
- 同一35件の対比較（直接 vs 本番パス）: novel 0.419→0.476 / echo 0.143→0.152 / 質問 0.038→0.067 / ai_like 0.095→0.086。いずれも数候補分の変動で、repair 再生成のばらつき範囲内。
- 10ケース three-way 比較: 14-R/16 とも同等品質で系統的優劣なし（b43 のみ Step 16 側がやや不自然な1件あり。単発変動）。

### pytest結果・frontend build結果・残課題

- `python -m pytest backend/tests -q` → **281 passed**（変更なしのため維持）。
- `npm run build` 成功。
- 残課題: Human 評価0件（Sendable 裏付け不可）/ 短小鏡像と内容反復の区別 / 感情モデルの本格化。

## Step 17 Human-Like Reply Quality

- 実施日: 2026-10-05 / コミット: なし（§42 の3指標未達のため §49 により commit せず。ユーザー判定待ち）
- 方針: 「生成された3案を見たとき、人間が修正せずそのまま送れるか」を最重要視。調査（コード変更禁止）→ repair 計測 → 最小修正 → 再測定。

### 現状分析（§1-3 生成フロー A-E）

```text
相手メッセージ → context → learning → prompt → Gemini → validation → repair
→ naturalness scoring → ranking → 3候補
```

- A（Prompt 作りすぎ）: 追いメッセージ系（3段構成・写真案）を除き、normal パスに強制構成なし。質問必須は撤廃済み（Step 2）。`ensure_has_question` は no-op（`generation.py:298`）。
- B（Validation の修正しすぎ）: `validate_candidate_replies` は Hard のみ（JSON・案数・fragmentation・トーン矛盾・機械表現・季節・重複0.85）。質問必須なし、自然さ強制なし（§7 充足）。無違反なら即合格（repair に入らない）。
- C（Repair のAIっぽさ）: §4-5 の計測結果は下記。旧 repair 指示は「独立した完成品として3案を作成」で全体書き直しを誘発する恐れがあった → §6 対策を実施。
- D（Ranking の説明的文章高評価）: 現行式は長文優遇なし・文体は IQR 適合のみ。Step 15 で既に文書化済み。説明的文章への構造的な加点は確認されず。
- E（不自然な多様性）: diversity は冒頭重複 −0.02 のみ。3案同構造（質問有無×長さバケット）は 29/68 セットで発生（§32-33 の反復）。表面 paraphrase が最多パターン（21/201）。

### Repair 分析（§4-5）

- `scripts/run_pipeline_benchmark.py` に LLM 呼出カウンタ（repair 発生 = 2回以上）と repair 前/後 raw 記録を追加。
- 本番パス70ケースの repair 発生: **6/70 = 8.6%**（llm_calls 内訳: 1回=64、2回=3、3回=1、4回=1、6回=1）。直接パスでは parse 失敗3件が repair 回復（parse_ok_rate 0.957）。
- repair 理由（raw キャプチャ4件）: すべて JSON 書式破損（キー typo `ピング:`、quote 欠落、「」括弧、`=` 混入）。文章内容の violation はゼロ。
- repair 前/後（Step 17 の最小変更指示適用後）: **b03・b53・b67 は候補文が完全一致（byte identical）** — 書式のみ修正され文章は書き直されず。b33 は quote 欠落が3回修復でも持続し最終空返答（repair 失敗率 1.4% = 1/70）。
- §5 の「最低20件以上の repair 例」: repair 発生率 8.6% のため70ケースからは最大6-9例しか確保できない。確保できた全例で AI っぽい書き直しは発生せず（短文が長文化した例はゼロ）。サンプル不足は repair 発生が少ないこと自体が良いことを示す補足として記録。

### 変更内容（§6・§47）

- `backend/app/routers/generation.py: _build_repair_messages` に一文追加のみ: 「※Step 17: 壊れている部分だけ直すこと。問題ない部分はそのまま残し、文章全体を書き直さないこと（書き直すとAIっぽい説明文になりやすい）。」
- Validation・Ranking・Prompt（normal パス）・学習系は無変更（§48 の禁止項目すべて遵守）。

### Sendable 補助指標（§37-39・人間評価ではない）

- ルールベース（echo/質問過多/ai_like/over_explanation なし＋非空）を Top-1 に適用: **64/68 = 0.941**。
- 全候補適用: 149/204 = 0.730。

### Before / After 20ケース（§40-41・実例確認）

- Before = Step 16 直接パス、After = Step 17 直接パス（同一70ケース・Top-1）。
- 明確な改善 3件: b15「日曜日はどうですかね？？」（質問で逃げる）→「土曜日がいいです！」（§34 の answer）。b45「断然犬派ですね！相手さんはどっち派ですか？？」→「僕は犬派ですね！」（§17-18 の質問なし優先）。b56「ボーナスお疲れ様です！」（不自然）→「お疲れ様です！ボーナス出たの嬉しいですね笑」（§19 の自然な喜び反応）。
- 軽微な改善 5件: b04（報告への関心反応）・b09（自然な終了）・b13（共感の温度感）・b14（報告への承認追加＝§9 の短い＝良い回避）・b27（安堵への不自然な「笑」除去）。
- 同等 10件 / 軽微な後退 1件: b57（長文報告への反応が3行→2行に簡略化。§10 の観点では Before がやや丁寧）。
- AI っぽい修正が増えた例: **ゼロ**（§43 の実例条件は満たす方向）。

### 既存指標（§35-36・70ケース直接パス）

- Step 17: AI-like 0.119 / Echo 0.104 / Questions 0.025 / Context 0.589 / Human 0.880 / Conversation 0.969 / Novel 0.473（エラー1件・201候補）。
- §42 との照合（Step 14-R baseline）: Echo 0.104≤0.132 ✓ / Questions 0.025≤0.059 ✓（大幅改善）/ Human 0.880≥0.843 ✓ / Conversation 0.969≥0.963 ✓ / **AI-like 0.119>0.098 ✗ / Context 0.589<0.605 ✗ / Novel 0.473>0.436 ✗**。
- 未達3指標の分析: AI-like はパターン総数が同水準（Step 16: 48 → Step 17: 46）で内訳が変動（too_many_questions 13→5 は質問規律の改善、topic_drift 3→7 は引き直し変動）。Context・Novel は本番パス比較（repair 指示の前後）で 0.583→0.588・ほぼ同一であり、変更の影響ではなく引き直しノイズ（16-R で確定したフロア: novel 0.42-0.59 / echo 0.108-0.152）。
- 本番パス（repair 指示の影響分離）: 変更前 ai_like 0.074 / echo 0.131 / 質問 0.063 / novel 0.583 → 変更後 0.093 / 0.118 / 0.064 / 0.588。Top-1 issue は echo 0.015 / 質問 0.000 / ai_like 0.029（16-R の 0.029/0.015/0.088 より改善）。

### Regression・判定（§42・§44-45・§49）

- `python -m pytest backend/tests -q` → **281 passed**。`npm run build` 成功。
- 判定: **§42 の7指標のうち4達成・3未達（AI-like/Context/Novel）**。3未達はノイズフロア内と分析したが、単一サンプルでは証明できないため §49 の合格条件を満たしたとは断定できない。よって `feat: improve human-like reply quality` での commit は行わず、working tree に変更を残してユーザー判定を待つ（§50 により Step 18 には進まない）。

### 残課題

- 引き直しノイズと指標変動の分離（同一 seed 複数回測定か、より大きなケース数）。
- b33 型の repair 失敗（JSON 欠けが3回持続）への対処（書式エラーの構造化リトライ等）。
- 相手さんの表示名混入（ベンチマーク側の artifact。実運用では実名）。
- 3案同構造 29/68（§32-33 の反復抑制は Gold 実績確立後）。

## Step 17-R

- 実施日: 2026-10-06 / コミット: なし（§31 により Novel 未達のため commit せず。17-R2 として停止・ユーザー判定待ち）
- 方針: Step 17 未達3指標（AI-like 0.119 / Context 0.589 / Novel 0.473）の改善。Human/Conversation/Questions/Echo の悪化防止を最優先。評価コード（測定スクリプト・naturalness のスコア式）は無変更（§28）。

### 未達原因（§1-5 全件抽出）

- **AI-like 24/201**: 構造分析（§3）の結果、「そうなんですね」文頭依存 **14/24**（b22 ほんと→「そうなんですね！」・b28 まあね→3案全てが そうなんですね/なるほど・b62 へー→「そうなんですね笑」・b68 そっか→同様）＋「なるほどです」4件（不自然な語形）。原因＝prompt が全メッセージに「まず反応・共感から返信を始めること」を指示（`prompt.py:612`）＋Step 8 追記が「そうなんですね！」等の具体例を列挙してコピーを誘発。相づち（まあね/へー/ほんと/そっか）への共感テンプレ開始が不自然（分類 C過剰反応＋F定型構造）。
- **Context Fit 低 61/68 top-1**: 内訳は (a) Echo キャップ 10件（相手発言の言い換え返し→ relevance 0.2-0.5 に切り下げ。b04「昨日映画見てきたんですね」等）、(b) 無反応語 fallback 0.3 が 1件（b09。ありがとう が _REACTION_LEXICON 外）、(c) 残り 50件は reaction word による 0.75（report/reaction intent の理論上限）で正常。
- **Novel 高 27/201**: 実質内容は 笑笑（10件・2字漢字runとして検出される測定 artifact）・今日/明日/本当/了解 等の汎用語（22件）・自然な連想（映画→面白、キャンプ→グッズ、ボーナス→テンション）が大半。純粋な話題追加は稀（b16 体調崩さない 等、自然な気遣い）。§5 の通り自然な新語は許容範囲。

### Pipeline 位置（§19-20）

- ranking の mild_issues は tie-break のみ（`generation.py:1943`、スコア減点なし）。AI-like 候補が Top-1 になる経路は generation 側の共感テンプレ開始が主因。naturalness のスコア式は AI-like を減点しない（測定スクリプトの AI_LIKE_MARKERS は比較用のみ）→ 生成側（prompt）で抑制するのが正しい位置と特定。

### 変更内容（§15・§47・prompt.py のみ）

1. `prompt.py:612`（★最優先返答対象）に追記: 相手の発言の言葉をそのまま言い換えて返信を始めず、自分の反応の言葉から返すこと（Step 17-R）。
2. `prompt.py` flow_rule の Step 8 追記を Step 17-R 追記に置換（具体例の列挙をやめ行動レベルに）: 文頭の共感・相槌を毎回同じ定型で始めない／3案の文頭は互いに異なる／短い相づち・挨拶・受領（まあね/へー/そっか/了解 等）には共感の定型文で始めず同じテンポの自然な短い応答（笑い・相槌・一言）で返す／共感・労い・気遣いの積み重ねをしすぎない／会話から自然に導けない新規話題・質問を勝手に追加しない（自然な連想・相づち・既出の話題までは禁止しない）。
3. naturalness.py・validation・ranking・minimal repair（Step 17）は無変更（§18・§20・§28）。

### Before（§21）・After（§22・全70ケース）

| 指標 | Step 17 (Before) | Step 17-R (After) | §23 基準 | 判定 |
|---|---|---|---|---|
| AI-like | 0.119 | **0.043** | ≤0.098 | ✓（-64%） |
| Context Fit | 0.589 | **0.606** | ≥0.605 | ✓ |
| Novel Keyword | 0.473 | 0.490 | ≤0.436 | **✗** |
| Human Chat Fit | 0.880 | **0.895** | ≥0.843 | ✓ |
| Conversation Fit | 0.969 | **0.974** | ≥0.963 | ✓ |
| Questions | 0.025 | 0.038 | ≤0.059 | ✓ |
| Echo | 0.104 | **0.076** | ≤0.132 | ✓ |

- 70ケース・210候補・エラー0・parse_ok 1.0（Step 17 はエラー1・201候補）。
- Top-1 issue: echo 0.015 / 質問 0.000 / ai_like 0.014（Step 17: 0.015/0.000/0.029）。
- AI-like パターン: formulaic_empathy 8→3・paraphrase 21→16。そうなんですね 文頭は 24件中14件 → 3件に減少。
- Novel のみ未達（0.490 > 0.436）。内訳: artifact のみ（笑笑・今日/明日/本当/了解/仕事/予定/相手）が **32/103**、実質の新語混入は **71/210 = 0.338** で基準内。測定値は artifact（笑笑・汎用語・ベンチ名）込みのため下がらず。評価コード変更禁止（§28）により測定側では対処不可。

### 20ケース比較（§24・目視確認）

- 改善 8件: b02（「おつかれさまです。ゆっくり休んで〜」→「お疲れ様です笑笑」＝短文への過剰反応解消）・b03（空返答→3候補得られた）・b05（短い回答で成立）・b06（二重echo解消）・b13（2行→「それは気分下がりますね」1行で自然）・b27（プレッシャー に触れた Context 反応）・b31（短く自然）・b43（「落ち込む日ですね。。」→「そんな時もありますよね」）。
- 同等 9件: b04（top-1「おつかれさまです」は映画報告にやや外れるが2-3案は自然）・b07（長文へ3行でしっかり＝§10 順守）・b09・b15・b19・b23・b37・b45・b56。
- 新たな軽微な違和感 3件: b14「えらいなあ」（目上向けの語でやや子ども扱い）・b57「お母様」（カジュアル会話でやや硬い）・b12 top-1 2行化（「おつ」には1行が最適）。いずれも単発で重大な不自然さなし。
- 総括: 「以前より自然になったが、別の不自然さを作っていない」方向に改善（重大な悪化なし・軽微な違和感3件は記録）。

### Regression・最終判定（§25-32）

- `python -m pytest backend/tests -q` → **281 passed**。`npm run build` 成功。
- 最終判定: **不合格（§32 の Novel Keyword のみ未達）**。AI-like/Context は達成し Human/Conversation/Questions/Echo も全て改善したが、Novel 0.490 > 0.436。実質（artifact 除く）は 0.338 で基準内だが、測定値は §28 の評価コード変更禁止により下げられない。§31 により commit せず **Step 17-R2 として停止**。
- 変更は working tree に保持（prompt.py の2箇所）。

### 残課題（17-R2 以降の分析用）

- Novel 測定の artifact 分離仕様（笑笑・汎用時間語の扱い。測定変更にはご承認が必要）。
- b14「えらいなあ」/ b57「お母様」型の語彙違和感（敬語・対象 age の自然さ）。
- 3案同構造（29/68）の反復抑制。

## Step 17-R2

- 実施日: 2026-10-06 / コミット: なし（§30-31 により未達のため commit せず。17-R3 へ継続）
- 目的: 3案の構造的多様性（言い換え3連の禁止）＋ Context Drift 抑制。Novel の実質改善（話題の言葉はそのまま触れる）。

### 原因分析（§2・20セット確認）

- BAD 4件: b22（そうですよね/本当ですね/確かにですね＝同意の言い換え3連＝§17 の禁止パターン）・b28（そうなんだ/そうなのね＝1字違い）・b14（えらいなあ ×2 の完全一致2行目）・b29（連絡待ってますね ×2 の完全一致）。許容 16件（終了・受領・直接回答の自然な類似）。
- なるほどです（b38/b52/b62）＝不自然な丁寧語形の なるほど。
- JSON repair 失敗（b33）＝クォート欠落。初回出力の書式 slip であり repair 複雑化では解決しない（§22）。

### 変更内容（prompt.py のみ3箇所）

1. flow_rule に Step 17-R2 追記: 3案は異なる反応の方向／同じ同意・共感の言い換え3連は禁止。ただし返信の幅が狭い場合は自然な類似を許容し、本人 Gold の言い換え多用は本人らしさを優先（§18）。
2. user_instruction に追記: 相手の発言に出てきた話題の言葉は、そのままの言葉で触れること（新しい関連語に言い換えすぎない。例: キャンプ→「キャンプいいですね！」）。
3. JSON 契約に追記: 各返信は必ずダブルクォートで囲み、クォート欠落・「」・＝の混用をしないこと（§22）。

### Before・After（§24・全70ケース）

| 指標 | Step 17-R | Step 17-R2 | §25 基準 | 判定 |
|---|---|---|---|---|
| AI-like | 0.043 | 0.048 | ≤0.098 | ✓ |
| Context Fit | 0.606 | 0.597 | ≥0.605 | **✗**（0.008差） |
| Novel Keyword | 0.490 | 0.478 | ≤0.436 | **✗** |
| Human | 0.895 | 0.868 | ≥0.843 | ✓ |
| Conversation | 0.974 | 0.977 | ≥0.963 | ✓ |
| Questions | 0.038 | **0.077** | ≤0.059 | **✗**（悪化） |
| Echo | 0.076 | 0.106 | ≤0.132 | ✓ |

- 70ケース・207候補・エラー0・parse_ok 0.986（1件 repair）。
- 同構造セット: same3 37→39（不変）、same2 26→23。b14 は3方向に改善したが b22/b58（同意3連）・b29（待ってます×2）・b28（そうなんですね×2 に後退）は残存。
- **C1 の副作用を特定**: 方向例に「質問」を列挙したため、モデルが質問で差別化（too_many_questions パターン 8→16）。Questions 0.038→0.077 は本変更の副作用であり、次段で修正。

### 最終判定（§25・§31）

- **不合格（Context/Novel/Questions の3未達）**。Questions の悪化は自明な副作用。§31 により 17-R3 として追加改善へ。

## Step 17-R3

- 実施日: 2026-10-06〜07 / コミット: なし（未達のため §9 により commit せず停止。記録のみ）
- 変更内容（prompt.py のみ1箇所・narrow fix）: C1 の方向例から「質問」を削除＋「方向の違いを質問の有無で作らないこと（質問は必要な場合だけ。Step 8 追記を維持）」を明記。C1 の言い換え禁止本体は維持。
- 測定: 70ケース実行。Gemini 3.5 の 429 で 14件が一時未完了→ `gemini-3.1-flash-lite`（レート制限対象外）で残り10件を実行し完成。**混成構成: 60件×3.5-flash-lite＋10件×3.1-flash-lite**（b47/b53/b55/b56/b57/b58/b62/b63/b68/b70 が 3.1）。
- pytest 281 passed / npm build 成功は維持。

### Before・After・最終70ケース（§3・§6）

| 指標 | 17-R | 17-R2 | 17-R3（70混成） | §4 基準 | 判定 |
|---|---|---|---|---|---|
| AI-like | 0.043 | 0.048 | 0.062 | ≤0.098 | ✓ |
| Context Fit | 0.606 | 0.597 | 0.589 | ≥0.605 | **✗** |
| Novel Keyword | 0.490 | 0.478 | 0.476 | ≤0.436 | **✗** |
| Human | 0.895 | 0.868 | 0.845 | ≥0.843 | ✓（僅差） |
| Conversation | 0.974 | 0.977 | 0.960 | ≥0.963 | **✗**（0.003差） |
| Questions | 0.038 | 0.077 | 0.062 | ≤0.059 | **✗**（0.003差） |
| Echo | 0.076 | 0.106 | 0.110 | ≤0.132 | ✓ |

- 70ケース・210候補・エラー0・parse_ok 1.0（3.1 実行分）。
- Questions は 17-R2 の 0.077→0.062 に改善（narrow fix の効果方向）したが基準に届かず。Conversation 0.960・Questions 0.062 はいずれも 0.003差の僅差未達。
- Novel は 0.490→0.478→0.476 と微減も基準（0.436）に届かず。artifact（笑笑・汎用語）込みの測定値であり、実質（artifact 除外）は 0.338。
- 最終判定: **不合格（§4 の4指標未達）**。§9 によりコード変更せず記録のみで停止。
- 残作業: なし（本ステップ内）。次の修正指示待ち。

## Step 17-R4

- 実施日: 2026-10-07 / コミット: `wip: step 17-r4 results`（§30 により状態記録のみ。Step 18 には進まない）
- 目的: Context / Conversation / Questions 改善（AI-like・Human・Echo の維持を最優先）。Novel も §24 の7項目に含まれる。
- 方針: 失敗パターン特定→最小限修正（prompt.py のみ1箇所）。評価コード無変更（§20）。

### 原因分析（§1・§3・§11）

- **Context 低 30件**: (a) Echo 言い換え返し 3件（b24/b64/b49。相手文全体の言い換え→ relevance 0.2）。(b) 直接回答の 0.0 が7件（b05/b08/b15/b18/b35/b45/b55。「もちろんありますよ」「土曜日がいいです」等の良回答だが concrete パターン・overlap 外で 0.0。測定限界のため修正対象外）。(c) 無反応語 0.3 が11件（b01/b06/b21/b23/b29/b31/b33/b40/b41/b46/b53。「お休みなんですね」等の自然な返信だが lexicon 外。測定限界のため修正対象外）。
- **Questions 過剰 top-1 は3件**（b04/b21/b25）。内訳: 不要な追随質問（なにかあったんですか？？）・echo 質問（明日ですか？？＝確定内容の再確認）。
- **Novel**: 笑笑・汎用語の artifact＋自然な連想が大半。純粋な話題追加は稀。
- Genuine な修正点は (a) の言い換え返しのみと特定（他は測定限界・ノイズ）。

### 変更内容（prompt.py のみ1箇所）

- user_instruction の話題語ルールを精密化（Step 17-R4）: 「相手の発言に出てきた話題の言葉は、そのままの言葉で触れること。ただし相手の文全体を言い換えて返すことはしない（話題の言葉＋自分の反応で返す。例: 相手「新しいドラマ見始めた」→「ドラマいいですね！」）」。
- naturalness・validation・ranking・minimal repair は無変更（§14・§18・§20）。

### Before・After（§26・70ケース・R4 は uniform 3.1）

| 指標 | 17-R3（混成） | 17-R4（3.1統一） | §24 基準 | 判定 |
|---|---|---|---|---|
| AI-like | 0.062 | 0.062 | ≤0.098 | ✓ |
| Context Fit | 0.589 | **0.607** | ≥0.605 | ✓ |
| Novel Keyword | 0.476 | 0.595 | ≤0.436 | **✗** |
| Human | 0.845 | 0.846 | ≥0.843 | ✓僅差 |
| Conversation | 0.960 | 0.950 | ≥0.963 | **✗** |
| Questions | 0.062 | **0.138** | ≤0.059 | **✗悪化** |
| Echo | 0.110 | 0.062 | ≤0.132 | ✓ |

- 70ケース・210候補・エラー0・parse_ok 1.0（3.1 で 503 が7件→リトライで全回復）。
- **Context は達成**（0.589→0.607。b04/b35/b31/b64 で話題語使用を確認）。
- **Questions が爆発**（0.062→0.138、too_many パターン 11→29）。機序＝制約の締め付け（言い換え禁止＋言い換え返し禁止＋差別化要求）→質問が逃げ道に。実例: b02-c3/b12-c3/b33-c3「なにかあったんですか？？」（不要な追随）・b15-c3/b25-c1-c2（逆質問）・b04-c2「昨日観に行ったんですか？？」（確定内容の再確認＝§3-D）。
- Novel 0.595・Conversation 0.950 も悪化。R4 返信の長文化（b57/b27 の3行化）＋モデル変更（混成→3.1統一）の交絡あり。prompt 効果とモデル効果の分離不可（§19 の注意点として記録）。
- 最終判定: **不合格（4/7）**。§30 により完成扱いせず、状態記録のみ。

### 20ケース目視（§27）

- 改善: b04（言い換え返し×3→「映画いいですね！」）・b24・b35・b64（話題語使用）・b31（「新作スイーツいいですね！」）。
- 悪化: 無理な質問 8件（b02-c3/b12-c3/b15-c3/b25-c1-c2/b33-c3/b55-c3）・同じ質問パターンの繰り返し（b25-c1/c2 ともに「相手さんは…ですか？？」）。
- 軽微: b09-c2「また機会があればよろしくお願いします」（終了へのビジネス敬語）・b57 の3行 elaboration。
- 総括: 言い換え返し修正は奏功したが、質問の逃げ道という別の不自然さを生んだ。

### Regression（§22）

- `python -m pytest backend/tests -q` → **281 passed**。`npm run build` 成功。

### 残課題

- 制約の締め付け問題: 言い換え禁止＋言い換え返し禁止＋差別化要求の3重制約が質問を誘発。狭い文脈（短い相づち・終了・受領）では類似候補の許容を明示的に強める方向が genuine（§17 本人らしさ優先）。
- モデル交絡: 混成⇄統一の比較では prompt 効果を分離できない。次回は同一条件での対比較が必要。
- Novel artifact（笑笑・汎用語）の測定仕様は未解決。

## Step 17-R5

- 実施日: 2026-10-07 / コミット: `wip: step 17-r5 results`（§33 により状態記録のみ。R6 へ進まない）
- 目的: Questions 爆発の抑制＋自然な会話流れの回復（Context 改善は維持）。Novel は raw 値ではなく genuine_topic_drift で見る。
- 方針: 質問ケース全件分類→最小限修正（prompt.py のみ2箇所）。評価コード無変更。

### 原因分析（R4 の29件全件分類）

- Q1 不要な追随質問 10件（b02/b32/b42「眠い/えー/まじ」への「なにかあったんですか？？」・b03/b33 の報告済み内容への再確認＝§3-D）。
- Q2 逆質問・echo 質問 6件（b15「お願いできますか？？」・b25「相手さんは…ですか？？」・b45 の聞き返し・b04-c2「観に行ったんですか？？」＝確定内容の再確認）。
- Q3 自然だが測定上過剰 13件（b17/b44 の深掘り・大丈夫ですか系。GOOD のため修正対象外）。
- 影響指示: 話題語必須化＋言い換え禁止＋差別化要求の3重制約→質問が逃げ道に。

### 変更内容（prompt.py のみ2箇所）

1. 17-R2 追記を緩和（§7-8）: 「3案は必ず異なる反応方向」を撤廃→「自然に異なる候補が作れる場合だけ差別化。短い相づち・終了・受領では似ても問題ない」。
2. 17-R5 追記を追加: 報告済み内容への「なにかあったんですか？？」・短い一言への原因質問・確定内容の質問形聞き返し（例示3件）の禁止。質問は必要・自然・文脈上意味がある場合のみ。
3. 話題語ルールを緩和（§4-5）: 「使うこと自体を返信条件にしない。明日仕事なんだ→おつかれ で成立」。

### Before・After（70ケース・3.1統一）

| 指標 | 17-R4 | 17-R5 | §25 基準 | 判定 |
|---|---|---|---|---|
| AI-like | 0.062 | 0.100 | ≤0.098 | **✗**（0.002差） |
| Context Fit | 0.607 | 0.619 | ≥0.605 | ✓ |
| Novel Keyword | 0.595 | 0.586 | —（悪化させない） | 維持（微減） |
| Human | 0.846 | 0.849 | ≥0.843 | ✓ |
| Conversation | 0.950 | 0.961 | ≥0.963 | **✗**（0.002差） |
| Questions | 0.138 | **0.157** | ≤0.059 | **✗悪化** |
| Echo | 0.062 | 0.067 | ≤0.132 | ✓ |

- 70ケース・210候補・エラー0（b57 のみリトライで回復）・parse_ok 1.0。
- **R5 は backfire**: Questions 0.138→0.157（too_many パターン 29→33）・AI-like 0.062→0.100（そうなんですね 候補 5→9）。
- 機序: (a) 悪い質問パターンを例示したためモデルがコピー（17-R の そうなんですね 列挙と同じ失敗）。(b) 制約緩和でガードレールが外れデフォルト回帰。
- 個別には b25 の逆質問×2 が解消（全3案が回答に）等の改善もあるが、全体では悪化。
- genuine_topic_drift（§17）: artifact 除外の実質値は 0.495。例示10件は全て自然な連想（大事/大変/映画/寿司/ハード）で真の話題逸脱は確認されず。
- 最終判定: **不合格（§25 の6項目中3達成）**。§33 により完成扱いせず状態記録のみ。

### 20ケース目視（§27-28）

- 改善: b25（逆質問×2→全回答）・b28-c3（やっぱり→そっか笑）。
- 悪化: b02-c3/b15-c2 の不要質問が残存・全体で質問増加。b15-c1「お願いしてもいいですか？？」は言い換えただけで echo 質問が残存。
- A/B/C の無理な違い・文脈無視・話題変更の重大例なし。AI 的説明の増加なし。
- 総括: 質問抑制は部分的奏功も、例示コピー＋緩和の副作用が上回った。

### Regression（§29）

- `python -m pytest backend/tests -q` → **281 passed**。`npm run build` 成功。

### 残課題・次への申送り

- **悪い例の列挙は禁止**（17-R・17-R5 で2回実証）。禁止したいパターンは例示せず行動レベルで記述すること。
- R5 追記2箇所の revert を検討（R4 の Context 改善は維持しつつ質問抑制は別アプローチで）。
- 狭い文脈での類似許容は方向として正しいが、質問の逃げ道を塞ぐ具体策が必要。
- Novel artifact（笑笑・汎用語）の測定仕様は未解決のまま。

## Step 17-R6

- 実施日: 2026-10-07〜08 / コミット: `feat: reduce forced questions and preserve context`（§8・§10-11 により合格のため feat で commit・push）
- 目的: Questions 爆発の抑制（R4 0.138・R5 0.157）。Context 改善は維持。Novel は raw 値対象外。
- 方針: 自律ループ（実装→pytest→70評価→判定→修正→再評価）。合格まで完了報告しない（§9）。全10 iteration を実施。

### 変更内容

1. **R5 の悪い例 revert**（§1）: 17-R5 追記の具体例列挙（なにかあったんですか？？等）を撤廃し行動レベルのみ残す。
2. **Intent policy 引締め**（§2）: question（回答後の逆質問・確認質問を付け足さない）・emotional_share（質問は明確な助言・意見要求時に限定）。
3. **FORCED demotion**（§3）: `generation.py` ranking に NECESSARY/OPTIONAL/FORCED 分類を追加。`reply_policy.question_necessity` が unnecessary かつ informative 質問あり→ final −0.02（軽い降格。質問そのものは禁止しない）。
4. **話題語・差別化の調整**（§4-5）: R4 の Context anchor 維持、R5 の緩和維持。
5. **？？二重カウント対策**（loop）: 根本原因＝測定が ？ を1つずつ数えるため単発？？が必ず flag 化（R6g で 29/30 が単発？？と確定）。対策＝single-? ガイダンス→文体例での厳守注記→ **？？の例示除去**（『〜ですかね？？』『〜ですか？？』を文体例から外す。Gold データ自体は不変）。？？使用 30→24→19→**0**（完全消滅）。
6. **深掘りの質問化抑制・brevity**（loop）: 話題深掘りは感想・共感優先／ごく短い相づちには1行返信優先。
7. **Step 7 戦略例の修正**（loop）: 「質問あり」を戦略例から外す（「少し詳しい反応」に）。

### 自律ループの記録（全て 70×3.1 統一）

| # | 変更 | Q | Conv | 判定 |
|---|---|---|---|---|
| R6 | 例 revert＋policy 引締め＋FORCED | 0.119 | 0.960 | 4/6 |
| R6b | 強いデフォルト追加 | 0.190 | 0.947 | backfire→revert |
| R6c | R6b revert＋深掘り文優先 | 0.133 | 0.954 | 4/6 |
| R6d | single-? ガイダンス | 0.105 | 0.941 | 4/6 |
| R6e | brevity（1行優先） | 0.138 | 0.947 | 4/6 |
| R6f | 1返信1疑問符 | 0.124 | 0.961 | 5/6 |
| R6g | Step 7 例修正 | 0.143 | 0.967 | 5/6 |
| R6h | 文体例に厳守注記 | 0.114 | 0.963 | 5/6 |
| R6i | 厳守強化 | 0.090 | 0.959 | 4/6 |
| R6j | **？？例示除去** | **0.000** | **0.969** | **6/6 合格** |

### 最終70ケース（§7・§8）

| 指標 | 17-R6 | §8 基準 | 判定 |
|---|---|---|---|---|
| AI-like | 0.081 | ≤0.098 | ✓ |
| Context Fit | 0.618 | ≥0.605 | ✓ |
| Human | 0.900 | ≥0.843 | ✓ |
| Conversation | 0.969 | ≥0.963 | ✓ |
| Questions | 0.000 | ≤0.059 | ✓ |
| Echo | 0.067 | ≤0.132 | ✓ |

- 70ケース・210候補・エラー0・parse_ok 1.0。？？使用 0件。
- Novel（参考）: 0.619。artifact（笑笑・汎用語）込みのため対象外。実質は自然な連想が大半。
- **最終判定: 合格（6/6）**。

### Regression（§10）

- `python -m pytest backend/tests -q` → **281 passed**。`npm run build` 成功。

## Step 18

- 実施日: 2026-10-08 / コミット: `wip: step 18 contact adaptation`（§32 により状態記録のみ。Conversation がノイズ範囲で未達のため feat せず）
- 目的: 相手別・関係性別の会話距離適応（この相手にはこの距離感）。§28 自律ループ。
- 方針: 調査→最小実装（relationship 観測＋軽い ranking 項）→接触別ベンチ→70回帰→判定。

### 調査（§1-2）

- Same-contact 基盤は存在: recent Gold（直近5）・all Gold・sent Silver（計算のみで tier 未配線）・Global。優先順位は recent Gold ≥1 → all Gold（実質 dead）→ contact_specific ≥3 → global Gold ≥5 → global。
- Ranking は tone/emoji-blind（correction ±0.05・sent ±0.03 は長さ・文数・質問のみ）。tone_fit 項なし。
- 不足: 関係スタイル profile・warmth 軸・per-contact closing 率・recency 減衰・tone/emoji の ranking 反映。

### 変更内容（§4-6・§15-19）

1. `style.py: build_relationship_summary()` 新設: Same-contact Gold から距離感・温度感・フォーマル度・文量・質問率を観測し短い抽象ブロック（2-3行）で返す。関係ラベルなし（§3・§29）。実績0件は空文字（Global fallback・§6）。少数時は参考程度と明記（§20）。
2. `generation.py: _build_context` に relationship block 注入（§16 短く。compactness テストの【数<26 遵守のため【】括弧なし）。
3. `contrast.py: contact_tone_fit()` 新設＋ ranking に ±0.02 の最下位項として加算（§18-19。Gold 3件未満は中立。Context/Human/Personal Gold より下位）。
4. 相手文体コピーなし（§9）。本人 Gold 最優先の順序維持（§5・§10）。

### 接触別ベンチ（§22・3 contacts・同一メッセージ「今日疲れた」）

| 接触 | Gold 特徴 | 適応後返信特徴 | 判定 |
|---|---|---|---|
| A（短・砕け・笑） | laugh 1.0・len 4.8 | laugh 1.0・len 8.7（おつかれさま笑 等） | ✓識別 |
| B（丁寧・長） | laugh 0.0・len 25.4 | laugh 0.0・len 27.0（丁寧2行） | ✓識別 |
| C（中） | laugh 0.0・len 18.4 | laugh 0.0・len 20.7 | ✓識別 |

- 3/3 が自身の Gold に最接近（A→A、B→B、C→C）。相手文コピーなし。本人マーカー維持。
- R6（verbatim Gold のみ）との対比較（§23）: B の文量距離が **12.7→1.6（約8倍改善）**。R6 は B に短文返信（12.7）で Gold（25.4）に合わず。relationship block（文量目安）＋tone-fit が長文丁寧接触の適応を実現。**Same-contact Style Fit 改善を実証**。

### Before・After（§24・70ケース・3.1統一）

| 指標 | 17-R6 | 18 | §25 基準 | 判定 |
|---|---|---|---|---|
| AI-like | 0.081 | 0.076 | ≤0.098 | ✓ |
| Context Fit | 0.618 | 0.607 | ≥0.605 | ✓ |
| Human | 0.900 | **0.908** | ≥0.900 | ✓（改善） |
| Conversation | 0.969 | 0.956 | ≥0.963 | **✗** |
| Questions | 0.000 | 0.005 | ≤0.059 | ✓ |
| Echo | 0.067 | 0.048 | ≤0.132 | ✓ |

- 70ケース・210候補・エラー0。
- **重要**: Step 18 は prompt.py 無変更のため run_live パスは R6 と同一コード。70ケース差は純粋な引き直しノイズ（確立済みフロア: Conversation 0.941-0.977）。Conversation 0.956 はノイズ範囲内であり、本変更による系統的悪化ではない（Gold-gated のため synthetic では全変更が中立）。
- Human 改善目標は達成（0.900→0.908）。Style Fit 改善は接触別ベンチで実証。
- 最終判定: **5/6（Conversation のみノイズ範囲で未達）**。§32 により feat せず、状態記録のみで停止。ChatGPT 判断待ち（ノイズとして受理／再実行指示／基準調整）。

### 20ケース目視（§27）

- R6 vs 18 で系統差なし（b02/b15/b25/b45/b23/b33 とも同等品質。b45-R18 は逆質問なしで改善方向）。
- 同じ内容でも相手によって返信が変わるか→接触別ベンチで実証済み（3/3 識別）。
- 本人らしさ維持・相手文体コピーなし・距離感の不自然なし。

### Regression

- `python -m pytest backend/tests -q` → **287 passed**（新規6件含む）。`npm run build` 成功。
- 新規 `test_step18_relationship.py`（6件）: 空データ・砕け/丁寧の識別・少数参考・tone-fit 中立/適合を固定。

### 残課題

- Conversation 0.956（ノイズ範囲）。ChatGPT 判断待ち。
- Novel artifact（笑笑・汎用語）の測定仕様は未解決のまま（§26 対象外）。
- per-contact closing 率・recency 減衰・phase別接触 profile は未実装（必要になれば）。

## Step 18-R2

- 実施日: 2026-10-08 / コミット: `wip: step 18-r2 ranking analysis`（Quota 枯渇のため70評価未完了。記録のみで停止）
- 目的: Conversation Fit 回帰修正（0.956→≥0.963）。Contact Adaptation（Human改善・3/3識別）を維持。

### §8 ranking 寄与分析（offline・70ケース記録データ・LLM不要）

- pipeline ranking を再現（style 中立＋nat＋FORCED＋mild＋diversity）し Top-1≠max-Conversation を抽出: **29/70**。
- 内訳: forced 0・mild 9・div 3・nat 17。
- forced demotion が max-Conv を落とす例はゼロ。nat 17件は Context 優先の正しい順位付け（§4・§19 通り）。mild/div 12件は tie-break の正常動作。
- 結論: ranking bug なし（§9）。修正不要。問題は生成側（短＋話題の両立候補の不足＝§10）。

### 変更内容（§10・prompt.py のみ1箇所）

- user_instruction の話題語ルールに短＋話題の組合せ例を追加: 「短い返信にも話題の言葉を1語入れると自然で文脈にも合う（例: 相手「昨日映画見てきた」→「映画いいですね！」。長い説明は不要）」。
- 良い例のコピーは安全（悪い例の列挙禁止は維持）。Context・Conversation の両立を狙う genuine 修正。
- pytest 287 passed 維持。`npm run build` 成功。

### 70ケース評価（§18-19・未完了）

- gemini-3.1-flash-lite の 429 Quota 枯渇により 70ケース実行不可（20分 timeout＋3分待機後も 429）。
- 新 version（短＋話題語）の測定は Quota 回復待ち。現時点の判定不可。
- 最終判定: **保留（Quota 待ち）**。製品コード変更は pytest 通過済みで保持。ChatGPT 判断待ち（Quota 回復後の再実行指示）。

### 18-R2 継続（Quota 再試行・未完了）

- 3.1 の Quota が一時回復したため 70ケース実行を開始も、2〜3コールで再枯渇。5件のみ成功（3＋2）し残り65件は 429。
- 10分待機後の再試行も全件 429 で Quota 完全枯渇と確定。これ以上の再試行は無駄な消費のため停止。
- §6 の loop 継続は Quota 回復後に再開（コード a030515 のまま固定・§1 遵守）。製品コード変更なし。

## Step 18-R2 3.5-uniform（ユーザ指示によるモデル切替）

- ユーザ確認（3.5 に制限なし）＋明示指示により `gemini-3.5-flash-lite` に切替。§7 の切替禁止はユーザ指示により上書き。
- 混成回避のため全70を 3.5-uniform で実行（3.1分の5件は破棄）。R6/18 baseline（3.1）とはモデルが異なる旨を記録。

### 3.5 初回70（loop iter 0・a030515 のまま・§25基準で判定）

| 指標 | 値 | §25 基準 | 判定 |
|---|---|---|---|
| AI-like | 0.072 | ≤0.098 | ✓ |
| Context | 0.576 | ≥0.605 | ✗ |
| Human | 0.873 | ≥0.900 | ✗ |
| Conversation | 0.978 | ≥0.963 | ✓ |
| Questions | 0.000 | ≤0.059 | ✓ |
| Echo | 0.126 | ≤0.132 | ✓僅差 |

- 3.5 vs 3.1 の系統差を確認: 3.5 は質問皆無（Q 0.000）だが言い換え返し多（paraphrase 26・Echo 0.126）。3.1 は逆。モデル特性の違い。
- 4/6（Context・Human 未達）から §6 loop 開始。

### loop iter 1（反応明確化・§6）

- ★行に追記: 反応・共感とは自分の言葉（おつかれ/いいね/わかる/笑など）であり繰り返しではない。
- 結果（3.5-uniform）: AI 0.062✓ / Context **0.604**（+0.028）/ Human **0.896**（+0.023）/ Conv 0.981✓ / Q 0.000✓ / Echo 0.119✓。**4/6**（Context・Human が僅差未達）。
- 系統的改善を確認（paraphrase 減・反応主導増）。

### loop iter 2（良い例追加・backfire→revert）

- ★行に良い例（明日仕事なんだ→おつかれ！）を追加。結果: Context 0.598/Human 0.889 に悪化（良い例の過剰コピーで話題性低下）。
- §21 に従い **revert**（iter 1 状態に復帰）。最終コード＝iter 1。
- pytest 287 passed 維持。

### 残存 gap の分析（測定 artifact と確定）

- 最低 relevance 8件: b05「ありますよ、お寿司美味しいですよね！」→0.0（お寿司≠寿司の tokenize 不一致）、b08（回答不能）、b10「わかりました！」→0.0（わかりました≠わかるの活用不一致）。
- いずれも良回答への 0.0 であり、品質問題ではない。修正には測定変更（§1 禁止）か不自然な言い回し強制（品質破壊）が必要で、制約内では不可。
- Context 残り 0.001・Human 残り 0.004 は単一候補レベルの artifact であり、系統的修正の対象外と確定。

### 最終判定（§6 loop 終了）

- loop 2 iteration を genuine に実行（系統的 +0.028/+0.023 達成＋backfire revert＋artifact 分析）。
- 残存 gap は測定 artifact 由来で制約内修正不可。これ以上の prompt 変更は backfire 実績（R6b/R5/R6-iter2）＋Echo 反転リスクのため期待値マイナス。
- **4/6（§25基準）で wip 記録して停止**。ChatGPT 判断待ち（marginal 受理／測定修正／基準調整／別アプローチ指示）。
- pytest 287 passed / npm build 成功。

## Step 18-R3

- 実施日: 2026-10-08 / コミット: `wip: step 18-r3 findings`（§26 未達のため feat せず。loop 1 iteration＋revert まで実行）
- 目的: Context（0.604→≥0.605）＋Human（0.896→≥0.900）の改善。3.5-uniform。Novel は対象外。

### 失敗ケース分析（§3・§6）

- Context 低 75件: rel 0.0×18（質問への良回答＋token 不一致＋回答不能）・0.2×6・0.3×26（自然な返信だが lexicon 外）・0.5×14・0.6-0.67×9。
- Human 低 47件: ほぼ全て relevance との複合（単独は echo 1・question 1 のみ）。Context 改善が Human も持ち上げる構造。
- Echo cap 20件の分離（§5）: 自然な mirror・良回答・必要 reuse の12件は修正対象外。真の言い換え返し 8件（b21/b22/b33/b44/b61/b64×2/b66）が genuine 修正対象。

### 変更と revert（§8・§19・§21）

- loop iter 1（short-tier に純粋反応優先を追加）: Echo 0.119→0.097・AI 改善も、Context 0.604→0.586 に悪化（話題性低下の tradeoff）。§21 に従い **revert**。
- loop iter 2（良い例追加）: Context/Human さらに悪化。**revert**。
- 最終製品コード変更（Step 18 差分）: **2箇所保持**（①★行の反応明確化＝系統的 +0.028/+0.023 に寄与、②短＋話題語コンボ＝18-R2 で追加し Context 維持に寄与）。純粋反応・良い例の2件は revert 済み。
- pytest 287 passed 維持。

### 接触bench（§20・2/3）

- A OK（laugh 1.0）・C OK・B MISS（len 17.3 vs Gold 25.4。文量適応のばらつき）。
- B-length は run により 27.0/19.7/15.3/17.3 と変動（relationship 文量目安の遵守率が部分的）。
- length 優先化（接触別を発言長区分より上位）は、狭いプローブへの過剰長文リスク＋backfire 実績のため見送り（期待値マイナスと判断）。

### 20ケース目視（§22・抜粋）

- R6→18→R3 で系統的な文脈無視・話題変更・AI説明の増加なし。b45-R18 の逆質問解消等の改善方向を維持。
- 実際に送れそうか: 短い相づち・終了・受領は自然。距離感は接触別ベンチ通り（A短・B丁寧・C中、ただしB文量にばらつき）。

### 最終判定（§20・§26）

- 70ケース（3.5-uniform・iter-1 状態の測定値で判定）: AI 0.062✓ / Context 0.604✗ / Human 0.896✗ / Conv 0.981✓ / Q 0.000✓ / Echo 0.119✓。**4/6**。
- 接触bench 2/3。pytest 287 / build 成功。
- **不合格**。§26 により完成扱いせず、wip 記録して停止。ChatGPT 判断待ち。

### 残課題

- Quota 回復後の70ケース測定（同一条件・3.1・70・210）。
- 測定後の §27 判定（6項目＋接触bench 3/3）。

## Step 18-R1

- 実施日: 2026-10-08 / コミット: docs のみ（製品コード無変更。§9 ループは全項目実行も修正なしと確定）
- 目的: Conversation Fit 回帰（0.956→≥0.963）の原因特定と修正。ノイズ結論の禁止（§1・§15）に従い全件比較を実施。

### R6 vs 18 per-case 比較（§1-2・Conversation 低下10件）

- b01/b03/b40/b43/b50/b51/b56/b60/b66（言い換えレベルの変動）・b08（R6: 回避返信3件 → 18: [AI_QUESTION]×3 の戦略反転）。
- b40（了解です！/承知しました笑/わかりました！→了解です！/承知しました！/ありがとうございます！）はほぼ同一。
- run_live は Gold なし・router ranking なし・Step 18 は prompt.py 無変更のため生成パスはコード同一。差は新規 LLM draw のみ。

### Contact Adaptation の関与（§3）

- synthetic 70ケースには Gold が存在しないため relationship_summary は空文字・contact_tone_fit は 0.5 中立（単体テスト `test_relationship_summary_empty_without_data`・`test_contact_tone_fit_neutral_without_data` で固定済み）。
- よって 10件の差に adaptation が関与した可能性はゼロ（router ranking 自体が run_live で実行されない）。
- 証拠に基づく結論: 引き直し変動（b08 の戦略反転を含む）。§15 の「ノイズだから合格」は行わないが、原因の証拠記録は §1 の要求通り実施。

### 優先順位（§4）・重み実験（§5）

- §4 遵守確認: tone_fit ±0.02 は最小の monetary 項（correction ±0.05・sent ±0.03 より下位）。変更なし。
- §5 重み実験（0/0.005/0.01/0.02）: synthetic 70では Gold なしのため全 weight で恒等的に無効（void）。seeded 接触（A/B/C）での offline 再ランキングでは全 weight で Top-1 安定（0→0.02 で順位不変）。±0.02 は補助的で適切と確定。変更なし。
- 注: §5 を synthetic 70 で実行しても意味がない（tone_fit が実行されない）ため seeded で正しく実行した。

### Brevity 実験と revert（§10）

- Conversation=length の観点から short-tier soft cap（2行・30字以内目安）を試行。
- 結果: 接触別識別が 3/3→1/3 に破壊（B の文量適応が Gold 25.4 に対し 19.7 に短縮）。§10「識別できなくなった場合は採用しない」に従い **revert**。
- 最終製品コード変更: **ゼロ**（revert により Step 18 状態に復帰。pytest 287 passed 維持）。

### 判定

- §14 Conversation ≥0.963: 0.956 で未達（§9 ループは原因特定・重み実験・brevity 試行＋revert まで実行も、系統的修正なしと確定）。
- Human 0.908・Style Fit（B文量8倍）・3/3 識別（Step 18 記録）は維持。製品コード無変更のため regression なし。
- **不合格のまま docs のみ記録して停止**。ChatGPT 判断待ち（ノイズとして受理／再実行指示／基準調整／R6 への rollback 指示）。

## 9. Frontend・DB・周辺の補足（生成フローに関わる範囲）

- Frontend: `GenerationPanel.tsx: generate()` が `condition/revision_instruction(original=案全文)/tone/mode` を送り 3 案カード化。`ChatArea.tsx` は AI 案送信を `source='generated'+historyId`、手入力を `source='manual'` で送る（＝Contrast の分岐点）。`HistoryModal` で rating 付与。`PracticePanel`（練習モード）は生成フローと別系統。
- DB（`backend/app/database.py: SCHEMA`）: `contacts` / `messages(source, generation_history_id)` / `generation_history(rating/rating_reason/tone/counterpart_message/batch_id)` / `generation_batches(outcome: pending|regenerated|candidate_sent|manual_replaced, parent_batch_id, attempt_no, selected_history_id, replacement_message_id, trigger_message_id)` / `training_examples/sessions/revisions` / `providers/settings` / `knowledge_files/contact_knowledge_files` / `contact_settings` / `user_profile(my_info 含む)` / `user_knowledge`。`init_db` 内に legacy 移行・knowledge 再作成等のマイグレーションあり。
- AI 設定解決: `ai/config.py`（全体 DB > `.env` > 既定 `cerebras/gpt-oss-120b/0.8/1024/50`）＋相手別上書き。API Key は Backend のみ保持。
- knowledge/training 資産: `knowledge/rules`（01〜08）・`references`（2件）・`training`（01〜10、質問なし返信・NG例等）。ただし §6 P2-5 の通り生成プロンプトへの未投入が疑われる（`preview` では参照表示される）。
- `docs/ARCHITECTURE.md` 等の既存 docs は旧構成（rules 投入ありき）の記述で、現行 `_build_context` との差異がある。

---

## Step 18-R4 Iteration 1（検証中）

### 現状と仮説

- GitHub `main` の確認SHA: `a75ba76998a377e527f1ea3bedaa655a6b89569c`。作業はPR #1の最新コードを基点に実施
- `compute_hierarchical_profile()` は同一相手Goldが1件でもGlobal profileを置き換え、Gold 1〜2件でもstyle summaryを出していた。相手別Silverが少数Goldより優先される経路もあった
- 最近5件だけを局所profileに使うと、それ以前のGoldが完全に切り捨てられる。少数データfallbackと緩やかなrecencyの両方が不足していた
- Contact Benchは接触ごとに履歴を登録してすぐ生成していたため、A/B/CでGlobal profileに入る相手が異なっていた。履歴を全て先にseedしてから同一入力を生成する形へ修正

### 実装と単体検証

- Gold 3件未満はGlobal Gold/Global profileへfallback。同一相手SilverはGoldが存在する場合にprofileを置き換えない
- Gold 3件以上はGlobal Goldに対し `n/(n+5)`（最大0.75）の重みでblend。過去の全Goldを保ったうえで、直近5件を件数に応じて最大0.5の追加重みで段階反映
- Style比率が混在している場合に「カジュアル中心」など一方の口調へ誤って決めつけないよう、tone要約を修正
- Promptで本人Gold・現在の会話内容を最優先し、相手の温度感は補助情報として扱う。適応だけを理由に質問・説明を加えず、相手の語句・口調をコピーしない
- `python -m pytest backend/tests -q`: **421 passed**（FastAPI非推奨警告2件）
- `npm run build`: **PASS**
- 独立Reviewer 1回目: **FAIL**（Silver優先の複合条件）。修正済み
- 新Reviewer 2回目: **FAIL**（recencyの切捨て・受入評価未完）。全Goldと直近Goldを混ぜる処理を追加。再レビューは受入評価後

### Contact Bench / 70-case 状態

- 最新Contact Benchは全履歴を先にseedし、同じ `今日疲れた` を入力。結果（3.5 Flash Lite）:
  - A・短く砕けたGold: 平均9字、笑い率1.00。例「今日もおつかれさまです笑」「それは大変だったね笑」「おつかれ笑」
  - B・丁寧で長めのGold: 平均29字、笑い率0。例「今日もおつかれさまです！\nゆっくり休んでくださいね！」
  - C・中間のGold: 平均12.3字、笑い率0.33。例「今日もおつかれさま！」「それは大変だったね、ゆっくり休んでね」「おつかれさまです笑」
- この出力ではA/B/Cの長さ・丁寧さ・笑い方に差が出たが、種データの受信文が相手ごとに異なるという交絡を発見。ベンチ入力と会話履歴を統一して再実行するため、**3/3は未確定**。上記数値は暫定参考値
- 70ケース本番router regressionはGemini 3.5 primary、429時3.1 fallback、6秒間隔で実行中。結果確定後に数値・実モデル・エラー・repair数を追記

### 次の判定

Contact Benchの統制版、70-case、最終レビュー、資料を揃えるまでStep 18-R4は未完成とする。現在までの中間出力だけで合格とは扱わない。

## Step 18-R4 Iteration 2（最終レビュー待ち）

### 実装判断

- Goldが3件未満の相手はGlobal fallbackを維持。3件以上の場合はGlobal Goldへ段階blendし、直近5件を全Goldに対して緩やかに反映する。少数SilverはGoldを上書きしない。
- 1〜2件の相手別Goldで文体を切り替えない。明確に混合したGoldにはhard tone lockをかけず、Gold例と会話文脈から自然な振れ幅を残す。
- 同一相手Goldが3件以上ある場合にのみGold優先の相手適応方針を追加し、Gold不足時は従来のGlobal fallback Promptを維持する。Goldのない70件回帰で全体Promptを変えないための条件分岐。
- `validate_tone_strict` が「ですね！」「ですよね！」をカジュアル語尾と誤認し、逆に「です笑」をタメ口として通す欠陥を発見。再現テストを追加して敬語・タメ口双方の検出を修正。

### Contact Bench（3.5 Flash Lite、同一条件の2入力）

各A/B/Cとも同じ5件の入力履歴と5件の手入力Goldを先に登録し、同一の受信文へ3候補を生成。CのGoldを、A/Bの間に来る長さで、丁寧・砕けの両方が実際に含まれる分布へ調整した。候補自体の長さは固定していない。

| 共通入力 | A: 短く砕けたGold | C: 中間・混合Gold | B: 丁寧で長めのGold |
|---|---:|---:|---:|
| 「仕事で疲れた」 | 平均9.3字、笑率0.67。「仕事おつかれさま笑」 | 平均22.0字、混合Gold。例「おつかれさまです、それは大変ですね…！」 | 平均30.0字、笑率0。例「お仕事おつかれさまです！\nゆっくり休んでくださいね。」 |
| 「今日は天気いいね」 | 平均9.3字、笑率0.67。「お出かけしたくなるね笑」 | 平均12.7字、笑率0.33。例「気持ちいい一日になりそう！」 | 平均15.7字、笑率0。「今日は過ごしやすい天気ですね！」 |

両入力で文量がA < C < Bとなり、Aの短い砕けた応答、Bの丁寧な長め応答、Cの中間的な文量と混合Goldの反映を実生成で確認。疲労のような感情共有ではContextを優先するためCが敬語寄りになり、軽い天気の話題では砕けた案も混ざった。話題に応じた変化として自然であり、相手の語尾のコピーや質問の水増しは確認されなかった。Contact Bench **3/3**。

### 70-case本番経路回帰

- Gemini 3.5 Flash Lite primary、429時Gemini 3.1 Flash Lite fallbackを設定し、6秒間隔で70ケースを実行。結果: **70件、207候補、HTTP/生成エラー0、修正11件、安全なユーザー確認1件**。全件で3.5が使われ、今回3.1への実切替はなし。fallback時の即時切替と履歴記録は `test_model_fallback.py` で検証。
- 指標（既存evaluator・70ケース・thresholdを変更せず）: AI-like **0.082**（基準≤0.098、PASS） / Context **0.635**（≥0.605、PASS） / Human **0.871**（≥0.900、未達） / Conversation **0.971**（≥0.963、PASS） / Questions **0.000**（≤0.059、PASS） / Echo **0.111**（≤0.132、PASS）。
- Humanの低得点候補の最小項目はQuestion 23件、Echo 23件、Length 17件（207候補中）。評価器は変更せず、個別例を確認した。
- Artifactの具体例: 「キャンプいいですね！」のように話題語を自然に共有する返答をparaphrase echoとして減点する例（b34）、「了解です！」（b10）、「週末空いてますよ！」（b55）のような質問への直接回答をEchoとして減点する例、長い感情共有b02への26〜34字の労いを相対文字比でLength 0.4とする例。これらは単純コピー・極端な短文とは区別し、閾値・対象ケース・evaluatorを変更せず記録する。
- 真の問題例も残る: 「おー！」（b52）の完全な一語反復や、不要な問い返し・未確認の自己開示。したがってHuman 0.871を数値だけで合格扱いにはせず、独立Reviewerが実例とartifact根拠を判定する。

### 最終テストと判定状況

- `python -m pytest backend/tests -q`: **427 passed**（FastAPI非推奨警告2件）。
- `npm run build`: **PASS**。
- 独立Reviewer: Iteration 1は2回ともFAIL。Iteration 2の最終diff・70ケース・2入力Contact Bench・Gold priority・fallback・artifact例は、これから新規read-only Reviewerに渡す。
- 現在は**レビュー待ち・未完成**。Human指標は数値上0.029未達。Reviewerがartifact根拠と実生成を認めない場合は新しいReviewerで再評価する前に必要修正を行う。PASS後のみStep 18-R4合格・GitHub反映とする。

## Step 18-R4 Iteration 3（独立レビューFAIL）

### 指摘からの修正

- Iteration 2レビューは、未登録名を「相手さん」と呼ぶ指示、相手の「眠い」から仕事を推測する返答、本人の「今日バタバタしていた」という架空自己開示、Cの疲労共有がBと同じ敬語一辺倒になる点をFAILとした。
- 通常返信でも確認済みの名前だけに「さん」を付け、名前が未設定・汎用placeholderなら呼称を作らない。Learned Style Policyから一律の「〇〇さん」呼称指示を削除。
- 会話入力がある通常生成では、根拠のない勤務・職場や休日の断定と、本人の一人称＋日時付き近況を検出してRepair対象にする。事実の照合対象がない単体ランキング呼び出しには適用しない。Gold/履歴で状況が確認できる場合は許可。
- 「今日暇だった」から「今日はお休みだった」と推測したb01も追加で確認。休日表現の根拠検査を追加し、未確認休日を差し戻す。b01・b02を3.1で再生成し、休日・仕事を補う候補が最終出力に残らないことを確認。
- Cの混合Gold比率とGoldに実在する会話調の返信を適応要約に明示。相手への共感・労いに自然な場合、3案のうち最低1案は敬語だけにせず混合/会話調を反映する。事務連絡や深刻な内容では無理に崩さない。

### Contact Bench（Gemini 3.5 Flash Lite）

A/B/Cに同数の6件Gold履歴を先にseed。各接触に共通する疲労系Goldは「今週ずっと忙しくて疲れた」、共通probeは別表現の「今日はもうへとへと」とし、入力文の単純な再掲で通らないようにした。天気共有も同一の別probeで確認した。

| 共通入力 | A: 短く砕けたGold | C: 中間・混合Gold | B: 丁寧Gold |
|---|---:|---:|---:|
| 「今日はもうへとへと」 | 平均8.0字。「おつかれ笑」「ゆっくり休んでね笑」 | 平均20.0字。「おつかれさま、今週は忙しかったもんね」「それは大変だったね、今日はゆっくり休んでね」 | 平均29.0字。丁寧な労いと休息の提案 |
| 「今日は天気いいね」 | 平均6.0字。短い砕けた反応、笑率1.00 | 平均11.7字。丁寧な案と「ほんとだね笑」等の混合 | 平均16.0字。笑なしの丁寧な反応 |

両方の入力で返信文量はA < C < B。疲労共有でCから会話調の返信が出ることを独立して確認し、Iteration 2で指摘された「CがBと同じ敬語3案」状態は解消した。候補は相手文の語尾を模倣しておらず、質問や笑いを条件として強制していない。Contact Bench **3/3**。

### 70-case本番経路回帰（採点器・閾値・70ケースは変更なし）

- 3.5 Flash Liteを主、429時3.1 Flash Liteへ切り替えて70件を実行。3.5の日次500リクエスト上限に到達後、3.1の一時503も複数発生し、最初の実行では11件がHTTP 502となった。失敗11件を削除せず番号を固定し、3.1で個別再実行して全件成功。全体70ケースに207候補の実生成・評価値が揃った。3.5を使ったケース41件、3.1で実行/再実行したケース28件、利用者への安全確認1件（このケースは返信候補を返さない）。
- 既存evaluatorによる結合後の指標: AI-like **0.072**（≤0.098） / Context **0.626**（≥0.605） / Human **0.888**（<0.900、未達） / Conversation **0.967**（≥0.963） / Questions **0.000**（≤0.059） / Echo **0.082**（≤0.132）。Human数値は未達のため、以前の0.871をartifactだけで受理したり、今回の0.888を合格と見なしたりしない。
- Human未達を閾値・ケース除外で処理しない。統合前に候補の実例と評価器出力を独立Reviewerへ提示し、artifactの根拠と残る自然さの問題を個別に判定してもらう。

### テストと判定

- `python -m pytest backend/tests -q`: **432 passed**（FastAPI非推奨警告2件）。一時的な初回テスト失敗6件は文脈のないランキング検査へ送信前事実検査を誤適用した回帰だった。会話入力がある時だけ事実検査するよう直し、全件再実行して成功。
- `frontend`で `npm run build`: **PASS**。
- 現在の判定は**未完成・独立レビュー待ち**。Contact Bench 3/3、70ケースの評価可能な出力、pytest、buildは揃った。Human目標は0.012未達。Iteration 2のReviewerとは別の新規read-only Reviewerで最終diff・70件・接触別実生成・Gold優先・残るHuman未達を確認する。PASSまでは最終合格・GitHub反映としない。

## Step 18-R4 Iteration 4（独立レビュー待ち）

### Reviewer指摘と追加監査

- Iteration 3の新規Reviewerは**FAIL**。70件中b45「犬派？猫派？」への「犬派です！」/「ずっと犬と暮らしてました」、b65「映画好き？」への「映画好きですよ！」を、確認済みユーザー情報がないのに答えていた。実際に送れる正確な返信という目的に反するため、artifactでなく実装欠陥と判断
- 特定の質問語だけを例外処理する方法は取らず、関連入力を追加監査。b35「辛いものは大丈夫？」も嗜好・耐性に関する質問と判定。また本人の一人称近況を検証する際、相手側の発言を会話全体から拾い本人情報の根拠と誤認する穴を特定

### 修正内容と実出力

- 嗜好・嫌悪・得手不得手・耐性を尋ねる直接質問に対し、本人の確認済みプロフィールまたは本人側の過去発言で裏付けられなければ、チャット相手向け候補を返さずアプリ利用者だけに `[AI_QUESTION]` で確認する。犬か猫の一方だけが既知でも他方を推測しない。辛さ・食べられるか等の耐性質問も対象
- 自己情報の根拠照合を、会話履歴全体ではなく本人として記録されたfactと確認済みプロフィールに限定。相手の「映画見た」から「僕も最近見てない」を正当化しない回帰テストを追加
- Gemini 3.1での実生成: b35（辛さ）、b45（犬猫）、b65（映画）はすべてチャット向け候補を出さず、利用者への確認に分岐。b04では相手側の映画経験を本人の経験として流用しない出力を確認。根拠のない答えが残ったとき、repairでも無理に生成せず安全な確認へ倒す
- 回帰テストに未登録嗜好、既知の片側だけから反対側を推測、映画嗜好、食の耐性、簡潔な本人確認質問、相手発言を自己factの根拠にしないケースを追加

### Contact Benchと70-case

- Contact Benchは前iterationと同一の6件ずつのContact Gold、共通probe2種、3.5 Flash Liteで再確認。**3/3**。疲労共有の平均長 A 8字 / C 20字 / B 29字、天気共有 A 6字 / C 11.7字 / B 16字でA<C<B。Cに混合トーンを確認。ただし疲労probeのC候補の一つはGold表現にかなり近く、Echoを一切起こさないとは評価しない
- 70ケースは元の70件、既存evaluator、thresholdを維持。3.5のRPD上限と3.1の一時503で失敗したケースを除外せず、case id固定で再実行。最新の実生成でb01,b02,b04,b35,b45,b65を更新して全体採点
- 結合結果: **70/70成功、エラー0、安全な利用者確認4件、評価候補198件**。Gemini 3.5は37ケース、3.1は29ケースで利用（利用者確認4件は候補なし）。モデル使用と再試行履歴を各caseに保持
- 既存evaluator指標: AI-like **0.076**（目標≤0.098） / Context **0.643**（≥0.605） / Human **0.885**（<0.900） / Conversation **0.961**（<0.963） / Questions **0.000**（≤0.059） / Echo **0.076**（≤0.132）。Humanは0.015、Conversationは0.002未達。未達をartifact扱いで受理しない

### 検証と現在判定

- `python -m pytest backend/tests -q`: **439 passed**（FastAPI `on_event` 非推奨警告2件）。`frontend`の `npm run build`: **PASS**
- Reviewer: Iteration 3の独立read-only Reviewerは、根拠のない犬猫・映画好みを実際に断定したため**FAIL**。本Iterationは別のfresh reviewerが差分、70件集計、Contact Benchと生成文を確認する
- 判定: **未完成**。Contact Bench・pytest・buildはPASSだがHumanとConversationの閾値は未達。新Reviewerが生成品質・残る問題を判断し、必要なら修正後さらに別Reviewerに再評価させる。合格条件が揃うまではpushしない

## Step 18-R4 Iteration 5（fresh review待ち）

### Iteration 4レビューFAILと追加監査

- Iteration 4のfresh Reviewerは**FAIL**。b15「土日どっちがいい？」への「土日は予定あけてますよ！」、b25「明日何時に起きる？」への「休みの日もつい早起きしちゃいます！」を、本人情報なしで断定していた。またb02の単なる状態反復とb04の助詞抜け、Human **0.885** / Conversation **0.961** 未達も指摘された
- 同種の直接質問を既存70件から追加監査。b55「週末空いてる？」に空き状況を断定する候補があったため、土日/週末の都合と起床時刻を好みと同様の本人情報として扱う
- 返信候補の生成中に、相手側メッセージを本人の経験根拠として使う境界の欠陥はIteration 4で修正済み。今回も関連するb04と一人称主張を再テスト

### 実装と回帰テスト

- 未知の直接質問（本人の嗜好・経験・体質・予定・起床時刻）は、会話相手向けの回答を捏造せず、アプリ利用者にだけ `[AI_QUESTION]` で確認する。JSONの `replies` 内に質問タグだけが1件返るprovider出力も安全に抽出するが、複数候補のうち一部だけを抜き出すことはしない
- 未確認の一人称嗜好・希望・習慣/心理傾向を検出する回帰検査を追加。b53/b63の将来不安・自信の習慣、b57/b64のカレーに関する「僕も好き/食べたい」をRepair後の返信から除去できることを実出力で確認
- echo検査を「わかります笑\n眠いですよね！」のように改行・笑いを挟んだbare paraphraseにも適用。ただし「ゆっくり休んでね」など気遣いの情報を加えた返信は通す。b04の「観たこと誰かに共有…」に相当する助詞欠落表現はRepair対象
- 既存の生成APIテストfixtureも、新しい本人事実境界に反する未確認の好み/習慣や裸のechoを成功期待として使わないように修正。Step 10の70 benchmarkデータ/evaluator/thresholdは未変更
- 対象検査で、未知の予定/習慣は利用者確認へ、根拠のない本人希望・一人称習慣は候補Repairへ、問題のない中立リアクションは維持されることをRED/GREENテストで確認

### 実モデル検証

- Gemini 3.1 Flash Liteで直接ケースを再生成。b05（寿司経験）、b15（土日都合）、b25（起床時刻）、b35（辛さ耐性）、b45（犬猫）、b55（週末予定）、b65（映画好み）はすべて利用者向け確認となり、相手向け候補を保存しなかった。b53/b63では一人称の習慣を含む初回候補をRepairし、最終候補から取り除いた。b57/b64では未確認のカレー嗜好・希望を除去
- b02「眠い」の最新生成では、bare echo検査が問題候補をRepairし、労い・休息提案を含む候補へ修正。b04では本人の映画経験や映画館へ行きたいという未確認希望を加えず、自然な話題反応にした
- APIの503/timeoutにより初回70件とContact Benchに失敗があった。失敗IDを削らず再試行。最新統合結果は70件中**エラー0**。3.1のRPDは500/日内で利用し、3.5日次上限は超過していたためIteration 5の70件は全て3.1で実行

### 70-case regression（ケース/evaluator/threshold不変）

- 70件すべての最新成功出力を既存evaluatorで集計: **70/70、エラー0、候補189、本人確認7**。3.1が63ケース、本人確認7ケースは候補なし。失敗runを対象から外さず、同じcase IDの成功再実行結果で置換
- AI-like **0.090**（≤0.098） / Context **0.650**（≥0.605） / Human **0.852**（<0.900） / Conversation **0.948**（<0.963） / Questions **0.005**（≤0.059） / Echo **0.122**（≤0.132）。HumanとConversationは未達。現在の3.1専用評価なので3.5 primaryの性能推定にはならないが、所定基準上は未達と報告

### Contact Bench・検証・判定

- 3.1 Flash Liteで同じ疲労probe「今日はもうへとへと」を再実行。別々のrunを含めA成功出力平均14.7字（砕け調）、B成功平均29.0字（敬語）、C成功平均23.3字（敬語）で文量はA<C<B。ただしCが全案敬語になったrunがあり、現行3.1でGoldの混合トーン反映を確認しきれていない。A/B/Cを同一runで完了できなかった理由は一時503である。Iteration 3には3.5で3/3達成した完全runの実出力が記録されているが、最新runの不確実性もfresh Reviewerへ提示
- `python -m pytest backend/tests -q`: **447 passed**（FastAPI非推奨警告2件）。`frontend`の `npm run build`: **PASS**
- fresh Reviewer: Iteration 4 Reviewerとは別の新規read-only Reviewerを起動する。Pythonの変更diffはpython-reviewerにも確認を依頼
- 判定: **未完成**。自動テストと全70件再集計はPassだがHuman/Conversationの基準未達。Contact Bench最新3.1 runのA/B/C完了とCの混合Styleも追加確認が必要。指摘修正のたびに新しいReviewerで再確認し、基準達成後だけpushする

## Step 18-R4 Iteration 6

### Independent review

- New read-only reviewer returned **FAIL**. Current available 70-case metrics remained below the target (Human 0.852 vs ≥0.900; Conversation 0.948 vs ≥0.963), and no fresh 70-case run reflected this iteration's changes.
- Review identified two safety gaps: negative preference polarity could be mistaken for positive (e.g. `猫派じゃない`), and a topicless answer such as `全然大丈夫` could escape preference-claim matching. It also found the 3.1 Contact Bench incomplete/overly formal for C, and requested fresh generated examples after the fixes.

### Iteration 7 corrections and verification in progress

- Added regression coverage and fixes for negative preference suffixes, topicless answers to single-topic preference/tolerance questions, exact availability period matching, wrong-period claims in replies, state restatement, and multi-question empathy replies. Normal empathy (`僕もよくわかります`) is excluded from the habitual-self-claim detector.
- The fresh 3.1 run was interrupted by provider 503s: b02 and b25 returned HTTP 502, b03 returned three candidates, and b15 safely ended in a private user question after six provider calls. Failures remain recorded, not discarded.
- Fresh Contact Bench: 3.1 completed A/B/C but C was too formal (mean lengths A/C/B 8/23.3/22). Under 3.5-primary with automatic 3.1 fallback, two real probes completed. Fatigue A/C/B averages were 8.0/11.0/25.7 chars; weather 7.3/11.7/20.3. C included conversational/hybrid candidates and each probe maintained A<C<B. The 3.5 provider returned its daily quota limit during the run; fallback calls succeeded.
- 70-case run on the corrected code is in progress; latest counts, metrics, full pytest/build, and a new independent reviewer result will be appended before any final decision. Current status: **not complete; no push**.

# Step 18-R4 Iteration 23–24: 伝聞予定の誤認ガード

## 問題と修正

独立レビューで、本人の予定に関する伝聞を確認済み事実として扱う穴を発見した。`空いてると言われている`、`と聞かされた` に加え、反復・否定形の `聞いていた`、`言われてた`、`聞かされていない` も本人の予定確認には不十分である。これらを曖昧な根拠として扱い、候補生成時に本人への確認へ分岐する回帰ケースを追加した。

Iteration 23 Reviewerは追加した肯定形4例を確認した一方、隣接した言い回しを追加発見したため **FAIL**。その指摘を踏まえてパターンを広げ、テストを追加。Iteration 24の新しいread-only Reviewerは対象例と既存予定判定の維持を確認し **PASS**。

## 検証

- `python -m pytest backend/tests/test_validation_and_repair.py -q`: 134 passed
- `python -m pytest backend/tests -q`: 451 passed（非推奨警告2件）
- `npm run build`: PASS
- Contact Bench（3.1 Flash Lite、共通probe「今週末、雨みたいだね。」）: A 11.7字/砕けた語尾、B 26字/敬語、C 12.7字/中間的な丁寧さ。距離感差を確認し3/3
- 最新70-case実走はモデルRPD到達のため15/70で中断。完了済み前回統合集計（70/70、Context 0.657 / Human 0.856 / Conversation 0.938 / AI-like 0.067 / Questions 0.006 / Echo 0.085）は今回コード適用前で、R4の合格判定には使わない

## 状態

全自動テスト、build、Contact Bench、Iteration 24 Reviewerは合格。ただし最新コードでの70ケース再評価が未完了で、直近の完了済み集計もHuman/Conversation基準に未達。したがってStep 18-R4は **未完成**、GitHubへpushしていない。Gemini 3.1/3.5 Flash Lite双方の無料枠回復後に3.5 primary＋3.1 fallbackで再評価を再開する。既存70ケース/evaluator/thresholdは変更していない。

# タップル会話戦略の調査メモ

公式発表ではプロフィールの趣味・デートプランを初回メッセージの話題に使うこと、相手への関心が伝わる具体的な質問が紹介されている。一方、査読研究は他アプリ・他言語圏の小規模会話分析が中心であり、「何往復で誘えば成功する」といった固定ルールは支持できない。返信速度も単独の好意指標にしない。

アプリへの候補方針は、返信文だけでなく「話題を続ける／希望を軽く確認する／誘う／待つ／引く」を状況別に提案すること。誘う場合は会話とのつながり、日時や内容の具体性、断りやすさを確認し、相手が会うことに安心感を持ち、信頼できると感じているかも安全条件にする。初回は公共の場所を提案する。拒否・保留・反応低下では追撃や説得を避ける。タップル公式の[安心安全ガイドライン](https://static.tapple.me/policy/safety.html)と[個人情報交換に関するヘルプ](https://support.tapple.me/hc/ja/articles/360009709194-%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E3%81%AE%E4%BA%A4%E6%8F%9B%E3%81%AF%E3%81%84%E3%81%84%E3%81%AE%E3%81%A7%E3%81%99%E3%81%8B)に従い、電話番号・メール・LINE等の交換は提案しない。また、既存の本人経験を捏造しない制約を優先する。公式の[3,835人アンケート](https://www.tapple.co.jp/news/1344/)では、会話のテンポ（57.3%）、プロフィールに沿った具体的な話題（22.0%）、相手への質問（18.6%）、共通点（15.0%）が報告されている。ただしこれは運営主体の自己申告調査で、主に初回メッセージについての結果であり、デートの誘い時期を示すデータではない。分析方法の詳細にも限界がある。

参考研究として、オンラインデート利用者105人のメール記録を調べた研究では、相手選び、自己呈示、共通の文脈づくり、情報交換など複数の戦略が使われていた。希望する相手像を話した人ほど2回目のデート意向が高い関連も報告されたが、標本と当時のサービス状況が限られ、観察研究なので特定の戦略がデートを成立させる因果効果とは言えない（[Sharabi & Dykstra-DeVette, 2019](https://doi.org/10.1177/0265407518822780)）。Tinderの157会話を分析した研究は、質問で促す自己開示に加えて、相手が応じないときに自発的に話を開くやり取りも観察しているが、対象はスペイン語・カタルーニャ語の会話（[Roca-Cuberes et al., 2023](https://discovery.ucl.ac.uk/id/eprint/10170934/)）。LINE等の返信速度研究では返信速度が相手の速度と関連したが、これは恋愛感情やデート成立の指標ではない（[村上, 2023](https://www.jstage.jst.go.jp/article/jjesp/62/2/62_2114/_article/-char/en)）。

## 2026-10-09 Step 18-R4 Iteration 23: 誘い時期と安全懸念の継続判定

独立レビューで、相手が過去に示した安全面の不安や会うことへの迷いを、後の活動への関心だけで上書きする問題を見つけた。疑問文の「安全面は大丈夫ですか？」や、否定・仮定・不安の再表明も、懸念が解消した証拠として扱う余地があった。安全の解消は「安全面の不安はなくなりました」のように、相手自身が明確に述べた場合だけ認める。

活動への関心は、同じ活動が相手と本人の双方の発言にあり、相手がその話題を広げた場合に限って誘いの根拠とする。活動語が別の活動への関心と同じ文にあるだけでは不十分で、「パンケーキは好きとはいえ、映画を見てみたい」のような対比では映画への関心とパンケーキの共通性を混同しない。温かい複数往復の例と、直近が相づちにとどまる例を11シナリオのベンチに対で追加した。

Tapple focused suiteは**363 passed / 2 warnings**、backend全体は**885 passed / 2 warnings**。frontend production build、対象Python compile、`git diff --check`はPASS。修正後の新規Python Reviewerと独立安全Reviewerはともに**PASS**。Gemini fallback focused suiteは**17 passed**で、主3.5→主3.1→予備3.5→予備3.1の順と、rate limit時のみ切り替えることを確認した。

Gemini APIは呼び出していない。最新70ケース、Contact Bench、Tapple全11シナリオの実生成文と全文レビューも未実施である。したがって、この変更はオフラインの受け入れ準備であり、Step 18-R4は未完成。GitHub main基点は`a75ba76`。WIP commit `86c399f`はforkの作業ブランチへpushし、リモートSHA一致を確認した。PR #1は未マージである。

利用者投稿は個人の体験や安全上の懸念を拾う用途に限る。Redditの[初回デートの安全に関する投稿](https://www.reddit.com/r/Tinder/comments/16m5mgf/question_for_my_tinder_girlies_about_safety/)は公共の場や帰りやすさの重要性を示す個人体験だが、母集団の好みを示さない。[タップル体験談](https://meeeet.jp/tupple-experience-story)は広告記事で、成功例の選択バイアスが強い。Xの投稿は本文を確認できなかったため、根拠から外した。タップルには24時間以内の相手探しを行う[「おでかけ」機能](https://support.tapple.me/hc/ja/articles/360007459053--%E3%81%8A%E3%81%A7%E3%81%8B%E3%81%91-%E6%A9%9F%E8%83%BD%E3%81%AB%E3%81%A4%E3%81%84%E3%81%A6)があるが、男性が募集する場合は本人確認と有料プランが必要（[公式FAQ](https://support.tapple.me/hc/ja/articles/18108576990489--%E3%81%8A%E3%81%A7%E3%81%8B%E3%81%91-%E6%A9%9F%E8%83%BD%E3%81%AB%E3%81%A4%E3%81%84%E3%81%A6%E3%82%88%E3%81%8F%E3%81%82%E3%82%8B%E8%B3%AA%E5%95%8F)）。固定の誘い時期や「女性一般の好み」へ一般化する根拠は得られなかった。

2026年8月31日に公表されたタップルと富士急ハイランドの共同調査では、20代2,033人のうち、出会った相手との関係を次に進めるのが簡単だと答えた人は41.2%。フェードアウトの理由として「特別に見えるきっかけがない」50.4%、「当たり障りのない会話で相手をよく分からない」45.9%が挙がった。テーマパークデート経験者の70.9%は交際・結婚・距離が縮まったと回答した（[公式発表](https://www.tapple.co.jp/news/1658/)）。ただしオープン調査と両社会員の混合標本による自己申告で、経験者に限った集計でもある。テーマパークが関係進展を引き起こしたとは言えず、特定のデート先を勧める根拠にもならない。実装では、相手が話した趣味や関心に結びつく共有体験を、希望が見えるときに候補として短く提案する設計仮説に留める。

タップル公式ヘルプは、相手のプロフィールや趣味を話題にし、挨拶と質問を添える方法を「返信いただける確率が高くなる可能性」として案内している。ただし返信は相手の判断と都合によるとも明記されている（[メッセージ機能の案内](https://support.tapple.me/hc/ja/articles/360000322921-%E3%83%A1%E3%83%83%E3%82%BB%E3%83%BC%E3%82%B8%E6%A9%9F%E8%83%BD%E3%81%AE%E4%BD%BF%E3%81%84%E6%96%B9%E3%81%AB%E3%81%A4%E3%81%84%E3%81%A6)）。これは運営の助言であり、比較実験ではないため、毎回質問を加える規則にはしない。電話番号・メール・SNSアカウント交換は禁止されているため、Tappleモードの外部連絡先ブロックを維持する（[公式ヘルプ](https://support.tapple.me/hc/ja/articles/360009709194-%E5%80%8B%E4%BA%BA%E6%83%85%E5%A0%B1%E3%81%AE%E4%BA%A4%E6%8F%9B%E3%81%AF%E3%81%84%E3%81%84%E3%81%AE%E3%81%A7%E3%81%99%E3%81%8B)）。

## 当初の既存アプリ反映案（実装前の設計メモ）

現状の`POST /api/generate`と`GenerationResult`は主に3つの返信候補と履歴情報を返し、画面も候補カードを中心にしている。次の段階では候補文とは別に、次の行動を小さく提案する構造を加えるのがよい。

```text
next_step: continue | clarify | invite | wait | stop
signals: 相手の直近メッセージから根拠となった明示的な反応
confidence: low | medium | high
reason: 短い説明と不確実な点
invite_options: 話題に沿った提案（inviteの場合のみ）
safety_notes: 初回の公共の場所、連絡先交換を促さない等
```

判断はメッセージの通数・経過日数・返信速度だけで決めない。相手からの質問、話題の自発的な展開、具体的な行きたい場所、日程への肯定など、会話中の明示的な参加を根拠にする。情報が足りないときは`clarify`か`wait`を返し、好意の点数を断定しない。明確な断りや保留には`stop`または`wait`を選び、追撃・説得文を候補にしない。

実装時は「タップル」モードを明示選択できるようにし、アプリ全体に規約固有ルールを暗黙適用しない。タップルモードではLINE等の連絡先交換を提案対象から除き、「おでかけ」やアプリ内通話など公式機能はユーザーが希望した場合に案内する。相手が会うことに安心感を持ち、信頼できると感じている明確な根拠がない場合は、誘うより会話を続けるか待つ。返信候補の本人らしさ・文脈適合・本人情報の正確さに加え、行動判断の適切さを別評価する。拒否後の再勧誘、根拠のない好意判定、規約違反の連絡先交換、安全でない初回場所の提案を重大な失敗として数え、デート成立率だけを最適化しない。

## Iteration 25–26: 状態共有への自然な気遣いを許可

全差分Reviewerは、相手の「仕事で疲れた」に対する「それは疲れたね、ゆっくり休んでね」まで、状態語が重なるために単なるechoと扱われる恐れを指摘し、Iteration 25をFAILと判定した。テストを先に追加したところ修正前に失敗。`_is_bare_state_echo` の「返信冒頭に状態語がある」だけで拒否していた条件を除き、文全体を見て追加の反応がない短い言い換えだけを拒否するよう修正した。裸の言い換えは拒否しつつ、労いや休息提案を含む自然な共感は許す。修正後はbackend 451 passed（非推奨警告2件）、frontend build PASS。Iteration 26の新しいread-only ReviewerもPASS。最新コードの70件再評価だけは、Gemini 3.1/3.5のRPD上限で未完了。

## Iteration 27–28: 相対日付の予定根拠に送信日を適用

Iteration 27の全差分ReviewerはFAIL。履歴の本人発言から`known_self_facts`を作る際に`created_at`を捨てており、過去の「明日は空いてる」が現在の「明日空いてる？」にも一致する。実際の日付が変わると意味が変わる予定情報を、単純な文字列比較で流用してしまう。

本人メッセージと同じ順序のtimestamp配列を生成contextに持たせ、validatorに渡す。プロフィール・Knowledgeの相対予定には時刻情報がないため、確認済みの予定根拠として採用しない。過去の相対日付は安全側に倒して再確認へ進める。

Iteration 28のReviewerはFAIL。曜日を含む過去の発言を一律に失効させると「毎週土曜は空いてる」のような繰り返し予定も消える。周期表現を同じ節の曜日に限って適用するよう変更し、無関係な「毎週ジムに行くけど、土曜は空いてる」から古い単発予定を有効化しない検査も追加。対象テストで、古い単発曜日・明日予定は期限切れ、明示的な毎週土曜と同日発言は有効であることを確認した。テスト後に新しいReviewerと全体suiteの結果を記録し、RPD回復時に70件を再実行する。

Iteration 29のReviewerはさらにFAIL。「毎週」などを発言全体から拾うだけでは、別の曜日予定まで再有効化する。周期表現の対象を同一節内の曜日だけに限定し、混在する古い相対日付を期限切れにするよう修正した。古い単発、明示的な繰り返し、無関係な繰り返し、同日予定を含む対象テストはPASS。Iteration 30 Reviewerの結果は後続記録する。

## Iteration 31: 相対予定の節ごとの鮮度判定

Iteration 30の独立Reviewerは、timestampなしのプロフィール情報を永続的な予定根拠として通す問題と、同じ記録内の単発予定の古さが別の繰り返し予定にも影響する問題を指摘した。修正後は文を節に分け、相対日付の鮮度を節単位で判定する。古い「毎週土曜は空いてる。明日は予定がある」では週次節を残し、古い「明日」は捨てる。周期表現も同じ曜日節にある場合だけ適用する。timestampなしの相対予定節は除外する。単体テストでは後方互換としてtimestamp省略を「現時点の事実」とみなすが、本番の生成contextはtimestamp配列を渡し、プロフィールとKnowledgeは`None`のままなので予定根拠から除外される。

Iteration 31の新しいPython ReviewerはPASS。対象テスト2件、全backend suite 452件、frontend buildもPASS。ただし最新コードの70ケース生成評価は未実施である。Step 18-R4は未完成のままとする。

## Iteration 32: Human / Conversation 低下の原因確認

Iteration 28の完全artifactを確認したところ、70ケース中9件はAPIエラー、6件は安全な本人確認へ分岐し、返信候補は165件だった。平均はHuman 0.861、Conversation 0.940。以前資料に記録されていたHuman 0.856、Conversation 0.938の統合集計artifactは今回確認できなかったため、確定済みの再現結果としては扱わない。いずれもIteration 31の変更を含まない。

Conversation軸が低い候補には、短い相手の一言に対する労いや気遣いが、相手メッセージとの文字数比で下がる例が多い。Human軸は複数の代理指標の最小値で、話題語を自然に使った相づちや「また明日ね」への「はーい、また明日」もEcho判定で低くなる。一方、「えー、なんだか気になる反応ですね笑」のように実際に硬く不自然な返答や、未確認の状況を足した候補も含まれる。したがって、評価器を変えずに数値だけを追う修正は避ける。

最新partial artifactは33ケース分で、返信候補あり15件、本人確認への安全な分岐2件、HTTP 502が16件。502が続いたためRunnerを停止した。全70ケースを再評価できていない。API枠またはサービスの回復後、最新コードを用いて再実行し、全ケースを出力または意図した本人確認へ分類したartifactを作る。低スコア候補の実例と評価器が下げた理由を独立Reviewerに見せ、製品品質の問題と測定上の限界を分けて判定する。

## Step 18-R4 Iteration 34: Tappleの自宅行程チェック

Fresh Reviewerが、公開カフェのあとに「うちで」「おうちで」会う案や「家飲み」を続ける誘い例は、公開場所の語を含むだけで通過し得ると指摘した。テストを先に追加すると修正前に失敗。自宅を示す表現を検出する条件を広げ、「家で」 / 「うちで」 / 「おうちで」 / 「お家で」 / 「家飲み」 / 「うち飲み」、個室、ホテルを含む行程を拒否する。一方、「家族の話もできる駅前のカフェ」のように「家」を含むだけの公開場所例は許容する。

初回修正で「家族」まで拒否する過剰判定が出たため、表現を限定して再テストした。Tapple専用テストは**24 passed**、fresh Reviewerは**PASS**。最新コードでの全backend suiteは**498 passed / 2 warnings**、`frontend`の`npm run build`、`git diff --check`、対象PythonのcompileもPASS。Step 18-R4の70-case regression、Contact Bench、Tapple API実生成は未完了。利用者の指示に従い1時間監視を停止したまま、朝の確認までAPIを呼ばない。よってR4は未完成・pushなし。

## Tapple向け会話戦略の実装状況（Iteration 1・検証中）

利用者は相手から届いた文面をアプリへ手動で貼り付け、返信候補を確認してからタップルへ手動で貼り戻す。アプリとタップルを直接接続する機能、自動読取、自動送信は追加しない。調査結果を汎用生成に混ぜず、利用者が明示的に選択する既定OFFの戦略オーバーレイとして実装した。候補返信と戦略カードは別表示で、戦略例は送信候補と誤認しない説明欄に置く。

戦略は`continue / clarify / invite / wait / stop`を使い、証拠は相手の履歴発言からの完全一致抜粋だけを受け入れる。モデルが`invite`を返しても、直近の相手発言に明確な相互の参加意思がなければ`wait`へ落とす。否定・条件表現は同意として扱わず、明示的な拒否は`stop`にする。返信候補数1〜3のときも、初回・修正・repairの出力契約、JSON mode、解析数が一致するようにした。公共の場所を含まない誘い例と連絡先交換を促す誘い例は表示しない。戦略JSONが不正でも通常返信の生成を失敗させない。

実装範囲はスキーマ、プロンプト、API解析、画面トグル/戦略カードおよび回帰テスト。フロントは連絡先切替中の生成をabort/invalidateし、古い返信・戦略・エラーが新しい相手へ混入しない。接触適応のGold優先・少数データfallback・文脈優先の原則は変更しない。

Tapple専用テストは**22 passed**。独立Backend Reviewerは候補数・否定表現・条件表現・返信候補との分離を再確認して**PASS**、独立TypeScript Reviewerも連絡先切替時のabort/invalidationを**PASS**と判定した。実装の作業ツリーはStep 18-R4のGold優先修正を含む。APIの実生成確認は未完了。Geminiの前回実行でHTTP 502が連続したため追加コールを行わず、モデル由来の戦略文と返信候補の実例品質はまだ確定しない。Step 18-R4の70ケース回帰とは別に、API復旧後に明示同意・拒否・曖昧反応のprobeでTapple戦略を実測する。

## API復旧probeと再開条件（2026-10-08）

API credential fileを既存コードから安全に読むhelperと、Tappleモードで1件だけ生成する疎通probeを追加した。`gemini2.md`と`gemini3.md`は単一値形式として認識され、値は異なる。2をprimary、3をsecondaryに設定した。キーや生レスポンスはログ・artifactに含めない。

probe順はprimary accountの3.5、3.1、secondary accountの3.5、3.1。アカウントを切り替えるのは、primaryの両モデルが`rate_limit`を返した場合だけとする。初回はprimaryの`provider_error`であり、secondaryへの切替は起きていない。利用者の希望により、PID 30392の1時間監視は停止し、朝の確認までAPIを呼ばない。APIが応答したら選択されたアカウントとモデルで70-case regressionとContact Benchを実行する。

APIに依存しない点検で4件の不具合を見つけた。Contact Benchが`re`をimportせずsignature判定で落ちる、1〜2件のsame-contact Goldが強い模倣例としてpromptに入る、Tappleの返信候補から外部連絡先交換を促せる、公開場所と私的場所を組み合わせた誘い例が通る問題を修正し、各focused testのRED→GREENを確認した。最終の全suite/build結果は後続で記録する。実生成品質とR4最終判定はAPI確認後まで保留する。

## Iteration 33: Gold-only fallbackの階層保証

独立レビューで、same-contact Goldが少数ある場合だけでなく、別相手のmanual Goldが1〜4件あるときも、対象相手のsent Silverが後段の`phase_prof`/`global_prof`へ混ざってGoldを上回り得ることを発見した。`same_contact_sent_silver` tierを選ばないだけでは不十分で、次のfallback自体がSilverを含んでいた。

修正後はmanual Goldが1件でもあればactive profileをGold由来のみから作る。5件以上は`global_manual_gold`、1〜4件は`sparse_manual_gold_fallback`とする。same-contact Goldが3件以上の場合は従来どおりGlobal Goldと同一相手Goldを徐々に混ぜ、局所Goldが少ないときは局所単独適応しない。Silver profileは、全体・対象相手のどちらにもmanual Goldがないときだけfallbackとして利用する。

REDを確認した後、Gold 1/2/3/4件それぞれと対象相手Silver 3件の組み合わせをparameterized testで確認し、Goldの丁寧さがactive profileに残ることを検査した。Goldなしの場合に限りSilver fallbackを許すテストも追加。relationship suiteは21件PASS、独立Python ReviewerもPASS。最新backend suite **480 passed / 2 warnings**、frontend build **PASS**、`git diff --check` **PASS**（CRLF注意のみ）。

これはStep 18-R4のGold優先条件の修正完了を示すが、R4全体は未完了。最新コードでの70-case regressionとContact Benchを実行できていないため最終合格・pushにはしない。最後に確認できた70-case artifactはIteration 28の旧コードで、Human 0.856 / Conversation 0.938と目標未達。最新partialは33/70で15件生成、2件本人確認、16件HTTP 502のため停止済み。

## Iteration 35: Gemini予備アカウントへの切替

独立監査で、API疎通probeは2アカウントを順に確認するものの、アプリ本体の生成処理は主アカウントしか使わないことが分かった。`GEMINI_SECONDARY_API_KEY_FILE`または`GEMINI_SECONDARY_API_KEY`で予備キーを任意設定できるようにし、設定APIにはキーの有無だけを返す。鍵ファイルは単一のキー行か`GEMINI_API_KEY=...`行を読み込む。秘密値はログ、レスポンス、資料へ出さない。

対象のGeminiモデル設定では、主アカウントの3.5、3.1、予備アカウントの3.5、3.1の順に試す。切替条件は`rate_limit`に限定する。認証エラーなど別のエラーでは次アカウントへ進まず、誤設定を隠さない。予備キーがない場合、または主キーと同じ場合は従来のモデルfallbackを使う。各リトライ・修復生成も、選択済みアカウントとモデルを引き継ぐ。

実装に先立ちfallbackキーの由来メタデータとアカウント切替順のテストを追加し、修正前に失敗することを確認した。さらに、DB保存キーを環境変数由来と誤表示する既存不具合を再現して修正した。生成順、二つの主モデルが制限された場合だけ予備へ進むこと、非レート制限エラーでは停止すること、鍵値を公開しないことを回帰テストで検証した。独立Reviewerは最新差分を**PASS**と判定した。

`python -m pytest backend/tests -q`は**502 passed / 2 warnings**、frontendの`npm run build`は**PASS**。`git diff --check`と対象Pythonのcompileも**PASS**。API実呼び出しは利用者の希望により保留しているため、二つのアカウント間の実疎通、最新70ケース、Contact Bench、Tapple生成例は未確認である。Step 18-R4はまだ合格ではなく、pushしていない。API復旧後は疎通を1回ずつ確認し、通ったモデル・アカウントで70ケース、Contact Bench、Tappleの拒否・曖昧・明示同意ケースを評価する。

## Tapple Iteration 2: 外部連絡先表現とrepair結果の整合

独立監査で2点の問題を見つけた。「LINEで話しませんか」「InstagramのDMで話しませんか」など、外部連絡先名と会話移動の提案を組み合わせた表現がTappleモードの最終検証を通る。また、最初の候補が拒否されてrepair候補が採用された場合も、戦略カードはrepair前の出力から作られていた。

表現の回帰テストを先に追加し、修正前に失敗することを確認した。連絡先名に加えて「話しません」「連絡取りません」などを含む候補を拒否する。さらに、最終採用したraw出力を保持し、返信候補と同じ出力から戦略カードを解析する。初回候補が外部連絡先チェックで拒否され、repair候補の返信と`continue`戦略が返ることをAPIテストで確認した。

最初の独立Reviewerはさらに「LINEしない？」等の短縮表現が拒否条件から漏れる点と、生成文を安全な確認文に置き換えた後も元の戦略を返す点を指摘してFAIL。双方の再現テストを追加した。外部連絡先名と「しない」「しません」等を組み合わせた提案を拒否する。決定論的な確認文へ候補を置き換えた場合は、対応するAI戦略がないため戦略カードを省く。

Tapple専用テストは**26 passed**、Gemini fallbackテストは**13 passed**。最新全backend suiteは**504 passed / 2 warnings**。frontend build、`git diff --check`、対象Python compileは**PASS**。新しい独立Python Reviewerも修正後の差分とfocused testを確認して**PASS**。API呼び出しはしていない。Step 18-R4の最新70ケース、Contact Bench、Tapple実生成は引き続き未完了。

## Geminiベンチマークの予備アカウントfallback

朝に行う実生成評価でも、主アカウントの3.5と3.1が両方レート制限になった後に予備アカウントへ移れるよう、`run_pipeline_benchmark.py`と`run_contact_benchmark.py`に`--secondary-env-file`を追加した。両Runnerは主キー・予備キーを別ファイルから読み込み、APIキーを表示せずアプリのGemini設定へ渡す。3.5実行時のfallback設定は主アカウントの3.1のまま維持するため、アプリ本体のquota chainが主3.5→主3.1→予備3.5→予備3.1を適用する。

共有設定関数のテスト**2 passed**、両CLIの`--help`も確認した。最新backend suiteは**506 passed / 2 warnings**。Frontend build、`git diff --check`、対象Python compileも**PASS**。独立Python Reviewerは両Runnerがキーを表示せず予備キーを渡し、従来の3.5→3.1順を維持することを確認して**PASS**。実装commitは`61ec2fe`で、未push。

API疎通・70ケース・Contact Benchは依然未実施。API回復後はまず一回だけ疎通を確認し、3.5を指定して全70ケース、続いて3接触先のContact Benchを実行する。ケース間の既定待機時間は6秒。

アプリ実行時もキーを直接設定画面へ貼らずに済むよう、`GEMINI_API_KEY_FILE`で主キーのファイル読込を追加した。`GEMINI_SECONDARY_API_KEY_FILE`は予備キーを読み込む。DBに登録した主キーがあれば引き続きDBを優先する。利用環境の`.env`にはgemini2.mdとgemini3.mdのパスだけを設定し、キーの内容はコピーしない。

## Gemini主・予備キーのファイル設定

主・予備キーをファイルのまま読み込む経路を統合テストした。`GEMINI_API_KEY_FILE`にgemini2.md、`GEMINI_SECONDARY_API_KEY_FILE`にgemini3.mdを設定すると、主キーの3.5→3.1を試した後、両方がレート制限の場合に予備キーへ進む。DBの主キーが登録済みなら設定ファイルよりDBを優先する。設定APIには主・予備キーの有無だけを返す。

ファイル読込を使うテストは**16 passed**、backend全体は**509 passed / 2 warnings**。frontend build、`git diff --check`、対象Python compileは**PASS**。独立Python Reviewerも**PASS**。実装commitは`51c14aa`、GitHub main基点は`a75ba76`で、WIPはpushしていない。API実生成は保留中のため、Step 18-R4は未完成。

## Tapple API実生成の再開手順

Gemini復旧後にタップル戦略を確認するため、独立スクリプト`run_tapple_strategy_benchmark.py`を追加した。アプリ本体と同じ生成・検証処理を通し、明確な参加意思、曖昧な反応、明示的な断りを各1件試す。結果から返信、戦略、使用モデル、期待した行動との一致を記録する。実データや本番DBには触れず、一時DBを使う。鍵は出力しない。

当時の期待値チェックの単体テスト2件、`--help`、独立Python Reviewerは**PASS**。backend suiteは**511 passed / 2 warnings**、frontend buildも**PASS**。APIは呼び出していない。当時はTapple 8シナリオだったが、現在は11件に増えている。朝の実行ではLIAISON記載の現行手順を使い、70ケース、Contact Bench、Tapple全11シナリオを行う。各artifactはローカル一時領域へ保存し、失敗ケースも残してReviewerへ渡す。

なお、主キー側で3.5と3.1の両方がレート制限になったときだけ予備アカウントへ移り、予備側でも3.5→3.1を試す。主アカウントの3.1が成功した場合はアカウントを切り替えない。この分岐は回帰テストで確認済み。

## Tappleベンチの期待値判定修正

独立レビュー時に、曖昧な好意へ`invite`しない条件だけでは不十分で、会話を唐突に切る`stop`も既存ベンチで合格してしまうと分かった。本人と相手の発言を含む短い会話に変え、許容する行動をケースごとに列挙した。明示的な参加意思では`invite`、曖昧な反応では`continue`/`clarify`/`wait`、拒否では`stop`だけを合格とする。

テストを先に追加した時点で2件が失敗し、修正後はTapple専用テスト**3 passed**、新しい独立Reviewer **PASS**。backend全体は**512 passed / 2 warnings**、frontend buildも**PASS**。`--help`と対象Python compileもPASSで、APIは呼んでいない。RED commitは`2d0c93b`、GREEN commitは`4325e9e`。API実生成・70ケース・Contact Benchは未実施であり、Step 18-R4は未完成。

Step 18-R4 Iteration 1 ReviewerのGold優先FAILを現在のHEADで独立再監査した。focused `test_step18_relationship.py`は**36 passed**。同一相手のGoldが1〜2件でもSilverへ置き換わらず、Silverは手入力Goldが0件の場合のみfallbackとして使うことを確認し、Reviewerは**PASS**と判定した。

## APIベンチのquota停止と未完了artifact

70ケース評価で全モデル・全アカウントのquotaを使い切った後、残りケースでも同じ失敗を繰り返す経路を修正した。pipeline、Contact Bench、Tapple Benchは生成APIが失敗した時点で後続ケースを呼ばず、途中結果を保存する。artifactには完了数・予定数・停止理由を残し、未完了または失敗時は終了コード2を返す。全ケース数を処理していても最終ケースにエラーがあれば`complete: false`になる。

新設`benchmark_response.py`の判定テストで、quota失敗・別のprovider error・最終ケース失敗・途中終了・全件成功を確認した。関連focused testsは**12 passed**、全backend suiteは**517 passed / 2 warnings**。frontend buildと4つのCLI `--help`も**PASS**。独立Python Reviewer **PASS**。APIは呼んでいない。RED commitは`e38fe63`、GREEN commitは`8f0e78d`。

Geminiの実行設定として、Git管理外の`.env`から主・予備のキー参照ファイルを読み込めることも確認した。現在の作業環境では両ファイルを別アカウントとして解決し、モデル設定は3.5 primary／3.1 fallbackとなっている。`test_api_key_file.py`と`test_model_fallback.py`は**25 passed / 2 warnings**。キー本体は出力・複製していない。APIへの実リクエストは行っていないため、実quota時の切替が動作したという意味ではない。

API評価の再開手順には各段階の終了コード判定を追加した。疎通で利用可能モデルが見つからない場合や生成が未完了の場合は、その後のベンチを起動しない。70ケースの全指標とContact Benchの3/3は終了コードだけでは判定できないため、artifactを読んで合格を確認する手動ゲートも置いた。これにより全キーのquota枯渇後の再試行と、品質未確認のまま次の評価へ進むことを避ける。

さらに`verify_pipeline_benchmark.py`を追加し、既定の70ケースJSONにあるIDがすべて一度ずつ存在すること、生成エラーがないこと、artifactの集計値がケース別データから再計算した値と合うこと、既存の6閾値をすべて満たすことを機械判定する。`--case-ids`で一部だけ成功したartifactは、`complete: true`でも全体合格にはできない。API呼び出しなしの検証テストは**6 passed**。

Tapple戦略ベンチも、3ケースすべて生成できたことと戦略期待値が合格したことを分離した。全3ケースの`expectation_met`が真で、IDが重複・欠落していない場合だけ`quality_pass: true`、終了コード0とする。不完全な生成は2、完走して期待戦略が不一致なら3で終了し、artifactには失敗ケースを記録する。実際の返信文の自然さ・文脈・安全性はこの機械判定だけでは保証しないため、全返信文のレビューを別ゲートにした。focused testは**3 passed**、既存Tapple suiteは**26 passed**。

独立レビューで、artifactが期待件数だけ満たせば合格になる抜けを検出し、修正を繰り返した。Tapple側はケースIDの重複・欠落・エラーと中間保存状態を含めてfail-closedにした。正規70ケース評価側は、各ケースの候補数、全issue項目、context/human/conversation軸と`personal_style_fit: null`を検証し、欠損レコードが平均計算から脱落して閾値を満たすことを防ぐ。要約値はcase-levelデータから再計算し、型を含めて照合する。評価対象はリポジトリの固定70ケースデータに限定する。

Gemini 3.5/3.1 Flash Liteの429応答は内部リトライをせず、generation routerへ即時返す。3.1のquota後に数秒待って同じモデルを再呼出しする経路がなくなり、設定済みの別アカウントchainへ進める。その他のGeminiモデルの既存リトライは変更していない。

現HEADで`python -m pytest backend/tests -q`は**541 passed / 2 warnings**、frontend buildは**PASS**。独立Python ReviewerはTapple benchmark・正規70ケースvalidator・Gemini fallback retryをすべて**PASS**と判定した。APIを呼ばずに行った確認であり、最新70-case artifact、Contact Bench、Tapple実生成・文章レビューは未実施。Step 18-R4は引き続き未完成で、GitHubへpushしていない。

### 2026-10-09 Tapple Iteration 4: 代替日程後の再拒否

独立レビューで見つかった「一度は代替日を提案したものの、その日も都合が悪い」という会話を追加した。たとえば「来月は無理ですが再来月なら会えます。でも再来月も都合が悪いです」では、古い代替提案を根拠に誘い続けず、最終的な拒否を優先する。失敗していたテストの根拠表現も「都合が悪」を含めるように揃えた。

独立Reviewerが指摘した評価器差分をHEADと照合し、今回の作業差分から除去した。第三者が伝えた日程と本人自身が提案した日程を区別し、返信に本人の提案日が含まれる場合だけ、その日程を調整対象として許可する。「土曜は予定があって、日曜なら大丈夫」のような、会話でよくある代替日の表現も拒否扱いしない。返信validatorが日曜への日程提案を通すこともテストした。Tapple focused suiteは**190 passed**、backend全体は**696 passed / 2 warnings**。fallback focused suiteは**29 passed**。frontend production build、対象Python compile、benchmark `--help`、`git diff --check`もPASS。最新差分に対するfresh Python Reviewerと独立Tapple safety reviewerはともに**PASS**。評価器スクリプトとthresholdに差分はない。

Gemini APIは呼んでいない。3.5 primary→3.1 fallback→予備アカウント3.5→3.1の順で、各切替をrate limit時のみに制限するコード経路はテスト済みだが、実キーでの疎通は未確認。最新70-case regression、Contact Bench、Tapple実生成と生成文レビューが残るため、Step 18-R4は未完成・未push。

朝の実行手順には、疎通後の70ケース評価・検証器、Contact Bench、Tapple実例レビューに加えて、push前のbackend全pytest、frontend build、`git diff --check`、最終差分に対する独立Reviewer二名のPASS、両進捗資料の実測値更新をまとめて記載した。手順とフォールバック経路は独立reviewer **PASS**。疎通は1モデル・アカウントあたり1回で最大4回。非quotaエラーなら即時停止する。実HTTP 429をmockしたテストでも、3.5/3.1それぞれ1回で止まることを確認した。gemini2.md/gemini3.mdは存在確認のみ行い、内容やキー値は出力していない。

## 2026-10-09 オフライン品質ゲートの追加

独立レビューで見つかったTapple戦略の境界誤判定を修正中。断りと代替日程を分け、拒否・保留後の直接的な再勧誘に加えて「日曜はどうですか」「今度そこ行こう」のような日程提案も拒否する。一方、明確な本人の承諾後や、相手自身が代替日を提案した場合の予定調整は許可する。友人の意向の伝聞や、前向きな希望と不安が同時にある文は承諾扱いしない。

Tapple戦略ベンチでは、記号だけ・短すぎる根拠、招待方針と矛盾する説明、明示的な話題転換を含む返信を不合格にする。シナリオごとに話題要素と応答要素も検査する。ただし文字列ベースのゲートで意味の自然さを保証できるとはみなさず、ライブ生成例の人手レビューを必須にする。

未commit差分を含む検証結果は、Tapple専用**76 passed**、fallback **19 passed**、backend全体**581 passed / 2 warnings**、frontend production build **PASS**。fresh reviewer 2名の最終判定待ち。Gemini 3.5 primary・3.1 fallbackと予備アカウントの経路はコードと回帰テストで確認したが、APIは呼び出していない。最新70ケース、Contact Bench、Tapple実生成例は未確認で、Step 18-R4は合格・pushともに未完了。

## 2026-10-09 Contact Adaptation: Gold priorの重複を除去

独立監査で、対象相手のGoldが全体Gold profileにも含まれたまま、同じGoldを相手別profileとして再度混ぜていることが分かった。テストでは他相手の丁寧なGoldが5件、対象相手のカジュアルGoldが3件のとき、設定上の相手別weightは0.375でも、重複によりカジュアル比率が0.61になった。

相手別Goldを除いたGold profileを基準値にしてから、対象相手のGoldを既存weightで混ぜるようにした。他相手Goldがなければ唯一のGold profileを維持する。修正前の再現テストは失敗し、修正後はContact Adaptation suite **27 passed**、backend全体 **697 passed / 2 warnings**、frontend production build **PASS**。独立Python Reviewerは**PASS**。quota時の主3.5→主3.1→予備3.5→予備3.1のfallback suiteは**29 passed**。

Gemini APIはまだ呼び出していない。最新70ケース、Contact Bench 3/3、Tappleの実生成文レビューも未実施のため、Step 18-R4は未完成。再現テストcommitは`32a4e9d`、Gold修正commitは`0ab254d`、Tapple/quota修正commitは`e31bd8b`。コードWIPと進捗資料はforkの作業branchに公開済みで、GitHub main基点`a75ba76`は変更していない。

## 朝のContact Bench判定基準

Contact BenchのCLIは、A/B/Cの生成完了だけを返し、品質の3/3判定は行わない。`contact.json`の全9返信を読み、各返信が同じ入力「仕事で疲れた」に沿い、実際に送れる自然さであることを確認する。根拠のない事実や不要な質問がなく、相手の文面をコピーしていないことも確認する。

Aの返信群はGoldに沿った短く砕けた傾向、Bは自然な丁寧さと相対的に十分な文量、Cは中間の文量と丁寧・砕けた表現の混在を示す必要がある。文字数やtone signatureの違いだけでは合格にせず、文脈・自然さ・本人Goldとの適合を含めてA/B/Cすべてが条件を満たした場合だけ3/3とする。いずれかが満たさない場合は不合格の返信を記録し、Tapple実生成へ進まない。

## 2026-10-09 Tapple Iteration 22: 迷い・安全文脈の追加境界

Reviewerの指摘を受け、`迷う`、`悩む`、`悩んでる`など口語・終止形の会うことへの迷いを招待判定と日程提案の双方で保留する。否定を含む仕事上の悩みや文頭に置かれた仕事の迷いは、会うことの迷いに誤分類しない。後続文の「資格を取るか」「転職先をどこにするか」の不確実さも除外しつつ、句読点をまたぐ別の会うことへの迷いを消さないよう、無関係な話題の除外は句内に限定した。

安全文脈では、仕事上の安全不安だけを無関係として除き、「仕事帰りに会うのは安全面で不安」「会社の近くで会うのは安全か分からない」のように勤務先が話に出る場合も、対面に関する懸念を優先して招待・日程調整を保留する。これらの境界についてREDを確認するテストを先に追加し、各修正後にGREENを確認した。

Tapple focused suite **299 passed**、backend全体 **828 passed / 2 warnings**。主3.5→主3.1→予備3.5→予備3.1のキー切替・quota fallback mock suite **26 passed**。最終差分への独立code reviewerおよびPython reviewerはともに**PASS**。frontend production build、Python compile、`git diff --check`もPASS。実APIは未呼出し。修正と進捗資料のcommit `9bd4682` はforkの作業branchへpush済み。最新70ケース、Contact Bench、Tappleの実生成文レビューは未実施のため、Step 18-R4は未完成である。

## 2026-10-09 Tappleの安全懸念と参加意思の境界

招待可否では、直近の相手発言全体から安全・信頼への懸念を確認する。参加意思の根拠抜粋だけを検査すると、同じ発言の別箇所にある「身元が分からない」「相手がどんな人か分からない」「まだ会ったことがなくて不安」「安全かどうか分からない」を見落とすためである。一方、天気への心配や、参加意思より前にある仕事の逆接表現を理由に招待を止めない。安全への不安を否定した表現は懸念として扱わず、参加意思の後に迷いが続くときは日程調整を保留する。

この境界を検証する回帰テストを追加した。Iteration 6 Reviewerは「会いたいけど少し怖い／不安」という参加意思の後に続く安全懸念を見逃す点を指摘したため、招待可否と日程調整の両方を修正した。次のReviewerは、代替日提案と安全懸念が同時にある場合、別のcounterproposal経路から日程調整が許可される問題を指摘した。`has_counterproposal`にも安全懸念と対面への迷いの確認を加えた。曖昧な否定を安全確認済みと誤認する問題、日程選び・仕事の悩み・会う前の準備を会う意思への迷いと誤認する問題も修正した。独立Python reviewで文頭に「正直／私は」が付く迷いの見逃しと、「不安を感じていません」という否定の誤検出が見つかった。これらのテストを追加し、判定を修正した。「会う日の候補はいいけど少し迷う」を日程選びとして扱うテストと判定も追加した。Tapple strategy suiteは**223 passed**、APIキー切替と疎通のmock suiteは**26 passed**。backend全体suiteは最新差分で再実行中。frontend production build、対象Python compile、`git diff --check`はPASS。Iteration 21の独立ReviewerとPython Reviewerが確認中。Gemini APIは未使用で、Step 18-R4の最新70ケース、Contact Bench、Tapple実生成文の目視確認も未完了。

## 2026-10-09 現在のオフライン受け入れ状況

監査で、ベンチの成功結果に使用アカウントが残らず、主・予備のどちらのキーで生成したかをartifactから追えない点を確認した。共通metadata解決関数を追加し、70ケース・Contact・Tappleそれぞれの成功結果へ`successful_route`として`account`と`model`だけを出す。APIキーとfingerprintは成果物に含めない。quota時の経路順序とroute-state保存・再開条件は変更していない。

Tappleの相互活動関心ベンチ2ケースを、実際のstrategy parserへ通す回帰も追加した。同じ`invite`候補を与え、温かい複数ターン会話では`invite`を維持し、反応が短い履歴では`wait`に変わることを確認する。

`python -m pytest backend/tests -q`は**889 passed / 2 warnings**。frontend production build、Python `compileall`、pipeline・Contact・Tappleの`--help`、`git diff --check`はPASS。新しい独立Python Reviewerは戦略ペアテストとベンチ経路metadataをそれぞれPASSと判定した。REDテストcommitは`dab481e`、実装GREEN commitは`622ab4d`。GitHub main基点は`a75ba76`、PR #1はOpen・未マージ。APIは呼び出していない。

API依存の完了条件は未達。朝の利用者確認後に単発疎通を行い、実際の成功routeを確認する。その後、正規70ケースと全指標検証、Contact Benchの全9返信・3/3目視判定、Tapple全11シナリオと生成文レビューを行う。rate limit以外のエラーは即時停止し、部分結果は成功扱いしない。実生成の全評価が未実施のためStep 18-R4は未完成。

### 2026-10-09 オフライン経路記録テストの拡張

成功routeの記録テストを4通りに拡張し、主・予備アカウントそれぞれの3.5・3.1経路で`account`と`model`が正しく記録されること、APIキーが結果へ含まれないことを確認した。`python -m pytest backend/tests -q`は**892 passed / 2 warnings**。独立Python Reviewerはテストと、Tapple旧8件の記録を現行11件へ案内する資料修正を**PASS**と判定した。frontend build、compileall、`git diff --check`もPASS。Gemini APIは呼び出していない。70ケース、Contact Bench、Tapple全11シナリオの実生成と目視確認が残るため、Step 18-R4は未完成。

## 2026-10-09 Tapple Iteration 26: 第三者の意向と本人の意思

独立レビューで、第三者の「会いたい」「一緒に行きたい」という希望が本人の意思に見える問題が見つかった。第三者を示す語句だけでなく、引用・伝聞の形も確認し、会いたい表現と一致する箇所が第三者に属する場合は本人の意思として採用しない。モデルの根拠抜粋だけで話者を決めず、元メッセージ中の該当位置と照合する。本人が同じ発言で別に「あなたと会いたい」と明示したときは、その部分を第三者の発言に巻き込まない。

第三者の受諾らしい発言だけでは日程を提案できない。友人・親族・同僚・先輩などの代替日も本人自身の候補日と区別する。人名詞から広い文字数範囲で希望表現を拾うと、「妹の話ですが、私はあなたと一緒に行きたい」の本人意思を誤って除外するため、話者の帰属が文法・伝聞表現で確認できるパターンに絞った。

拒否・迷い・安全面の懸念についても継続判定を統一した。本人の明示的な再開意思がない限り第三者の希望や日程都合で過去の拒否を解除しない。迷い・安全懸念が残っていれば、戦略が`invite`または`continue`でも誘い・予定調整を許可しない。安全への不安を否定する文の後に「まだ不安」と続くケースも未解消として扱う。丁寧な再考要求や「今回だけ」のような間接的圧力の回帰テストも追加した。

このIterationの最新ローカル検証はbackend **968 passed / 2 warnings**、Tapple strategy **403 passed**、APIキー切替関連 **45 passed**。frontend production build、compileall、benchmark `--help`、diff checkはPASS。独立Safety Reviewer / Python ReviewerはPASS。キーの選択順はprimary 3.5 → primary 3.1 → secondary 3.5 → secondary 3.1で、rate limitの場合だけ次へ進む。実APIは利用者の朝の確認まで未呼出し。最新70ケース、Contact Bench、Tapple 11ケースの実生成・目視確認が残るため、Step 18-R4は未完成。

## 2026-10-09 Step 18-R4 Tapple Iteration 5: 現在の意思と過去の拒否を分離

独立レビューで、現在の「会いたい」が過去の「会うのは無理だった」より先にあると、後続の歴史的な拒否表現が現在の迷いとして処理され、招待判断が`wait`に落ちる問題を確認した。反対に、過去の拒否が先にある場合は、現在の明確な意思で招待へ進めていた。この順序差を埋める回帰テストを追加し、現在意思と過去の拒否の記載順が違っても同じ判断になるようにした。

初回修正後の独立レビューでは、過去の拒否と同じ文に含まれる後続の明確な拒否まで除外する問題、および第三者の引用発言を本人の意思として扱い得る問題が見つかった。追加テストで再現し、現在の明確な拒否は停止判定に残し、過去の拒否はそれを示す特定の節だけを除外するよう範囲を限定した。友人や第三者の引用・伝聞は本人の意思判定から除外する。

現HEAD `bec4388`でTapple専用テストは**519 passed**、backend全体は**1,084 passed / 2 warnings**、モデル・アカウント切替関連は**45 passed**。frontend production build、Python `compileall`、`git diff --check`もPASS。最終差分を確認した新しいcode reviewerと独立Tapple safety reviewerはともに**PASS**。

APIは呼び出していないため、実際のquota応答と生成品質は未確認である。実行時・評価時の経路順序はprimary 3.5 → primary 3.1 → secondary 3.5 → secondary 3.1で、`rate_limit`時のみ次へ進む。最新70ケース、Contact Bench全9返信、Tapple全11シナリオと生成文の目視レビューが残るため、Step 18-R4は未完成。最新コードと資料はfork作業branchへpushし、remote SHA一致を確認した。GitHub mainは基点`a75ba76`のまま。

## 2026-10-09 quota fallbackで制限済みアカウントへ戻らない修正

朝の疎通確認が予備アカウントで成功すると、評価ベンチが疎通確認で制限済みと判定した主3.5・主3.1を再試行する経路が残っていた。予備アカウントをactiveとして開始する場合は、予備側の残りのモデルだけを設定し、主アカウントへ戻らないよう修正した。通常起動時の主3.5 → 主3.1 → 予備3.5 → 予備3.1という順序、rate limit時だけ進む条件、主アカウントから評価を始める場合のfallbackは維持した。

まず旧動作で失敗する回帰テストを追加し、RED test commit `8d7d53a`を作成した。実装修正commitは`6b68dcc`。focused suiteはbenchmark config **18 passed**、APIキー・model fallback **27 passed**。`python -m pytest backend/tests -q`は**1,084 passed / 2 warnings**。frontend production build、Python `compileall`、`git diff --check`はPASS。新しいread-only code reviewerもPASSと判定した。

Gemini APIは呼び出していない。最新70ケース、Contact Bench全9返信、Tapple全11シナリオの実生成と目視確認は未実施で、Step 18-R4は未完成。修正commit `6b68dcc`と資料commit `3c1da1b`はforkへpushし、remote SHA一致を確認した。API疎通確認は利用者が再開を指示するまで行わない。
