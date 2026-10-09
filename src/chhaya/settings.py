"""Environment-driven settings: one place that decides local vs AWS mode.

Every knob has a safe default, so ``make local`` needs no environment at all.
The defaults describe the offline demo: a fake hot afternoon, a JSON store in
``.chhaya/``, the scripted agent, and console notifications. AWS mode is
opt-in by setting ``CHHAYA_MODE=aws``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Settings", "load_settings"]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    """Resolved configuration for one Chhaya process."""

    mode: str
    policy_path: Path
    source: str
    recording_path: Path | None
    store_dir: Path
    agent: str
    notifier: str
    sns_topic_arn: str | None
    bedrock_model_id: str
    site_id: str | None
    aws_region: str
    dynamo_table: str

    @property
    def is_aws(self) -> bool:
        return self.mode == "aws"


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Build settings from *environ* (defaults to ``os.environ``)."""
    env = os.environ if environ is None else environ
    mode = env.get("CHHAYA_MODE", "local").strip().lower()

    policy_raw = env.get("CHHAYA_POLICY")
    policy_path = (
        Path(policy_raw) if policy_raw else _PROJECT_ROOT / "policies" / "heat_policy_v1.yaml"
    )

    recording_raw = env.get("CHHAYA_RECORDING")
    recording_path = Path(recording_raw) if recording_raw else None

    store_raw = env.get("CHHAYA_STORE_DIR")
    store_dir = Path(store_raw) if store_raw else _PROJECT_ROOT / ".chhaya"

    default_source = "fake" if mode == "local" else "openmeteo"
    default_agent = "local" if mode == "local" else "bedrock"
    default_notifier = "console" if mode == "local" else "sns"

    return Settings(
        mode=mode,
        policy_path=policy_path,
        source=env.get("CHHAYA_SOURCE", default_source).strip().lower(),
        recording_path=recording_path,
        store_dir=store_dir,
        agent=env.get("CHHAYA_AGENT", default_agent).strip().lower(),
        notifier=env.get("CHHAYA_NOTIFIER", default_notifier).strip().lower(),
        sns_topic_arn=env.get("CHHAYA_SNS_TOPIC_ARN"),
        bedrock_model_id=env.get(
            "CHHAYA_BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
        ),
        site_id=env.get("CHHAYA_SITE_ID"),
        aws_region=env.get("AWS_REGION", "ap-south-1"),
        dynamo_table=env.get("CHHAYA_DYNAMO_TABLE", "chhaya-heat"),
    )
