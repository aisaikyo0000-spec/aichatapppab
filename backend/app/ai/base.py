"""AI Providerの抽象インターフェース。

新しいProviderを追加する場合は、AIProviderを継承して実装する。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypedDict


class ModelInfo(TypedDict, total=False):
    """モデル情報。nameは必須、tierは任意（"free" or "tier1"）。"""

    name: str
    tier: str


class AIError(Exception):
    """AI生成に関わるエラー。codeはユーザー向けメッセージの判定に使う。"""

    def __init__(self, message: str, code: str = "provider_error"):
        super().__init__(message)
        self.message = message
        self.code = code


class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    def generate(
        self,
        *,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        max_tokens: int,
        json_mode: bool = False,
    ) -> str:
        """チャット補完を実行し、テキストを返す。"""
        raise NotImplementedError

    @abstractmethod
    def available_models(self) -> list[str]:
        """利用可能なモデル名の一覧を返す。"""
        raise NotImplementedError

    def model_infos(self) -> list[ModelInfo]:
        """利用可能なモデル情報を返す。デフォルトはtier情報なし。"""
        return [{"name": m} for m in self.available_models()]
