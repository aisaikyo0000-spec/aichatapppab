from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from benchmark_config import build_gemini_benchmark_config
import benchmark_config


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


def test_benchmark_config_exposes_ordered_quota_attempts_without_repeating_quota_models():
    config = build_gemini_benchmark_config(
        primary_key="primary-test-key",
        secondary_key="secondary-test-key",
        model="gemini-3.5-flash-lite",
    )

    assert [
        (attempt["account"], attempt["model"])
        for attempt in config["quota_attempts"]
    ] == [
        ("primary", "gemini-3.5-flash-lite"),
        ("primary", "gemini-3.1-flash-lite"),
        ("secondary", "gemini-3.5-flash-lite"),
        ("secondary", "gemini-3.1-flash-lite"),
    ]
    assert config["quota_attempt_start_index"] == 0


def test_benchmark_success_moves_next_case_to_last_successful_quota_route():
    config = build_gemini_benchmark_config(
        primary_key="primary-test-key",
        secondary_key="secondary-test-key",
        model="gemini-3.5-flash-lite",
    )

    index = benchmark_config.record_gemini_benchmark_success(
        config, api_key="primary-test-key", model="gemini-3.1-flash-lite"
    )

    assert index == 1
    assert config["quota_attempt_start_index"] == 1

    index = benchmark_config.record_gemini_benchmark_success(
        config, api_key="secondary-test-key", model="gemini-3.5-flash-lite"
    )

    assert index == 2
    assert config["quota_attempt_start_index"] == 2


def test_benchmark_success_does_not_move_state_for_unknown_route():
    config = build_gemini_benchmark_config(
        primary_key="primary-test-key",
        secondary_key="secondary-test-key",
        model="gemini-3.5-flash-lite",
    )

    index = benchmark_config.record_gemini_benchmark_success(
        config, api_key="unknown-key", model="gemini-3.1-flash-lite"
    )

    assert index is None
    assert config["quota_attempt_start_index"] == 0
