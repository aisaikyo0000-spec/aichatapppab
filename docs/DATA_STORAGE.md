# データ保存

## 基本方針

- すべてのアプリデータは**プロジェクトルートを基準**として保存する
- PC固有のユーザー名・絶対パスをコードへ埋め込まない
- プロジェクトフォルダをコピーするだけで別PCへ移行できる

## 保存先

| パス | 内容 |
| --- | --- |
| `data/app.db` | SQLite（相手・チャット・生成履歴・設定など主要データ） |
| `data/contacts/` | プロフィール画像（Phase 2） |
| `data/chats/` | チャット関連ファイル（必要時） |
| `data/generation_history/` | 生成履歴のファイル出力（必要時） |
| `data/backups/` | バックアップ（Phase 2） |
| `knowledge/rules/` | 絶対ルール（TXT、複数可） |
| `knowledge/references/` | 参考資料（TXT、複数可） |
| `knowledge/training/` | 学習用テキスト（TXT、複数可） |
| `training/examples/` | 学習データ（Phase 3） |
| `training/sessions/` | AI練習セッション（Phase 3） |
| `config/` | 初期設定ファイル |
| `logs/` | 開発用ログ |

## Git管理

- `data/`, `config/`, `.env`, `node_modules/`, `.venv/`, `frontend/dist/` はGit管理対象外（`.gitignore`）
- `knowledge/`, `training/` は共有可能なデータのためGit管理対象

## ルール・参考資料

- ルール/参考資料/学習用はテキストファイルとして `knowledge/` 配下（`rules/` `references/` `training/`）に保存
- アプリ起動時に自動スキャンして `knowledge_files` テーブルへ登録する
- 設定画面の「フォルダから再読み込み」でいつでも再スキャンできる（新規追加・削除の両方を反映）
- ファイルの実体はTXTだが、内部管理はSQLiteのレコードが正（TXTはユーザーが外部で作成・編集しやすい形式）
- 読み込み失敗（UTF-8エラー等）のファイルは無視して生成を継続する

## バックアップ（Phase 2予定）

`data/backups/` に自動バックアップを作成し、設定画面から作成・復元できるようにする。

## 移行手順

1. プロジェクトフォルダをコピー
2. 移行先で `scripts/setup.bat`（または `setup.sh`）を実行
3. `scripts/start.bat`（または `start.sh`）で起動
4. API Keyは `.env` または設定画面で再設定
