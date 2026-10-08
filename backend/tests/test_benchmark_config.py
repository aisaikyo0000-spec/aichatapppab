from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_config import build_gemini_benchmark_config


def test_benchmark_config_carries_secondary_key_after_primary_model_fallback():
    config = build_gemini_benchmark_config(
        primary_key="primary-test-key",
        secondary_key="secondary-test-key",
        model="gemini-3.5-flash-lite",
    )

    assert config["api_key"] == "primary-test-key"
    assert config["fallback_model"] == "gemini-3.1-flash-lite"
    assert config["fallback_api_key"] == "primary-test-key"
    assert config["secondary_api_key"] == "secondary-test-key"


def test_benchmark_config_keeps_secondary_key_optional():
    config = build_gemini_benchmark_config(
        primary_key="primary-test-key",
        secondary_key="",
        model="gemini-3.1-flash-lite",
    )

    assert config["model"] == "gemini-3.1-flash-lite"
    assert config["secondary_api_key"] == ""
    assert "fallback_model" not in config
