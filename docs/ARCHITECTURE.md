# アーキテクチャ

## 概要

個人用のローカルWebアプリ。ブラウザからlocalhostのBackendへアクセスする。

- 外部AI APIとの通信はすべてBackendから行う
- API KeyはFrontendへ渡さない
- プロジェクトフォルダ単体で別PCへコピー可能（絶対パス不使用、データはプロジェクトルート基準）

## 構成

```
Frontend (React/Vite, :5173 dev)
   │  /api プロキシ（開発時）または同一オリジン（本番ビルド時）
   ▼
Backend (FastAPI, :8000)
   ├── routers/       # HTTP API
   ├── ai/            # AI Provider抽象化・プロンプト組み立て
   └── database.py    # SQLiteアクセス
   ▼
SQLite (data/app.db) ＋ ファイル保存（knowledge/, data/）
```

本番利用時は `frontend/dist` をBackendが静的配信するため、Backendのポート1つで完結する。

## データフロー

```
相手メッセージ入力 (POST /api/contacts/{id}/messages)
      │
      ▼
返信生成 (POST /api/generate)
      │  1. ルール・参考資料・相手情報・会話履歴を組み立て
      │  2. AIProvider.generate() を呼ぶ
      │  3. 生成履歴をSQLiteへ記録
      ▼
結果表示 → コピー / 送信（本アプリ内チャットへ追加） / 修正して再生成
```

## セキュリティ方針

- API KeyはBackendのみが保持（設定画面からの登録はSQLite settings テーブル、または .env）
- API Keyはログへ出力しない。Frontendへ返さない（`has_api_key` のみ返す）
- 外部サービスとの通信はBackendからのみ
- ファイルパスはユーザー入力から直接信頼しない（ファイル名はbasenameに正規化）

## ディレクトリ構成

```
backend/app/
├── main.py          # FastAPIエントリポイント・静的配信
├── config.py        # パス解決（プロジェクトルート基準）・ログ設定
├── database.py      # SQLiteスキーマ・接続・settingsストア
├── schemas.py       # Pydanticスキーマ
├── routers/
│   ├── contacts.py    # 相手CRUD・ピン留め・アーカイブ
│   ├── messages.py    # メッセージCRUD・チャットエクスポート
│   ├── generation.py  # AI返信生成・修正再生成
│   ├── settings.py    # 設定取得・更新
│   ├── knowledge.py   # ルール/参考資料ファイル管理
│   └── history.py     # 生成履歴
└── ai/
    ├── base.py        # AIProvider抽象インターフェース・AIError
    ├── cerebras.py    # Cerebras実装
    ├── factory.py     # Providerファクトリ
    ├── config.py      # 生成設定の解決（DB設定 > .env > デフォルト）
    └── prompt.py      # プロンプト組み立て・知識ファイル読み込み
```
