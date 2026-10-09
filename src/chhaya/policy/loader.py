"""Load a heat policy from YAML and fingerprint it for the audit trail.

The fingerprint matters: every decision Chhaya records carries the sha256 of
the policy that produced it, so months later a supervisor can prove which
policy was in force when a crew was stopped, and a court can see the policy
was not edited after the fact.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from chhaya.policy.models import HeatPolicy, PolicyError

__all__ = ["load_policy", "load_policy_text", "policy_fingerprint"]


def load_policy(path: str | Path) -> HeatPolicy:
    """Read and validate a policy file from disk."""
    policy_path = Path(path)
    try:
        text = policy_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PolicyError(f"policy file not found: {policy_path}") from None
    return load_policy_text(text, source=str(policy_path))


def load_policy_text(text: str, source: str = "<string>") -> HeatPolicy:
    """Validate raw YAML text as a heat policy.

    Raises:
        PolicyError: on any schema violation, unknown key, or unsafe
            self-contradiction (duplicate ids, duplicate phrases).
    """
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PolicyError(f"{source}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise PolicyError(f"{source}: policy must be a YAML mapping, got {type(raw).__name__}")
    try:
        return HeatPolicy.model_validate(raw)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise PolicyError(f"{source}: {problems}") from exc


def policy_fingerprint(policy: HeatPolicy) -> str:
    """Stable sha256 of the policy's canonical form.

    Two policies with identical content fingerprint identically regardless of
    key order or whitespace, so the fingerprint identifies the *rules*, not
    the file's formatting.
    """
    canonical = json.dumps(policy.model_dump(mode="json"), indent=2, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
