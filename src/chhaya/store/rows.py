"""Row deserializers shared by both stores.

Local mode reads JSON files; AWS mode reads DynamoDB items. Both produce the
same row dicts, so both need the same careful conversion back into domain
objects — floats that arrive as strings, enums that arrive as their value,
nested approval decisions that arrive as maps. One implementation, two
backends, no drift.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from chhaya.domain.bands import Band
from chhaya.policy.engine import ApprovalDecision
from chhaya.safety.actions import ActionOutcome, ActionRecord, VerificationOutcome
from chhaya.safety.advisory import StandingAdvisory
from chhaya.safety.escalate import PendingProposal
from chhaya.safety.exposure import CoverageEvent, ExposureEvent

__all__ = [
    "advisory_from",
    "coverage_from",
    "exposure_from",
    "iso",
    "pending_from",
    "record_from",
]


def iso(value: str) -> datetime:
    return datetime.fromisoformat(value)


def advisory_from(row: dict[str, Any]) -> StandingAdvisory:
    return StandingAdvisory(
        site_id=str(row["site_id"]),
        action_id=str(row["action_id"]),
        band_at_issue=Band(str(row["band_at_issue"])),
        wbgt_c=float(row["wbgt_c"]),
        issued_at=iso(str(row["issued_at"])),
        expires_at=iso(str(row["expires_at"])),
        issued_by=str(row["issued_by"]),
        approval_phrase=str(row["approval_phrase"]),
        reason=str(row["reason"]),
        policy_fingerprint=str(row["policy_fingerprint"]),
        advisory_id=str(row["advisory_id"]),
        revoked_at=(None if row.get("revoked_at") is None else iso(str(row["revoked_at"]))),
        revoked_by=(None if row.get("revoked_by") is None else str(row["revoked_by"])),
        revoke_reason=(None if row.get("revoke_reason") is None else str(row["revoke_reason"])),
    )


def record_from(row: dict[str, Any]) -> ActionRecord:
    approval = row["approval"]
    if not isinstance(approval, dict):
        raise ValueError(f"action record approval must be a map, got {approval!r}")
    roles = approval.get("approver_roles", ())
    decision = ApprovalDecision(
        allowed=bool(approval["allowed"]),
        reason=str(approval["reason"]),
        action_id=str(approval["action_id"]),
        approver_roles=tuple(str(role) for role in roles),
        phrase_required=str(approval["phrase_required"]),
        phrase_matched=bool(approval["phrase_matched"]),
        explain=str(approval["explain"]),
    )
    return ActionRecord(
        site_id=str(row["site_id"]),
        action_id=str(row["action_id"]),
        band_at_proposal=Band(str(row["band_at_proposal"])),
        wbgt_c=float(row["wbgt_c"]),
        proposed_at=iso(str(row["proposed_at"])),
        approver=str(row["approver"]),
        approver_roles=tuple(str(role) for role in row["approver_roles"]),
        decision=decision,
        outcome=ActionOutcome(str(row["outcome"])),
        approval_excerpt=str(row.get("approval_excerpt", "")),
        executed_at=(None if row.get("executed_at") is None else iso(str(row["executed_at"]))),
        verified_at=(None if row.get("verified_at") is None else iso(str(row["verified_at"]))),
        verification_outcome=VerificationOutcome(str(row["verification_outcome"])),
        verification_detail=str(row.get("verification_detail", "")),
        advisory_id=(None if row.get("advisory_id") is None else str(row["advisory_id"])),
        policy_fingerprint=str(row.get("policy_fingerprint", "")),
        dry_run=bool(row.get("dry_run", False)),
        record_id=str(row["record_id"]),
    )


def exposure_from(row: dict[str, Any]) -> ExposureEvent:
    return ExposureEvent(
        site_id=str(row["site_id"]),
        observed_at=iso(str(row["observed_at"])),
        band=Band(str(row["band"])),
        wbgt_c=float(row["wbgt_c"]),
        workload=str(row["workload"]),
        workers=int(row["workers"]),
        minutes_observed=float(row["minutes_observed"]),
        event_id=str(row["event_id"]),
    )


def coverage_from(row: dict[str, Any]) -> CoverageEvent:
    return CoverageEvent(
        site_id=str(row["site_id"]),
        action_id=str(row["action_id"]),
        started_at=iso(str(row["started_at"])),
        minutes_covered=float(row["minutes_covered"]),
        workers=int(row["workers"]),
        band_at_issue=Band(str(row["band_at_issue"])),
        advisory_id=(None if row.get("advisory_id") is None else str(row["advisory_id"])),
        event_id=str(row["event_id"]),
    )


def pending_from(row: dict[str, Any]) -> PendingProposal:
    return PendingProposal(
        site_id=str(row["site_id"]),
        action_id=str(row["action_id"]),
        band_at_proposal=Band(str(row["band_at_proposal"])),
        wbgt_c=float(row["wbgt_c"]),
        proposed_at=iso(str(row["proposed_at"])),
        requires_stop=bool(row["requires_stop"]),
        proposal_id=str(row["proposal_id"]),
        resolved=bool(row.get("resolved", False)),
        escalated=bool(row.get("escalated", False)),
    )
