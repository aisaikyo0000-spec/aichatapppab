"""Cerebras API用のAI Provider実装。

API Keyはソースコードへ埋め込まず、コンストラクタで受け取る。
ログへAPI Keyを出力しない。
"""
from __future__ import annotations

import httpx

from .base import AIError, AIProvider

CEREBRAS_BASE_URL = "https://api.cerebras.ai/v1"
DEFAULT_TIMEOUT = 90.0


class CerebrasProvider(AIProvider):
    name = "cerebras"

    # 現在Cerebrasで提供されているモデルのみ（2026-08時点）
    MODELS = ["gpt-oss-120b", "zai-glm-4.7", "gemma-4-31b"]

    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()

    def available_models(self) -> list[str]:
        return list(self.MODELS)

    def generate(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        if not self.api_key:
            raise AIError(
                "Cerebras API Keyが設定されていません。設定画面からAPI Keyを登録してください。",
                code="api_key_missing",
            )

        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                resp = client.post(
                    f"{CEREBRAS_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException:
            raise AIError(
                "AI APIの応答がタイムアウトしました。時間をおいて再試行してください。",
                code="timeout",
            )
        except httpx.RequestError:
            raise AIError(
                "AI APIへの通信に失敗しました。ネットワーク接続を確認してください。",
                code="network_error",
            )

        if resp.status_code == 401:
            raise AIError(
                "API Keyが正しくありません。設定画面からAPI Keyを確認してください。",
                code="invalid_api_key",
            )
        if resp.status_code == 429:
            raise AIError(
                "APIのレート制限に達しました。少し時間をおいて再試行してください。",
                code="rate_limit",
            )
        if resp.status_code == 404:
            raise AIError(
                "指定したモデルが見つかりませんでした。設定画面でモデル名を確認してください。",
                code="model_not_found",
            )
        if resp.status_code >= 400:
            raise AIError(
                f"AI APIエラーが発生しました。(HTTP {resp.status_code})",
                code="provider_error",
            )

        data = resp.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise AIError(
                "AI APIから空のレスポンスが返されました。再試行してください。",
                code="empty_response",
            )
        if not content or not content.strip():
            raise AIError(
                "AI APIから空のレスポンスが返されました。再試行してください。",
                code="empty_response",
            )
        return content.strip()
