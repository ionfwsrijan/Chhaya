"""Tests for settings resolution and the runtime composition root."""

from __future__ import annotations

from pathlib import Path

import pytest

from chhaya.agent import BedrockAgent, LocalAgent
from chhaya.notify import ConsoleNotifier, LogNotifier, SnsNotifier
from chhaya.runtime import build_runtime
from chhaya.settings import load_settings
from chhaya.sources import FakeSource, RecordedSource


class TestSettings:
    def test_local_defaults(self) -> None:
        settings = load_settings({})
        assert settings.mode == "local"
        assert settings.source == "fake"
        assert settings.agent == "local"
        assert settings.notifier == "console"
        assert settings.dynamo_table == "chhaya-heat"
        assert not settings.is_aws

    def test_aws_defaults_flip_the_backends(self) -> None:
        settings = load_settings({"CHHAYA_MODE": "aws"})
        assert settings.is_aws
        assert settings.source == "openmeteo"
        assert settings.agent == "bedrock"
        assert settings.notifier == "sns"

    def test_env_overrides_win(self) -> None:
        settings = load_settings(
            {
                "CHHAYA_SOURCE": "recorded",
                "CHHAYA_RECORDING": "demo/peak.json",
                "CHHAYA_NOTIFIER": "log",
                "CHHAYA_DYNAMO_TABLE": "other-table",
                "AWS_REGION": "us-east-1",
            }
        )
        assert settings.source == "recorded"
        assert settings.recording_path == Path("demo/peak.json")
        assert settings.notifier == "log"
        assert settings.dynamo_table == "other-table"
        assert settings.aws_region == "us-east-1"


class TestRuntimeLocal:
    def test_builds_the_offline_stack(self, tmp_path: Path) -> None:
        settings = load_settings({"CHHAYA_STORE_DIR": str(tmp_path / "store")})
        runtime = build_runtime(settings)
        assert isinstance(runtime.source, FakeSource)
        assert isinstance(runtime.agent, LocalAgent)
        assert isinstance(runtime.notifier, ConsoleNotifier)
        assert runtime.settings.mode == "local"

    def test_describe_names_every_choice(self, tmp_path: Path) -> None:
        settings = load_settings({"CHHAYA_STORE_DIR": str(tmp_path / "store")})
        described = build_runtime(settings).describe()
        assert described["mode"] == "local"
        assert described["site_id"] == "hyderabad-miyapur-site-b"
        assert described["dry_run"] is True
        assert described["agent"] == "LocalAgent"
        assert described["weather_source"] == "fake"
        assert len(str(described["policy_fingerprint"])) == 64

    def test_log_notifier_uses_the_store_dir(self, tmp_path: Path) -> None:
        store_dir = tmp_path / "store"
        settings = load_settings({"CHHAYA_NOTIFIER": "log", "CHHAYA_STORE_DIR": str(store_dir)})
        runtime = build_runtime(settings)
        assert isinstance(runtime.notifier, LogNotifier)

    def test_unknown_source_is_refused(self, tmp_path: Path) -> None:
        settings = load_settings(
            {"CHHAYA_SOURCE": "telepathy", "CHHAYA_STORE_DIR": str(tmp_path / "store")}
        )
        with pytest.raises(ValueError, match="unknown CHHAYA_SOURCE"):
            build_runtime(settings)

    def test_unknown_agent_is_refused(self, tmp_path: Path) -> None:
        settings = load_settings(
            {"CHHAYA_AGENT": "crystal-ball", "CHHAYA_STORE_DIR": str(tmp_path / "store")}
        )
        with pytest.raises(ValueError, match="unknown CHHAYA_AGENT"):
            build_runtime(settings)

    def test_recorded_requires_a_path(self, tmp_path: Path) -> None:
        settings = load_settings(
            {"CHHAYA_SOURCE": "recorded", "CHHAYA_STORE_DIR": str(tmp_path / "store")}
        )
        with pytest.raises(ValueError, match="CHHAYA_RECORDING"):
            build_runtime(settings)

    def test_sns_requires_a_topic(self, tmp_path: Path) -> None:
        settings = load_settings(
            {"CHHAYA_NOTIFIER": "sns", "CHHAYA_STORE_DIR": str(tmp_path / "store")}
        )
        with pytest.raises(ValueError, match="CHHAYA_SNS_TOPIC_ARN"):
            build_runtime(settings)

    def test_recorded_source_with_a_file(self, tmp_path: Path) -> None:
        import json

        recording = tmp_path / "rec.json"
        recording.write_text(
            json.dumps(
                {
                    "hyderabad-miyapur-site-b": [
                        {
                            "time": "2026-05-18T14:00",
                            "temperature_2m": 38.0,
                            "relative_humidity_2m": 50.0,
                            "wind_speed_10m": 2.0,
                            "shortwave_radiation": 800.0,
                            "cloud_cover": 20.0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        settings = load_settings(
            {
                "CHHAYA_SOURCE": "recorded",
                "CHHAYA_RECORDING": str(recording),
                "CHHAYA_STORE_DIR": str(tmp_path / "store"),
            }
        )
        runtime = build_runtime(settings)
        assert isinstance(runtime.source, RecordedSource)

    def test_bedrock_agent_selection(self, tmp_path: Path) -> None:
        settings = load_settings(
            {"CHHAYA_AGENT": "bedrock", "CHHAYA_STORE_DIR": str(tmp_path / "store")}
        )
        assert isinstance(build_runtime(settings).agent, BedrockAgent)

    def test_sns_notifier_selection(self, tmp_path: Path) -> None:
        settings = load_settings(
            {
                "CHHAYA_NOTIFIER": "sns",
                "CHHAYA_SNS_TOPIC_ARN": "arn:aws:sns:ap-south-1:1:heat",
                "CHHAYA_STORE_DIR": str(tmp_path / "store"),
            }
        )
        assert isinstance(build_runtime(settings).notifier, SnsNotifier)
