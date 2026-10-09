"""Verification: proving an action actually happened, not that it was ordered.

The gap between "we told them to stop" and "they stopped" is where heat
casualties live. Every action the policy approves names its own
post-condition in :class:`~chhaya.policy.models.VerifyMethod`, and this
module checks that post-condition against the store or the notifier rather
than trusting the execution step's own report.

Two methods can be checked here without a human:

- ``STORE_PRESENT``: the standing advisory exists and is unexpired.
- ``NOTIFIER_ACK``: the notifier accepted the message for delivery.

The other two need a human or a sensor, so they return ``PENDING`` and the
orchestrator leaves the record open rather than guessing. An unverifiable
action reported as verified is worse than an action reported as pending.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from chhaya.policy.models import ActionSpec, VerifyMethod
from chhaya.safety.actions import VerificationOutcome
from chhaya.safety.advisory import StandingAdvisory

__all__ = ["Verification", "verify_action"]


@dataclass(frozen=True, slots=True)
class Verification:
    """The result of checking one action's post-condition.

    Attributes:
        outcome: pass, fail, or pending.
        detail: One sentence a human can read.
        method: Which policy method produced this verdict.
    """

    outcome: VerificationOutcome
    detail: str
    method: VerifyMethod

    @property
    def passed(self) -> bool:
        return self.outcome is VerificationOutcome.PASS

    def as_dict(self) -> dict[str, object]:
        return {
            "outcome": self.outcome.value,
            "passed": self.passed,
            "detail": self.detail,
            "method": self.method.value,
        }


def verify_action(
    spec: ActionSpec,
    *,
    now: datetime,
    advisory: StandingAdvisory | None = None,
    notifier_acknowledged: bool | None = None,
) -> Verification:
    """Check one action's post-condition.

    Args:
        spec: The action that was executed.
        now: The instant of verification.
        advisory: The advisory the execution created, if any. Passed in
            rather than looked up so this function stays free of "which
            advisory" policy decisions — the orchestrator already knows.
        notifier_acknowledged: Whether the notifier confirmed delivery, for
            ``NOTIFIER_ACK`` actions. ``None`` means "not asked yet", which
            is pending, not failed.

    Returns:
        A :class:`Verification`. ``PENDING`` is a first-class outcome: it
        means a human or a sensor still has to confirm, and the record stays
        open.
    """
    method = spec.verify

    if method is VerifyMethod.STORE_PRESENT:
        if advisory is None:
            return Verification(
                outcome=VerificationOutcome.PENDING,
                detail="No advisory was created; a human must confirm the action took effect.",
                method=method,
            )
        if advisory.revoked:
            return Verification(
                outcome=VerificationOutcome.FAIL,
                detail=f"Advisory {advisory.advisory_id} was revoked before verification.",
                method=method,
            )
        if not advisory.is_active_at(now):
            return Verification(
                outcome=VerificationOutcome.FAIL,
                detail=f"Advisory {advisory.advisory_id} expired before verification.",
                method=method,
            )
        return Verification(
            outcome=VerificationOutcome.PASS,
            detail=(
                f"Advisory {advisory.advisory_id} is active, unexpired "
                f"({advisory.remaining_minutes(now):.0f} min remaining)."
            ),
            method=method,
        )

    if method is VerifyMethod.NOTIFIER_ACK:
        if notifier_acknowledged is True:
            return Verification(
                outcome=VerificationOutcome.PASS,
                detail="Notifier accepted the message for delivery.",
                method=method,
            )
        if notifier_acknowledged is False:
            return Verification(
                outcome=VerificationOutcome.FAIL,
                detail="Notifier rejected the message.",
                method=method,
            )
        return Verification(
            outcome=VerificationOutcome.PENDING,
            detail="Notifier has not been asked yet.",
            method=method,
        )

    # SUPERVISOR_CONFIRM and GEOFENCE need a human or a sensor callback that
    # has not arrived in this invocation. Pending, never guessed.
    label = {
        VerifyMethod.SUPERVISOR_CONFIRM: "A supervisor must confirm the crew complied.",
        VerifyMethod.GEOFENCE: "A position fix inside the safe zone is required.",
    }[method]
    return Verification(outcome=VerificationOutcome.PENDING, detail=label, method=method)
