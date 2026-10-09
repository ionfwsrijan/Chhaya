"""Standing advisories: scoped, TTL-bounded, revocable directives.

A standing advisory is the artifact Chhaya leaves behind when a band is
reached and a supervisor approves an action: a record that says *this site,
under this policy, for this long, was told to do this*. Three properties
make it safe rather than a banner that never turns off:

1. **Bounded** — every advisory carries an ``expires_at`` no further out than
   the policy's ``max_advisory_ttl_minutes``. Nothing persists forever.
2. **Scoped** — one site, one action, one policy fingerprint. An advisory
   cannot silently apply somewhere else or under rewritten rules.
3. **Revocable** — revocation is a first-class transition that records who
   and why, never a deletion. The history stays.

Advisories are the only thing the console shows as "active" state, so an
expired-but-unrevoked advisory must not read as active: :meth:`StandingAdvisory.is_active_at`
checks the clock, not just the revocation flag.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from chhaya.domain.bands import Band
from chhaya.policy.models import ActionSpec, HeatPolicy, PolicyError

__all__ = ["AdvisoryError", "AdvisoryManager", "StandingAdvisory"]


class AdvisoryError(ValueError):
    """An advisory could not be issued or revoked within the policy's rules."""


@dataclass(frozen=True, slots=True)
class StandingAdvisory:
    """One issued advisory, immutable once created except for revocation.

    Attributes:
        advisory_id: Unique id.
        site_id: The site it applies to.
        action_id: The approved action it encodes.
        band_at_issue: The verdict band that triggered it.
        wbgt_c: The WBGT at the moment of issue.
        issued_at: When it took effect.
        expires_at: When it stops being active, regardless of revocation.
        issued_by: Who approved it (their display name).
        approval_phrase: The exact phrase that authorized it.
        reason: The engine finding in one sentence.
        policy_fingerprint: sha256 of the policy in force when issued.
        revoked_at: When it was revoked, if it was.
        revoked_by: Who revoked it.
        revoke_reason: Why.
    """

    site_id: str
    action_id: str
    band_at_issue: Band
    wbgt_c: float
    issued_at: datetime
    expires_at: datetime
    issued_by: str
    approval_phrase: str
    reason: str
    policy_fingerprint: str
    advisory_id: str = ""
    revoked_at: datetime | None = None
    revoked_by: str | None = None
    revoke_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.advisory_id:
            object.__setattr__(self, "advisory_id", uuid4().hex)

    @property
    def revoked(self) -> bool:
        return self.revoked_at is not None

    def is_active_at(self, now: datetime) -> bool:
        """Active means: not revoked and not expired *as of this instant*."""
        if self.revoked:
            return False
        return now < self.expires_at

    def remaining_minutes(self, now: datetime) -> float:
        if self.revoked or now >= self.expires_at:
            return 0.0
        return (self.expires_at - now).total_seconds() / 60.0

    def as_dict(self) -> dict[str, object]:
        return {
            "advisory_id": self.advisory_id,
            "site_id": self.site_id,
            "action_id": self.action_id,
            "band_at_issue": self.band_at_issue.value,
            "wbgt_c": round(self.wbgt_c, 2),
            "issued_at": self.issued_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "issued_by": self.issued_by,
            "approval_phrase": self.approval_phrase,
            "reason": self.reason,
            "policy_fingerprint": self.policy_fingerprint,
            "revoked": self.revoked,
            "revoked_at": (None if self.revoked_at is None else self.revoked_at.isoformat()),
            "revoked_by": self.revoked_by,
            "revoke_reason": self.revoke_reason,
        }


@dataclass
class AdvisoryManager:
    """Issues and revokes advisories within the policy's bounds.

    Holds no state of its own: every method takes the store, so the manager
    is a set of rules rather than a hidden cache. The TTL clamp lives here,
    not in the orchestrator, because "no advisory outlives the policy max" is
    a policy property and must hold however an advisory is created.
    """

    policy: HeatPolicy

    def issue(
        self,
        spec: ActionSpec,
        *,
        site_id: str,
        band_at_issue: Band,
        wbgt_c: float,
        issued_at: datetime,
        issued_by: str,
        reason: str,
        policy_fingerprint: str,
        ttl_minutes: int | None = None,
    ) -> StandingAdvisory:
        """Create an advisory, clamping its TTL to the policy maximum.

        Raises:
            AdvisoryError: if the action is not standing-allowed, or if no
                TTL can be derived (the action has none and none was given).
        """
        if not spec.standing_allowed:
            raise AdvisoryError(f"action {spec.id!r} may not be issued as a standing advisory")
        desired = ttl_minutes if ttl_minutes is not None else spec.ttl_minutes
        if desired is None:
            raise AdvisoryError(f"action {spec.id!r} has no ttl_minutes and none was supplied")
        capped = min(desired, self.policy.policy.max_advisory_ttl_minutes)
        return StandingAdvisory(
            site_id=site_id,
            action_id=spec.id,
            band_at_issue=band_at_issue,
            wbgt_c=wbgt_c,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(minutes=capped),
            issued_by=issued_by,
            approval_phrase=spec.phrase,
            reason=reason,
            policy_fingerprint=policy_fingerprint,
        )

    def revoke(
        self,
        advisory: StandingAdvisory,
        *,
        revoked_at: datetime,
        revoked_by: str,
        reason: str,
    ) -> StandingAdvisory:
        """Return the advisory with revocation recorded, or refuse."""
        if advisory.revoked:
            raise AdvisoryError(f"advisory {advisory.advisory_id} is already revoked")
        return StandingAdvisory(
            site_id=advisory.site_id,
            action_id=advisory.action_id,
            band_at_issue=advisory.band_at_issue,
            wbgt_c=advisory.wbgt_c,
            issued_at=advisory.issued_at,
            expires_at=advisory.expires_at,
            issued_by=advisory.issued_by,
            approval_phrase=advisory.approval_phrase,
            reason=advisory.reason,
            policy_fingerprint=advisory.policy_fingerprint,
            advisory_id=advisory.advisory_id,
            revoked_at=revoked_at,
            revoked_by=revoked_by,
            revoke_reason=reason,
        )

    def validate_ttl(self, ttl_minutes: int) -> int:
        """Clamp a requested TTL, refusing anything beyond the policy allows."""
        maximum = self.policy.policy.max_advisory_ttl_minutes
        if ttl_minutes > maximum:
            raise PolicyError(
                f"requested advisory TTL {ttl_minutes} min exceeds this "
                f"policy's maximum of {maximum} min"
            )
        return ttl_minutes
