from pathlib import Path
import argparse
import json
import sys
import pytest


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


def test_runner_can_preserve_secondary_account_label_when_it_is_the_active_key():
    config = build_gemini_benchmark_config(
        primary_key="active-secondary-key",
        model="gemini-3.5-flash-lite",
        active_account="secondary",
    )

    assert [
        (attempt["account"], attempt["model"])
        for attempt in config["quota_attempts"]
    ] == [
        ("secondary", "gemini-3.5-flash-lite"),
        ("secondary", "gemini-3.1-flash-lite"),
    ]


def test_secondary_active_account_does_not_retry_primary_after_probe_exhausted_it():
    config = build_gemini_benchmark_config(
        primary_key="active-secondary-key",
        secondary_key="other-primary-key",
        model="gemini-3.5-flash-lite",
        active_account="secondary",
    )

    assert [
        (attempt["account"], attempt["model"], attempt["api_key"])
        for attempt in config["quota_attempts"]
    ] == [
        ("secondary", "gemini-3.5-flash-lite", "active-secondary-key"),
        ("secondary", "gemini-3.1-flash-lite", "active-secondary-key"),
    ]


def test_active_account_cli_argument_defaults_to_primary_and_accepts_secondary():
    parser = argparse.ArgumentParser()
    benchmark_config.add_active_account_argument(parser)

    assert parser.parse_args([]).active_account == "primary"
    assert parser.parse_args(["--active-account", "secondary"]).active_account == "secondary"


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


@pytest.mark.parametrize(
    "api_key, model, expected_account",
    [
        ("primary-test-secret", "gemini-3.5-flash-lite", "primary"),
        ("primary-test-secret", "gemini-3.1-flash-lite", "primary"),
        ("secondary-test-secret", "gemini-3.5-flash-lite", "secondary"),
        ("secondary-test-secret", "gemini-3.1-flash-lite", "secondary"),
    ],
)
def test_successful_route_metadata_identifies_account_without_exposing_key(
    api_key, model, expected_account
):
    config = build_gemini_benchmark_config(
        primary_key="primary-test-secret",
        secondary_key="secondary-test-secret",
        model="gemini-3.5-flash-lite",
    )

    route = benchmark_config.successful_gemini_benchmark_route(
        config,
        api_key=api_key,
        model=model,
    )

    assert route == {
        "account": expected_account,
        "model": model,
    }
    route_json = json.dumps(route)
    assert "secondary-test-secret" not in route_json
    assert "primary-test-secret" not in route_json
    assert "api_key" not in route


def test_successful_route_metadata_is_absent_for_unknown_route():
    config = build_gemini_benchmark_config(
        primary_key="primary-test-secret",
        secondary_key="secondary-test-secret",
        model="gemini-3.5-flash-lite",
    )

    route = benchmark_config.successful_gemini_benchmark_route(
        config,
        api_key="unknown-secret",
        model="gemini-3.1-flash-lite",
    )

    assert route is None


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
