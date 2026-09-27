# 開発ガイド

## 前提

- Python 3.10+ / Node.js 18+
- セットアップ: `scripts/setup.bat`（Windows）または `./scripts/setup.sh`（Mac/Linux）

## 開発サーバー

### Windows

```bat
scripts\dev.bat
```

- Backend: http://127.0.0.1:8000 （--reload）
- Frontend: http://127.0.0.1:5173 （Vite、/api はBackendへプロキシ）

### Mac / Linux

```sh
./scripts/dev.sh
```

## ビルドと本番確認

```sh
cd frontend
npm run build
```

`frontend/dist` が生成されると、Backend起動時に自動で静的配信される（`http://127.0.0.1:8000` のみで完結）。

## Backend API 一覧

| Method | Path | 説明 |
| --- | --- | --- |
| GET | /api/health | ヘルスチェック |
| GET | /api/contacts | 相手一覧（search / include_archived） |
| POST | /api/contacts | 相手作成 |
| GET / PATCH / DELETE | /api/contacts/{id} | 相手取得 / 更新 / 削除 |
| GET / POST | /api/contacts/{id}/messages | メッセージ一覧 / 追加 |
| PATCH / DELETE | /api/messages/{id} | メッセージ編集 / 削除 |
| GET | /api/contacts/{id}/export | チャット履歴をTXTでエクスポート |
| POST | /api/generate | AI返信生成・修正再生成 |
| POST | /api/generate/preview | AIへ渡される内容を確認（AIは呼ばない） |
| GET / PUT | /api/settings | 設定取得（API Key非表示）/ 更新 |
| GET / POST | /api/knowledge | ルール・参考資料・学習用の一覧 / 追加 |
| PATCH / DELETE | /api/knowledge/{id} | 有効無効切替 / 削除 |
| POST | /api/knowledge/reload | フォルダを再スキャンして追加・削除を反映 |
| GET | /api/history | 生成履歴一覧 |
| GET / PATCH | /api/history/{id} | 生成履歴取得 / 状態更新 |

APIドキュメント: Backend起動中に `http://127.0.0.1:8000/docs`（Swagger UI）

## テストの実行

```sh
.venv/Scripts/python -m pip install -r backend/requirements-dev.txt   # 初回のみ
cd backend && ../.venv/Scripts/python -m pytest tests -q
```

テストは外部AI APIへ接続せず、モックで実行される。

## テスト方針

Phaseごとに動作確認する。

- 相手管理: 作成・編集・削除・ピン留め・アーカイブ
- チャット: 相手/自分メッセージ追加・編集・削除・自動スクロール・再起動後復元
- AI: 正常生成・条件なし/付き生成・修正再生成・コピー・アプリ内送信・API Keyエラー・Rate Limit・Timeout
- ファイル: ルール/参考資料の追加・削除・再起動後読み込み・UTF-8エラー時の挙動
- 移行: フォルダコピー → setup → 起動 → データが読み込まれること

## 実装ルール

- いきなり全機能を一括実装せず、Phase単位で実装する
- 既存機能を壊さない（DB構造変更時はマイグレーションを考慮）
- API Keyをソースコード・ログ・Frontendへ出さない
- Frontendから外部AI APIを直接呼び出さない
- PC固有の絶対パスを使用しない
- 実際のマッチングアプリとの自動連携（ログイン・送信・スクレイピング）は実装しない
