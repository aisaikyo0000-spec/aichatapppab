"""Make one minimal, Tapple-mode Gemini request without printing credentials."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai.base import AIError  # noqa: E402
from app.ai.gemini import GeminiProvider  # noqa: E402
from app.ai.prompt import build_initial_generation_messages  # noqa: E402
from api_key_file import read_gemini_api_key  # noqa: E402


def _make_probe_provider(api_key: str) -> GeminiProvider:
    """Create a provider that cannot retry and spend more than one probe per model."""
    provider = GeminiProvider(api_key)
    provider.MAX_RETRIES = 1
    return provider


def _probe(provider: GeminiProvider, model: str) -> bool:
    messages = build_initial_generation_messages(
        system_prompt="自然で送信可能な日本語返信を1件だけ生成してください。",
        chat_history_text="相手: 休日はよくカフェに行きます。おすすめありますか？",
        candidates=1,
        strategy_mode="tapple",
    )
    raw = provider.generate(
        model=model,
        messages=messages,
        temperature=0.0,
        max_tokens=128,
        json_mode=True,
    )
    payload = json.loads(raw)
    replies = payload.get("replies") if isinstance(payload, dict) else None
    return isinstance(replies, list) and len(replies) == 1 and isinstance(replies[0], str) and bool(replies[0].strip())


def _probe_models(provider: GeminiProvider, models: tuple[str, str]) -> tuple[str | None, str | None]:
    for model in models:
        try:
            if _probe(provider, model):
                return model, None
            return None, "invalid_probe_shape"
        except AIError as exc:
            if exc.code != "rate_limit":
                return None, exc.code
        except (json.JSONDecodeError, ValueError, TypeError):
            return None, "invalid_probe_response"
    return None, "rate_limit"


def _select_available_model(
    primary_provider: GeminiProvider,
    secondary_provider: GeminiProvider | None,
    primary_model: str,
    fallback_model: str,
) -> tuple[str | None, str, str | None]:
    model_order = (primary_model, fallback_model)
    model, error = _probe_models(primary_provider, model_order)
    if model:
        return model, "primary", None
    if error != "rate_limit":
        return None, "primary", error
    if secondary_provider is None or secondary_provider.api_key == primary_provider.api_key:
        return None, "primary", "rate_limit"

    model, error = _probe_models(secondary_provider, model_order)
    if model:
        return model, "secondary", None
    return None, "secondary", error


def main() -> int:
    parser = argparse.ArgumentParser(description="one-request Tapple-mode API connectivity check")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--secondary-env-file", type=Path)
    parser.add_argument("--primary-model", default="gemini-3.5-flash-lite")
    parser.add_argument("--fallback-model", default="gemini-3.1-flash-lite")
    args = parser.parse_args()

    api_key = read_gemini_api_key(args.env_file)
    if not api_key:
        print("FAIL account=primary code=api_key_missing")
        return 2

    provider = _make_probe_provider(api_key)
    secondary_api_key = read_gemini_api_key(args.secondary_env_file) if args.secondary_env_file else ""
    secondary_provider = _make_probe_provider(secondary_api_key) if secondary_api_key else None
    model, account, error = _select_available_model(
        provider,
        secondary_provider,
        args.primary_model,
        args.fallback_model,
    )
    if model:
        print(f"PASS model={model} account={account}")
        return 0
    print(f"FAIL account={account} code={error or 'provider_error'}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
