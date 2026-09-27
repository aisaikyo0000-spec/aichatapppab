# データベース

SQLite（`data/app.db`）を中心にデータを管理する。WALモードで運用し、起動時にスキーマを自動作成する。

## テーブル

### contacts

相手の基本情報。

| カラム | 型 | 説明 |
| --- | --- | --- |
| id | INTEGER PK | |
| name | TEXT | 名前（必須） |
| profile | TEXT | 自由記述 |
| is_pinned | INTEGER | ピン留め（0/1） |
| is_archived | INTEGER | アーカイブ（0/1） |
| created_at / updated_at | TEXT | ISO8601 (UTC) |

### messages

チャットメッセージ。

| カラム | 型 | 説明 |
| --- | --- | --- |
| id | INTEGER PK | |
| contact_id | INTEGER FK→contacts | ON DELETE CASCADE |
| sender | TEXT | `contact` または `self` |
| content | TEXT | 本文 |
| created_at / updated_at | TEXT | |

### contact_images

相手のプロフィール画像（Phase 2で利用）。

| カラム | 型 | 説明 |
| --- | --- | --- |
| id | INTEGER PK | |
| contact_id | INTEGER FK→contacts | ON DELETE CASCADE |
| file_path | TEXT | プロジェクトルート基準の相対パス |
| description | TEXT | 画像説明 |
| sort_order | INTEGER | 表示順 |
| created_at | TEXT | |

### generation_history

AI生成履歴。過去の生成結果の確認・復元に利用。

| カラム | 型 | 説明 |
| --- | --- | --- |
| id | INTEGER PK | |
| contact_id | INTEGER FK→contacts | ON DELETE SET NULL |
| provider / model | TEXT | 生成に使ったProvider/モデル |
| current_condition | TEXT | 今回の返信条件 |
| generated_text | TEXT | 生成結果（修正時は元の文章） |
| revision_instruction | TEXT | 修正指示 |
| revised_text | TEXT | 修正後の文章 |
| is_adopted / is_copied / is_sent | INTEGER | 採用/コピー/送信状態 |
| created_at | TEXT | |

### training_examples

学習データ（Phase 3）。将来のFew-shot / RAG / Fine-tuning用に構造化して保存。

| カラム | 型 | 説明 |
| --- | --- | --- |
| id | INTEGER PK | |
| conversation | TEXT | 会話 |
| ai_response | TEXT | AI生成文章 |
| user_feedback | TEXT | ユーザー評価・フィードバック |
| corrected_response | TEXT | ユーザー修正版 |
| rating | INTEGER | 評価 |
| created_at | TEXT | |

### training_sessions

AI練習セッション（Phase 3）。

| カラム | 型 | 説明 |
| --- | --- | --- |
| id | INTEGER PK | |
| contact_id | INTEGER FK | |
| messages | TEXT | JSON配列 |
| created_at / updated_at | TEXT | |

### user_profile

ユーザー自身（AIが演じる主人公）のプロフィール。単一行（id=1）で管理。生成時に【SELF】としてAIへ渡される。

| カラム | 型 | 説明 |
| --- | --- | --- |
| id | INTEGER PK | 常に1 |
| name | TEXT | 名前（ニックネーム） |
| gender / age | TEXT | 性別 / 年齢（任意） |
| occupation | TEXT | 職業 |
| hobbies | TEXT | 趣味 |
| personality | TEXT | 性格 |
| speaking_style | TEXT | 話し方・文体 |
| profile | TEXT | 自由記述 |
| updated_at | TEXT | |

### providers

利用可能なAI Provider。

| カラム | 型 | 説明 |
| --- | --- | --- |
| id | INTEGER PK | |
| name | TEXT UNIQUE | `cerebras` 等 |
| is_enabled | INTEGER | |
| created_at | TEXT | |

### settings

キー・バリュー形式の設定（API Key等）。値は平文でSQLiteに保存される（Git管理外のローカルDBのため）。

| カラム | 型 | 説明 |
| --- | --- | --- |
| key | TEXT PK | 設定キー |
| value | TEXT | 設定値 |

主なキー: `ai_provider`, `ai_model`, `api_key`, `ai_temperature`, `ai_max_tokens`, `ai_history_limit`

### knowledge_files

ルール・参考資料ファイルの管理レコード（実体は `knowledge/rules/`, `knowledge/references/` 配下のTXT）。

| カラム | 型 | 説明 |
| --- | --- | --- |
| id | INTEGER PK | |
| type | TEXT | `rules` または `references` |
| file_name | TEXT | ファイル名 |
| file_path | TEXT | 絶対パス（起動時に自動登録） |
| enabled | INTEGER | 有効/無効 |
| created_at / updated_at | TEXT | |

### contact_settings

相手ごとのAI設定の上書き。値がNULLの項目は全体設定を使う。

| カラム | 型 | 説明 |
| --- | --- | --- |
| contact_id | INTEGER PK FK→contacts | ON DELETE CASCADE |
| provider / model | TEXT | NULLなら全体設定 |
| temperature / max_tokens / history_limit | REAL / INTEGER | NULLなら全体設定 |
| updated_at | TEXT | |

### contact_knowledge_files

相手ごとに使うルール・参考資料の選択（複合PK）。未選択なら全体設定の有効ファイルを使う。

| カラム | 型 | 説明 |
| --- | --- | --- |
| contact_id | INTEGER FK→contacts | ON DELETE CASCADE |
| knowledge_file_id | INTEGER FK→knowledge_files | ON DELETE CASCADE |

## 方針

- 起動時に `CREATE TABLE IF NOT EXISTS` で自動マイグレーション
- スキーマ変更が必要になった場合は、既存データを壊さないマイグレーションを追加する
- チャット履歴の内部保存形式はSQLiteが正であり、TXTエクスポートはあくまで出力用
