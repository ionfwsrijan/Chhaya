"""Policy-as-code: the site's heat policy, its loader, and its interpreter.

Nothing Chhaya does at runtime is decided here. This package defines what
*may* be done, who may approve it, and the exact words that constitute
approval — then the engine answers yes or no, deterministically.
"""

from chhaya.policy.engine import (
    ApprovalDecision,
    DeniedReason,
    action_by_id,
    check_approval,
    required_phrase_display,
    unknown_action_error,
    verify_method_of,
)
from chhaya.policy.loader import load_policy, load_policy_text, policy_fingerprint
from chhaya.policy.models import (
    ActionSpec,
    ApprovalPolicy,
    CrewDefaults,
    EmergencyContact,
    Escalation,
    HeatPolicy,
    PolicyConfig,
    PolicyError,
    Proposals,
    Site,
    SiteSupervisor,
    Thresholds,
    VerifyMethod,
)

__all__ = [
    "ActionSpec",
    "ApprovalDecision",
    "ApprovalPolicy",
    "CrewDefaults",
    "DeniedReason",
    "EmergencyContact",
    "Escalation",
    "HeatPolicy",
    "PolicyConfig",
    "PolicyError",
    "Proposals",
    "Site",
    "SiteSupervisor",
    "Thresholds",
    "VerifyMethod",
    "action_by_id",
    "check_approval",
    "load_policy",
    "load_policy_text",
    "policy_fingerprint",
    "required_phrase_display",
    "unknown_action_error",
    "verify_method_of",
]
