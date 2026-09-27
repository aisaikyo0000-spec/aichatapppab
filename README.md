# Matching Reply Assistant 完全仕様書（Conversation-Learned Reply System v3.1）

本書は、**Matching Reply Assistant** の最新コードベース、データベース構造、REST API、AIプロンプト生成エンジン、学習優先型アーキテクチャ（v3.1）、フロントエンドUI、および運用仕様を網羅した**正本（Source of Truth）となる完全仕様書**である。

本書は、従来の固定ルールによる機械的一律制約を完全撤廃し、**「ユーザー自身が実際に手入力・送信した実メッセージ履歴（Gold/Silver）を最優先の正解として学習・抽出し、階層スタイルプロファイル、対照学習（不採用AI案 vs 手入力）、2段階検索（Phase/品質/トピック一致）によって本人らしい自然な会話返信を生成する『Conversation-Learned Reply System v3.1』」**の仕様を定義する。

---

## 目次

1. [システム概要](#1-システム概要)
2. [設計思想とConversation-Learned Reply System v3.1](#2-設計思想とconversation-learned-reply-system-v31)
3. [技術構成](#3-技術構成)
4. [AIモデルと推論設定](#4-aiモデルと推論設定)
5. [全体アーキテクチャとデータフロー](#5-全体アーキテクチャとデータフロー)
6. [データモデルとDBスキーマ（generation_batches & messages.source）](#6-データモデルとdbスキーマgeneration_batches--messagessource)
7. [学習モジュール（backend/app/learning/）仕様](#7-学習モジュールbackendapplearning仕様)
   - 7.1. [コーパス抽出・Turn集約・品質ラベリング（corpus.py）](#71-コーパス抽出turn集約品質ラベリングcorpuspy)
   - 7.2. [階層スタイルプロファイリング（style.py）](#72-階層スタイルプロファイリングstylepy)
   - 7.3. [失敗差分の対照学習（contrast.py）](#73-失敗差分の対照学習contrastpy)
   - 7.4. [2段階ランキング・Few-shot検索（retrieval.py）](#74-2段階ランキングfew-shot検索retrievalpy)
8. [ゼロベース 8ブロック プロンプト構成（prompt.py）](#8-ゼロベース-8ブロック-プロンプト構成promptpy)
9. [生成バッチライフサイクルと診断API](#9-生成バッチライフサイクルと診断api)
10. [不変のコア規約（HARD INVARIANTS / CORE RULES）](#10-不変のコア規約hard-invariants--core-rules)
11. [トーンHard Lock・誤分割阻止・再生成仕様](#11-トーンhard-lock誤分割阻止再生成仕様)
12. [API仕様一覧](#12-api仕様一覧)
13. [テストスイートと検証実績](#13-テストスイートと検証実績)
14. [改修時に絶対に壊してはいけない仕様](#14-改修時に絶対に壊してはいけない仕様)

---

## 1. システム概要

* **名称**: Matching Reply Assistant [実装確認済み]
* **ビルドバージョン**: `learned-reply-v3.1` (PROMPT_VERSION: `v3.1`) [実装確認済み]
* **形態**: ローカル完結型 シングルページWebアプリケーション (SPA) + REST API サーバー [実装確認済み]
* **外部連携**: 実際のマッチングアプリとの直接API連携は行わず、手動コピペ運用を前提とする [実装確認済み]。
* **データ可搬性**: すべての会話履歴、設定、プロファイル、画像、評価履歴はプロジェクトルート直下の `data/app.db` (SQLite) およびローカルフォルダ内に保存 [実装確認済み]。

---

## 2. 設計思想とConversation-Learned Reply System v3.1

* **ユーザーの実績が最上位のスタイル正解（Learning-First）**:
  * 過去の固定ルール（「句点完全禁止」「必ず3〜5文」「顔絵文字0〜2個」等）や旧knowledgeファイル（rules/references/training）の常時全結合を完全撤廃。
  * ユーザーが実際にアプリ内で送信した文章（`messages.sender='self'`）の実績（文量、改行テンポ、句点、絵文字、口調）を階層プロファイル（Same-Contact Recent Gold > Same-Contact All Gold > Same-Contact Silver > Global Gold > Global Profile）として学習し、プロンプトに注入。
* **同じ相手への直近手入力Gold（3〜6件）の原文提示**:
  * 統計集計値だけでなく、同じ相手に対して送った直近の手入力返信を原文ペアとして提示し、語尾・笑・改行リズムをダイレクトに模倣。
* **トーン指定の Hard Lock 化**:
  * `tame` 指定時は丁寧終止（です/ます/でした/ました/なんですね/ありますか 等）を厳格検査し、違反なら自動 repair。
* **3案誤分割（fragmentation）の完全阻止**:
  * 1つの返信が3分割されて全案に生出力全体が複製保存されるバグを根絶し、各案が独立した完成返信として個別保存。
* **全履歴二重投入の廃止 ＆ Conversation State Ledger 導入**:
  * system prompt と user message での全履歴重複を排除し、決定論的状態サマリー（既知事実・既出質問・話題）へ集約、直近ターンのみを最終タスク直前に配置。
* **不変のコア規約（上書き不可）**:
  * 架空自己開示・事実捏造の絶対禁止
  * 話者の完全分離（相手発言と自分発言の混同防止）
  * 相手呼称は必ず「さん」付け（呼び捨て・あだ名禁止）
  * 相手発言内容のオウム返し・コピー禁止
  * 出力契約（JSON形式・[AI_QUESTION] fullmatch）
  * 候補同士の重複排除

---

## 3. 技術構成

| レイヤー | 技術 / ツール | バージョン / 詳細 | ステータス |
|---|---|---|---|
| **OS** | Windows 11 (PowerShell / cmd) | - | [実装確認済み] |
| **Backend** | Python 3.12, FastAPI, Uvicorn, Pydantic v2, httpx | `backend/requirements.txt` | [実装確認済み] |
| **Database** | SQLite 3 (`data/app.db`) | WALモード, PRAGMA foreign_keys = ON | [実装確認済み] |
| **Frontend** | React 18, TypeScript, Vite 5, Tailwind CSS 3 | SPA, ルーティングなし単一画面 | [実装確認済み] |
| **AI Client** | HTTPX (OpenAI互換エンドポイント経由) | `generativelanguage.googleapis.com/v1beta/openai` | [実装確認済み] |

---

## 4. AIモデルと推論設定

* **主モデル**: `gemini-2.5-flash` / `gemini-3.5-flash-lite` [実装確認済み]
* **プロバイダ**: Google Gemini API (Google AI Studio) [実装確認済み]
* **推論パラメータ**:
  * `temperature`: 0.7 (設定により変更可) [実装確認済み]
  * `max_tokens`: 1024 (GeminiProvider内部で `MIN_MAX_TOKENS = 2048` を下限保証し思考トークン枯渇を防止) [実装確認済み]
  * `json_mode`: `candidates > 1` の場合に `response_format: {"type": "json_object"}` を適用 [実装確認済み]

---

## 5. 全体アーキテクチャとデータフロー

```text
[ユーザー操作]
  ├─ 相手メッセージ入力 → [POST /api/contacts/{id}/messages (sender='contact')]
  └─ 「3案を生成」ボタンクリック（手動トリガー） ───┐
                                                    │
                                                    ▼
[POST /api/generate]
  ├─ 1. generation_batches 作成（outcome='pending', attempt_no=1）
  ├─ 2. _build_context()
  │     ├─ 対象contactの全messages取得（LIMITなし全件参照・独立時刻行除外）
  │     ├─ learning.corpus: Turn集約、品質ラベル付与（Gold/Silver/Bronze/Negative）
  │     ├─ learning.style: 階層プロファイル学習 & 【LEARNED USER RESPONSE POLICY】構築
  │     │   └─ 【SAME-CONTACT RECENT GOLD REPLIES】（直近手入力Goldの原文提示）
  │     ├─ learning.retrieval: 2段階ランキングにより良例ペア【POSITIVE REPLY PAIRS】選定
  │     ├─ learning.contrast: manual_replaced差分から【RELEVANT CONTRAST】抽出
  │     ├─ 相手文体分析 & 20〜30%適応ブロック構築
  │     └─ prompt.build_system_prompt()（ゼロベース8ブロック、10,000文字以内）
  ├─ 3. AI推論呼び出し（Gemini API）
  ├─ 4. レスポンスパース（JSON strict parse & 誤分割・トーンHard Lock検証）
  ├─ 5. Soft Style Scoring による本命ソート
  ├─ 6. generation_history 個別レコード保存（batch_id付与）
  └─ 7. レスポンス返却（replies, history_ids, build_version, requested_tone, effective_tone）
```

---

## 6. データモデルとDBスキーマ

### generation_batches テーブル [実装確認済み]
| カラム | 型 | 説明 |
|---|---|---|
| `id` | INTEGER PRIMARY KEY AUTOINCREMENT | バッチID |
| `contact_id` | INTEGER NOT NULL | 相手ID |
| `trigger_message_id` | INTEGER NULL | 返信対象の相手メッセージID |
| `condition` | TEXT | 生成指示・条件 |
| `revision_instruction` | TEXT | 再生成時の修正指示 |
| `parent_batch_id` | INTEGER NULL | 再生成元のバッチID |
| `attempt_no` | INTEGER DEFAULT 1 | 試行回数（初回=1、再生成でインクリメント） |
| `outcome` | TEXT DEFAULT 'pending' | バッチ結果 (`pending`, `candidate_sent`, `regenerated`, `manual_replaced`, `abandoned`) |
| `selected_history_id` | INTEGER NULL | 採用送信された候補のhistory ID |
| `replacement_message_id`| INTEGER NULL | 手入力置換されたmessage ID |
| `created_at` | TEXT NOT NULL | 作成日時 (ISO 8601) |

---

## 7. 学習モジュール仕様

### 7.1. コーパス抽出・Turn集約（`corpus.py`）
* 連続する同一送信者発言を 1 つの Turn に集約。
* 独立した時刻行（`22:58` 等）のみを正規表現で除去。
* ラベル分類:
  * **Gold**: `messages.sender='self' AND source='manual'`
  * **Silver**: `messages.sender='self' AND source='generated'`（送信・採用実績あり）
  * **Bronze**: `legacy_unknown`
  * **Negative**: `rating='bad'` または除外データ

### 7.2. 階層スタイルプロファイル（`style.py`）
* 優先順位:
  1. `same_contact_recent_manual_gold` (最優先)
  2. `same_contact_all_manual_gold`
  3. `same_contact_sent_silver`
  4. `global_manual_gold`
  5. `global_profile`
* 原文実例ブロック `【SAME-CONTACT RECENT GOLD REPLIES】` をプロンプトへ自動注入。

---

## 8. ゼロベース 8ブロック プロンプト構成（`prompt.py`）

1. **【ROLE & CURRENT DIRECTIVE】/【REPLY DIRECTIVE】**: 最優先指示、トーンHard Lock
2. **【HARD INVARIANTS】/【CORE RULES】**: 事実整合、架空自己開示禁止、さん付け、カタカナ語禁止
3. **【CHAT HISTORY】/【CONTACT & CHAT HISTORY】**: 唯一の事実ソース（全履歴二重投入を廃止）
4. **【LEARNED USER RESPONSE POLICY】 & 【SAME-CONTACT RECENT GOLD REPLIES】**: 本人の実績文体
5. **【POSITIVE REPLY PAIRS】/【RETRIEVED USER REPLY PAIRS】**: 2段階検索された良例
6. **【RELEVANT CONTRAST】**: 過去の失敗差分教訓
7. **【COUNTERPART STYLE ADAPTATION】**: 相手への温度感適応（コピー禁止）
8. **【OUTPUT CONTRACT】/【FINAL TASK】**: 独立した3案JSON契約（誤分割禁止）

---

## 9. サーバー起動・プロセス管理仕様

* **起動スクリプト**: `scripts/start_server.ps1`
  * ポート 8000〜8004 に残留する古いプロセスを安全に検知・停止。
  * 最新コードで `http://127.0.0.1:8000` に単一プロセスを起動。
* **ヘルスチェックエンドポイント**: `GET /api/health`
  * 返却値: `status`, `build_version`, `prompt_version`, `pid`, `db_path`
* **診断エンドポイント**: `GET /api/learning/diagnostics`
  * 返却値: コーパス内訳（Gold/Silver/Bronze/Negative）、スタイル階層、バッチ採用率

---

## 10. テストスイートと検証実績

* **pytest バックエンドテスト**: **101 件中 101 件 合格（101 passed, 0 failed, 100% Passed）**
* **フロントエンドビルド**: `npm run build` エラーゼロ・成功
* **ランタイム実測検証**:
  * `/api/health` 実測: `build_version="learned-reply-v3.1"`, `pid=48768`
  * ポート 8000 リスナー: 1 系統のみ稼働実測確認
