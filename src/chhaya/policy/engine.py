"""The consent interpreter: a deterministic yes/no on every proposed action.

This module answers one question — *may this action proceed, given this
policy, this approver, and this raw message?* — the same way every time, with
no model in the loop. A language model may propose the action and may
transcribe the supervisor's words, but the words are checked here as a plain
string comparison against the phrase the policy demands.

The three refusal reasons are kept distinct so the audit trail says *why* an
action was denied: the approver lacked the role, the phrase did not match, or
the action itself is not on the allowlist.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from chhaya.policy.models import ActionSpec, HeatPolicy, PolicyError, VerifyMethod

__all__ = [
    "ApprovalDecision",
    "DeniedReason",
    "action_by_id",
    "check_approval",
    "required_phrase_display",
]


class DeniedReason:
    """Stable refusal codes, kept machine-readable for the audit trail."""

    UNKNOWN_ACTION = "unknown_action"
    MISSING_ROLE = "approver_lacks_role"
    PHRASE_MISMATCH = "approval_phrase_mismatch"
    NOT_STANDING = "action_does_not_allow_standing_advisory"


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    """The result of checking one proposed action against one message.

    Attributes:
        allowed: True only when every policy rule passed.
        reason: One of the :class:`DeniedReason` codes, or ``"approved"``.
        action_id: The action that was checked.
        approver_roles: The roles the approver claimed.
        phrase_required: The exact phrase the policy demands.
        phrase_matched: Whether the raw message contained that phrase.
        explain: One sentence a human can read, never the basis for the
            decision itself (the decision is the checks above).
    """

    allowed: bool
    reason: str
    action_id: str
    approver_roles: tuple[str, ...]
    phrase_required: str
    phrase_matched: bool
    explain: str

    @property
    def approved(self) -> bool:
        return self.allowed

    def as_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "action_id": self.action_id,
            "approver_roles": list(self.approver_roles),
            "phrase_required": self.phrase_required,
            "phrase_matched": self.phrase_matched,
            "explain": self.explain,
        }


def action_by_id(policy: HeatPolicy, action_id: str) -> ActionSpec:
    """The action spec for ``action_id``, or a refusal for anything else."""
    return policy.action(action_id)


def required_phrase_display(spec: ActionSpec) -> str:
    """The phrase, shown to a supervisor exactly as it must be typed."""
    return spec.phrase


def _message_contains_phrase(raw_message: str, phrase: str, casefold: bool) -> bool:
    """Exact word-boundary containment of the phrase in the raw message.

    This is the whole consent check. No fuzzy matching, no stemming, no
    model. The phrase must appear as complete words: ``APPROVE MOVE TO
    SHADE`` is contained in ``...APPROVE MOVE TO SHADE now`` but *not* in
    ``APPROVE MOVE TO SHADEE`` — a typo is a refusal, not a near-pass.
    """
    haystack = raw_message.casefold() if casefold else raw_message
    needle = phrase.casefold() if casefold else phrase
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def check_approval(
    policy: HeatPolicy,
    spec: ActionSpec,
    raw_message: str,
    approver_roles: tuple[str, ...] | list[str],
    *,
    standing: bool = False,
) -> ApprovalDecision:
    """Check one proposed action against the policy and one raw message.

    Args:
        policy: The loaded policy, for the approval ``casefold`` setting and
            the standing-advisory rule.
        spec: The action being proposed. Must come from ``policy``; the
            orchestrator looks it up via :func:`action_by_id` so a caller
            cannot smuggle in an action the policy never allowed.
        raw_message: The supervisor's message exactly as received, before any
            summarisation. The Beacon lesson: consent must be checked against
            the transcript, never a summary of it.
        approver_roles: The roles the human approver holds.
        standing: True when the proposal is to create a standing advisory
            rather than a one-shot action.

    Returns:
        An :class:`ApprovalDecision`. ``allowed`` is True only when every
        check passed; ``reason`` names the first check that failed.
    """
    roles = tuple(approver_roles)

    if standing and not spec.standing_allowed:
        return ApprovalDecision(
            allowed=False,
            reason=DeniedReason.NOT_STANDING,
            action_id=spec.id,
            approver_roles=roles,
            phrase_required=spec.phrase,
            phrase_matched=False,
            explain=f"{spec.label!r} may not be issued as a standing advisory.",
        )

    role_held = bool(set(roles) & set(spec.approver_roles))
    phrase_matched = _message_contains_phrase(
        raw_message, spec.phrase, casefold=policy.policy.approval.casefold
    )

    if not role_held:
        required = ", ".join(sorted(spec.approver_roles))
        held = ", ".join(sorted(roles)) if roles else "(none)"
        return ApprovalDecision(
            allowed=False,
            reason=DeniedReason.MISSING_ROLE,
            action_id=spec.id,
            approver_roles=roles,
            phrase_required=spec.phrase,
            phrase_matched=phrase_matched,
            explain=(
                f"Approver holds roles [{held}] but {spec.label!r} requires one of: {required}."
            ),
        )

    if not phrase_matched:
        return ApprovalDecision(
            allowed=False,
            reason=DeniedReason.PHRASE_MISMATCH,
            action_id=spec.id,
            approver_roles=roles,
            phrase_required=spec.phrase,
            phrase_matched=False,
            explain=(
                f"Message did not contain the exact approval phrase "
                f"{spec.phrase!r}. Consent is a string comparison, not a "
                f"judgement about intent."
            ),
        )

    return ApprovalDecision(
        allowed=True,
        reason="approved",
        action_id=spec.id,
        approver_roles=roles,
        phrase_required=spec.phrase,
        phrase_matched=True,
        explain=(
            f"Approver holds {', '.join(sorted(set(roles) & set(spec.approver_roles)))} "
            f"and typed the exact phrase {spec.phrase!r}."
        ),
    )


def verify_method_of(spec: ActionSpec) -> VerifyMethod:
    """How the orchestrator should prove this action happened."""
    return spec.verify


def unknown_action_error(policy: HeatPolicy, action_id: str) -> PolicyError:
    """Build the error the orchestrator raises for a non-allowlisted action."""
    allowed = ", ".join(sorted(a.id for a in policy.actions))
    return PolicyError(f"action {action_id!r} is not on this site's allowlist: {allowed}")
