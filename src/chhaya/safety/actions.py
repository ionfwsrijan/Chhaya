"""Action records: the audit trail for every proposal, approval, and execution.

One record per proposed action, from proposal through verification. The
record is the thing a supervisor, an auditor, or a judge reads to answer
"what happened here, and who said it could". Nothing is deleted; a failed
verification or a denied approval is a record like any other.

The record deliberately does *not* store the approver's full raw message —
that may contain unrelated conversation — but does store the phrase that was
matched and an excerpt, because the phrase is public (it is in the policy)
and the excerpt is what proves the match happened against real text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from chhaya.domain.bands import Band
from chhaya.policy.engine import ApprovalDecision

__all__ = ["ActionOutcome", "ActionRecord", "VerificationOutcome"]


class ActionOutcome(StrEnum):
    """Where an action got to in the loop."""

    PROPOSED = "proposed"
    DENIED = "denied"
    DRY_RUN = "dry_run"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"
    VERIFIED = "verified"
    VERIFICATION_FAILED = "verification_failed"


class VerificationOutcome(StrEnum):
    """Result of checking an action's post-condition."""

    PENDING = "pending"
    PASS = "pass"
    FAIL = "fail"


@dataclass(frozen=True, slots=True)
class ActionRecord:
    """One action's full path through the safety loop.

    Attributes:
        record_id: Unique id.
        site_id: Where it happened.
        action_id: Which allowlisted action.
        band_at_proposal: The verdict band that prompted the proposal.
        wbgt_c: The WBGT at proposal time.
        proposed_at: When the system proposed it.
        approver: Who approved (or denied) it, display name.
        approver_roles: The roles they claimed.
        decision: The full approval decision, for the audit trail.
        approval_excerpt: First 200 chars of the raw approval message.
        outcome: How far it got.
        executed_at: When it was executed (None if never).
        verified_at: When verification completed (None if still pending).
        verification_outcome: pass / fail / pending.
        verification_detail: One sentence about the verification.
        advisory_id: The standing advisory it created, if any.
        policy_fingerprint: sha256 of the policy in force.
        dry_run: True when the policy was in dry-run mode.
    """

    site_id: str
    action_id: str
    band_at_proposal: Band
    wbgt_c: float
    proposed_at: datetime
    approver: str
    approver_roles: tuple[str, ...]
    decision: ApprovalDecision
    outcome: ActionOutcome
    approval_excerpt: str = ""
    executed_at: datetime | None = None
    verified_at: datetime | None = None
    verification_outcome: VerificationOutcome = VerificationOutcome.PENDING
    verification_detail: str = ""
    advisory_id: str | None = None
    policy_fingerprint: str = ""
    dry_run: bool = False
    record_id: str = ""

    def __post_init__(self) -> None:
        if not self.record_id:
            object.__setattr__(self, "record_id", uuid4().hex)

    @property
    def approved(self) -> bool:
        return self.decision.allowed

    @property
    def closed(self) -> bool:
        """True once the record can no longer change outcome."""
        return self.outcome in {
            ActionOutcome.DENIED,
            ActionOutcome.DRY_RUN,
            ActionOutcome.VERIFIED,
            ActionOutcome.VERIFICATION_FAILED,
            ActionOutcome.EXECUTION_FAILED,
        }

    def as_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "site_id": self.site_id,
            "action_id": self.action_id,
            "band_at_proposal": self.band_at_proposal.value,
            "wbgt_c": round(self.wbgt_c, 2),
            "proposed_at": self.proposed_at.isoformat(),
            "approver": self.approver,
            "approver_roles": list(self.approver_roles),
            "approval": self.decision.as_dict(),
            "approval_excerpt": self.approval_excerpt,
            "outcome": self.outcome.value,
            "executed_at": (None if self.executed_at is None else self.executed_at.isoformat()),
            "verified_at": (None if self.verified_at is None else self.verified_at.isoformat()),
            "verification_outcome": self.verification_outcome.value,
            "verification_detail": self.verification_detail,
            "advisory_id": self.advisory_id,
            "policy_fingerprint": self.policy_fingerprint,
            "dry_run": self.dry_run,
        }
