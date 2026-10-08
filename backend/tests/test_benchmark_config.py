from pathlib import Path
import json
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


def test_known_primary_35_quota_can_start_benchmark_at_primary_31_then_secondary():
    config = build_gemini_benchmark_config(
        primary_key="primary-test-key",
        secondary_key="secondary-test-key",
        model="gemini-3.1-flash-lite",
    )

    assert [
        (attempt["account"], attempt["model"])
        for attempt in config["quota_attempts"]
    ] == [
        ("primary", "gemini-3.1-flash-lite"),
        ("secondary", "gemini-3.5-flash-lite"),
        ("secondary", "gemini-3.1-flash-lite"),
    ]


def test_benchmark_route_state_reuses_last_success_without_persisting_api_key(tmp_path):
    route_state_path = tmp_path / "quota-route.json"
    first_config = build_gemini_benchmark_config(
        primary_key="primary-test-secret",
        secondary_key="secondary-test-secret",
        model="gemini-3.5-flash-lite",
    )

    index = benchmark_config.record_gemini_benchmark_success(
        first_config,
        api_key="secondary-test-secret",
        model="gemini-3.5-flash-lite",
        route_state_path=route_state_path,
    )

    assert index == 2
    route_state_text = route_state_path.read_text(encoding="utf-8")
    saved_route = json.loads(route_state_text)
    assert saved_route["account"] == "secondary"
    assert saved_route["model"] == "gemini-3.5-flash-lite"
    assert "test-secret" not in route_state_text
    assert "config_fingerprint" in saved_route

    next_benchmark_config = build_gemini_benchmark_config(
        primary_key="primary-test-secret",
        secondary_key="secondary-test-secret",
        model="gemini-3.5-flash-lite",
    )
    restored_index = benchmark_config.load_gemini_benchmark_route(
        next_benchmark_config, route_state_path
    )

    assert restored_index == 2
    assert next_benchmark_config["quota_attempt_start_index"] == 2


def test_benchmark_route_state_ignores_route_when_api_key_configuration_changes(tmp_path):
    route_state_path = tmp_path / "quota-route.json"
    first_config = build_gemini_benchmark_config(
        primary_key="primary-test-secret",
        secondary_key="secondary-test-secret",
        model="gemini-3.5-flash-lite",
    )
    benchmark_config.record_gemini_benchmark_success(
        first_config,
        api_key="secondary-test-secret",
        model="gemini-3.5-flash-lite",
        route_state_path=route_state_path,
    )
    changed_config = build_gemini_benchmark_config(
        primary_key="rotated-primary-secret",
        secondary_key="rotated-secondary-secret",
        model="gemini-3.5-flash-lite",
    )

    index = benchmark_config.load_gemini_benchmark_route(
        changed_config, route_state_path
    )

    assert index is None
    assert changed_config["quota_attempt_start_index"] == 0


def test_benchmark_route_state_ignores_legacy_route_without_fingerprint(tmp_path):
    route_state_path = tmp_path / "quota-route.json"
    config = build_gemini_benchmark_config(
        primary_key="primary-test-secret",
        secondary_key="secondary-test-secret",
        model="gemini-3.5-flash-lite",
    )
    route_state_path.write_text(
        json.dumps({"account": "secondary", "model": "gemini-3.5-flash-lite"}),
        encoding="utf-8",
    )

    index = benchmark_config.load_gemini_benchmark_route(config, route_state_path)

    assert index is None
    assert config["quota_attempt_start_index"] == 0


def test_benchmark_route_state_ignores_invalid_utf8(tmp_path):
    route_state_path = tmp_path / "quota-route.json"
    route_state_path.write_bytes(b"\xff\xfe")
    config = build_gemini_benchmark_config(
        primary_key="primary-test-secret",
        secondary_key="secondary-test-secret",
        model="gemini-3.5-flash-lite",
    )

    index = benchmark_config.load_gemini_benchmark_route(config, route_state_path)

    assert index is None
    assert config["quota_attempt_start_index"] == 0
