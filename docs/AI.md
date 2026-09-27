# AI設計

## Provider抽象化

`AIProvider`（`backend/app/ai/base.py`）をインターフェースとし、Provider固有処理を分離する。

```python
class AIProvider(ABC):
    name: str
    def generate(self, *, model, messages, temperature, max_tokens, json_mode=False) -> str: ...
    def available_models(self) -> list[str]: ...
```

- Frontendから外部AI APIを直接呼び出さない（必ず `POST /api/generate` 経由）
- 新しいProviderは `factory.py` の `_REGISTRY` に追加するだけで利用可能

### 現在のProvider

| Provider | モデル |
| --- | --- |
| cerebras | gpt-oss-120b（デフォルト）, gpt-oss-20b, llama-3.3-70b |

## 設定の優先順位

```
設定画面（SQLite settings） > .env > デフォルト値
```

- `ai_provider`: cerebras
- `ai_model`: gpt-oss-120b
- `api_key`: 設定画面の入力値 > `CEREBRAS_API_KEY`
- `ai_temperature`: 0.8
- `ai_max_tokens`: 512
- `ai_history_limit`: 50（AIへ送信する直近メッセージ数）

## プロンプト構造

生成時にBackendで以下を組み立ててAIへ渡す。

```
SYSTEM              AIの役割・基本方針
SELF                あなた（ユーザー）のプロフィール（user_profile テーブル）
RULES               絶対に守るルール（knowledge/rules/）
CURRENT REQUEST     今回だけの追加条件
CONTACT             相手の名前・プロフィール
CHAT HISTORY        直近の会話（履歴送信件数で制限）
REFERENCES          マッチングアプリに関する参考資料（knowledge/references/）
TRAINING EXAMPLES   過去の良い返信例（training_examples テーブル）
LEARNING MATERIALS  学習用テキスト（knowledge/training/）
TASK                現在の会話に対する返信を作成
```

- 【SELF】はAIが演じるユーザー自身のプロフィール。この人物になりきって返信を作成する

- RULES は必ず守る命令、REFERENCES は判断材料として扱う（矛盾時はRULES優先）
- 会話履歴は無制限に送らず、直近N件（デフォルト50）に制限する
- 3案生成時は `response_format: json_object` で `{"replies": [...]}` を要求する

## 修正して再生成

`POST /api/generate` に `revision_instruction` と `original_generated` を渡すと、
元の生成結果＋修正指示を会話として送り、修正後の文章を生成する。

## エラー処理

AIErrorにコードを持たせ、ユーザー向けに分かりやすい日本語メッセージを返す。

| code | メッセージ例 |
| --- | --- |
| api_key_missing | API Keyが設定されていません。設定画面から登録してください |
| invalid_api_key | API Keyが正しくありません |
| rate_limit | APIのレート制限に達しました |
| timeout | 応答がタイムアウトしました |
| model_not_found | モデルが見つかりません |
| empty_response | 空のレスポンス |
| network_error | 通信に失敗しました |
| provider_error | APIエラー |

内部のStack Traceは通常画面へ表示しない。詳細は `logs/app.log` へ記録する（API Keyは記録しない）。

## 将来の拡張（Phase 4）

- 相手ごとのAI設定上書き（Provider/Model/Rules/References）
- Embedding / RAGによる過去返信の自動検索
- 古い会話の要約
- Vision対応（画像説明の自動生成）
- Fine-tuning（データ構造は training_examples で対応済み）
