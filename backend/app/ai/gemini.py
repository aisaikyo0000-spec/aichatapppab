"""Gemini API（Google AI Studio）用のAI Provider実装。

OpenAI互換エンドポイント（generativelanguage.googleapis.com/v1beta/openai）を利用する。
API Keyはソースコードへ埋め込まず、コンストラクタで受け取る。
ログへAPI Keyを出力しない。

無料枠（クレカ登録不要）でも Flash 系モデルが使える。
AI Studio でAPI Key を発行する: https://aistudio.google.com/apikey
"""
from __future__ import annotations

import logging
import time

import httpx

from .base import AIError, AIProvider, ModelInfo

logger = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
DEFAULT_TIMEOUT = 90.0

# 推論(thinking)が出力トークンを消費するため、最低限の出力を確保する。
MIN_MAX_TOKENS = 2048
QUOTA_FALLBACK_MODELS = frozenset(
    {"gemini-3.5-flash-lite", "gemini-3.1-flash-lite"}
)


class GeminiProvider(AIProvider):
    name = "gemini"

    # モデル一覧。tier: "free"=無料枠、"tier1"=有料プラン（クレカ登録が必要）。
    # gemini-2.5系は新規アカウントでは利用不可になったため含めない。
    # 先頭がデフォルト推奨（日本語チャット生成の品質と速度のバランス）。
    MODELS = [
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.1-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
        "gemini-3.1-pro-preview",
    ]

    MODEL_TIERS: dict[str, str] = {
        "gemini-3.5-flash": "free",
        "gemini-3.5-flash-lite": "free",
        "gemini-3.1-flash-lite": "free",
        "gemini-3.6-flash": "free",
        "gemini-3.7-flash": "free",
        "gemini-3.1-pro-preview": "tier1",
    }

    # レート制限時のリトライ設定
    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 5.0  # 秒

    def __init__(self, api_key: str):
        self.api_key = (api_key or "").strip()

    def available_models(self) -> list[str]:
        return list(self.MODELS)

    def model_infos(self) -> list[ModelInfo]:
        all_models: list[ModelInfo] = []
        for m in self.MODELS:
            tier = self.MODEL_TIERS.get(m, "free")
            all_models.append({"name": m, "tier": tier})
        return all_models

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
                "Gemini API Keyが設定されていません。Google AI Studio（aistudio.google.com）から無料で取得できます。",
                code="api_key_missing",
            )

        # Geminiはthinkingトークンも出力トークンに含まれるため、
        # max_tokensが小さいと思考で使い切って返信が空になることがある。
        effective_max_tokens = max(int(max_tokens or 0), MIN_MAX_TOKENS)

        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": effective_max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {self.api_key}"}

        for attempt in range(self.MAX_RETRIES):
            try:
                resp = self._send_request(headers, payload)
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

            body_text = resp.text or ""
            # Response bodies may include prompts, personal messages, or generated replies.
            logger.info("Gemini API response: status=%d", resp.status_code)

            try:
                return self._parse_response(resp, body_text)
            except AIError as e:
                # The production 3.5 Flash Lite -> 3.1 Flash Lite route switches
                # models/accounts as soon as quota is exhausted. Repeating
                # either quota-chain model only delays fallback and spends RPD.
                if (
                    e.code == "rate_limit"
                    and model not in QUOTA_FALLBACK_MODELS
                    and attempt < self.MAX_RETRIES - 1
                ):
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    logger.info("Rate limit hit, retrying in %.1f s (attempt %d/%d)", delay, attempt + 1, self.MAX_RETRIES)
                    time.sleep(delay)
                    continue
                raise

        # 到達不能だが型エラー防止
        raise AIError("リトライ回数の上限に達しました。", code="provider_error")

    def _send_request(self, headers: dict, payload: dict) -> httpx.Response:
        """APIリクエストを送信する。"""
        with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
            return client.post(
                f"{GEMINI_BASE_URL}/chat/completions",
                headers=headers,
                json=payload,
            )

    def _parse_response(self, resp: httpx.Response, body_text: str) -> str:
        """APIレスポンスを解析し、テキストを返す。エラー場合はAIErrorを送出。"""
        body_lower = body_text.lower()
        if resp.status_code == 401 or (
            resp.status_code == 400
            and ("api key" in body_lower or "invalid auth key" in body_lower)
        ):
            raise AIError(
                "API Keyが正しくありません。Google AI Studioで発行したGemini API Keyを確認してください。",
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
            # レート制限エラーが本文に含まれる場合がある
            if "rate" in body_text.lower() or "limit" in body_text.lower() or "quota" in body_text.lower():
                raise AIError(
                    "APIのレート制限に達しました。少し時間をおいて再試行してください。",
                    code="rate_limit",
                )
            raise AIError(
                f"AI APIエラーが発生しました。(HTTP {resp.status_code})",
                code="provider_error",
            )

        data = resp.json()

        # レスポンス本文にレート制限エラーが含まれる場合
        error_obj = data.get("error", {})
        if error_obj:
            error_msg = error_obj.get("message", "")
            error_status = error_obj.get("status", "")
            if "rate" in error_msg.lower() or "limit" in error_msg.lower() or "quota" in error_msg.lower() or error_status == "RESOURCE_EXHAUSTED":
                raise AIError(
                    "APIのレート制限に達しました。少し時間をおいて再試行してください。",
                    code="rate_limit",
                )
            if error_msg:
                raise AIError("Gemini APIからエラーが返されました。", code="provider_error")

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
