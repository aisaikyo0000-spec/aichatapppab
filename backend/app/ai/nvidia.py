"""NVIDIA NIM API用のAI Provider実装。

build.nvidia.com の無料エンドポイント（OpenAI互換API）を利用する。
API Keyはソースコードへ埋め込まず、コンストラクタで受け取る。
ログへAPI Keyを出力しない。
"""
from __future__ import annotations

import httpx

from .base import AIError, AIProvider

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_TIMEOUT = 90.0


class NVIDIAProvider(AIProvider):
    name = "nvidia"

    # 推論モデル（nemotron-3.5-lightning）は巨大なシステムプロンプトだと
    # 推論がcontentに漏れて返信が返らないため、リストから除外している。
    # Nanoは推論をreasoning_contentに分離し、9秒前後で正常に返信する。
    MODELS = [
        "nvidia/nemotron-3-nano-30b-a3b",
        "meta/llama-3.3-70b-instruct",
    ]

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
                "NVIDIA API Keyが設定されていません。build.nvidia.com からAPI Keyを取得して設定してください。",
                code="api_key_missing",
            )

        # NVIDIAの推論モデルは推論(reasoning)トークンで消費が大きい。
        # max_tokensが小さいと推論が途中で切れ、推論トレースがcontentに漏れて
        # 返信が返らなくなるため、最低でも4096トークンを確保する。
        effective_max_tokens = max(int(max_tokens or 0), 4096)

        payload: dict = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": effective_max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        headers = {"Authorization": f"Bearer {self.api_key}"}

        try:
            with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
                resp = client.post(
                    f"{NVIDIA_BASE_URL}/chat/completions",
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
            finish_reason = data["choices"][0].get("finish_reason")
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
        # 推論トレースの漏れガード: 英語の分析文で始まり長文の場合は、
        # 推論トレースがcontentに漏れたと判断し、再試行させる。
        # （推論トレースはユーザーの日本語メッセージを引用するため、
        #   日本語判定では検出できない。英語の文頭+長文がシグナル）
        if _is_reasoning_leak(content):
            raise AIError(
                "AI APIから推論トレースが返されました。再試行してください。",
                code="empty_response",
            )
        return content.strip()


def _is_reasoning_leak(content: str) -> bool:
    """contentが推論トレースの漏れかどうかを判定する。"""
    text = content.lstrip()
    if not text:
        return False
    head = text[:30]
    # 先頭がASCII英字で始まる = 英語の分析文（推論漏れ）。日本語の返信は
    # 必ず日本語（かな・漢字・絵文字）で始まる。
    first = head[:1]
    if not first.isascii() or not first.isalpha():
        return False
    # 推論トレースは長文になる（正常な返信は短文）
    return len(text) > 300
