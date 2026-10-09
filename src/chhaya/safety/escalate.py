"""Escalation: what happens when nobody approves in time.

The clock is the safety net. When the system proposes a stop-work action and
the supervisor does not respond inside the policy's window, silence is not
consent — it is a failure mode, and this module names it. An escalation
names the emergency contact, notifies the site owner, and is itself recorded
so a later review can see the net fired.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import uuid4

from chhaya.domain.bands import Band
from chhaya.policy.models import EmergencyContact, HeatPolicy

__all__ = ["EscalationEvent", "PendingProposal", "check_escalation"]


@dataclass(frozen=True, slots=True)
class PendingProposal:
    """A proposal waiting for a human, tracked against the clock.

    Attributes:
        proposal_id: Unique id.
        site_id: Where the action was proposed.
        action_id: What was proposed.
        band_at_proposal: The band that prompted it.
        wbgt_c: The WBGT at proposal time.
        proposed_at: When it was proposed.
        requires_stop: True when this is a Stop-band proposal, the kind
            escalation exists for.
        resolved: True once a human has answered (approved or denied), which
            disarms the clock across process restarts.
        escalated: True once the clock has fired, so a scheduled check does
            not notify the same silence twice.
    """

    site_id: str
    action_id: str
    band_at_proposal: Band
    wbgt_c: float
    proposed_at: datetime
    requires_stop: bool
    proposal_id: str = ""
    resolved: bool = False
    escalated: bool = False

    def __post_init__(self) -> None:
        if not self.proposal_id:
            object.__setattr__(self, "proposal_id", uuid4().hex)

    def minutes_waiting(self, now: datetime) -> float:
        return (now - self.proposed_at).total_seconds() / 60.0

    def answered(self) -> PendingProposal:
        return replace(self, resolved=True)

    def fired(self) -> PendingProposal:
        return replace(self, escalated=True)

    def as_dict(self) -> dict[str, object]:
        return {
            "proposal_id": self.proposal_id,
            "site_id": self.site_id,
            "action_id": self.action_id,
            "band_at_proposal": self.band_at_proposal.value,
            "wbgt_c": round(self.wbgt_c, 2),
            "proposed_at": self.proposed_at.isoformat(),
            "requires_stop": self.requires_stop,
            "resolved": self.resolved,
            "escalated": self.escalated,
        }


@dataclass(frozen=True, slots=True)
class EscalationEvent:
    """A fired escalation: who was called, for what, and how late.

    Attributes:
        event_id: Unique id.
        site_id: Where the silence happened.
        action_id: What was waiting for approval.
        escalated_at: When the clock ran out.
        waited_minutes: How long the proposal sat unapproved.
        notified_role: The role the policy says to notify.
        emergency_contacts: Who the policy says to call.
        wbgt_c: The WBGT still in force — the hazard that did not get an
            answer in time.
    """

    site_id: str
    action_id: str
    escalated_at: datetime
    waited_minutes: float
    window_minutes: float
    notified_role: str
    emergency_contacts: tuple[EmergencyContact, ...]
    wbgt_c: float
    band_at_proposal: Band
    event_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            object.__setattr__(self, "event_id", uuid4().hex)

    def message(self) -> str:
        """The text the notifier sends. Written to be read at 2 a.m."""
        names = ", ".join(f"{c.name} ({c.phone})" for c in self.emergency_contacts)
        return (
            f"HEAT ESCALATION - {self.site_id}\n"
            f"No approval received for '{self.action_id}' within "
            f"{self.window_minutes:.0f} minutes.\n"
            f"Band at proposal: {self.band_at_proposal.value}. WBGT: {self.wbgt_c:.1f} C.\n"
            f"Escalating to {self.notified_role}. Emergency contacts: {names}."
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "site_id": self.site_id,
            "action_id": self.action_id,
            "escalated_at": self.escalated_at.isoformat(),
            "waited_minutes": round(self.waited_minutes, 1),
            "window_minutes": round(self.window_minutes, 1),
            "notified_role": self.notified_role,
            "emergency_contacts": [
                {"name": c.name, "phone": c.phone, "role": c.role} for c in self.emergency_contacts
            ],
            "wbgt_c": round(self.wbgt_c, 2),
            "band_at_proposal": self.band_at_proposal.value,
        }


def check_escalation(
    policy: HeatPolicy,
    proposal: PendingProposal,
    *,
    now: datetime,
    approved: bool = False,
) -> EscalationEvent | None:
    """Fire an escalation if a stop proposal has waited past the policy window.

    Args:
        policy: The loaded policy, for the window and the contacts.
        proposal: The pending proposal to check.
        now: The current instant.
        approved: True if the proposal was approved in the meantime, which
            disarms the clock.

    Returns:
        An :class:`EscalationEvent` if the window has passed on an
        unapproved Stop-band proposal, else ``None``. Only Stop-band
        proposals escalate: a missed water break is not an emergency, a
        missed stop-work is.
    """
    if approved or proposal.resolved or not proposal.requires_stop:
        return None
    window = timedelta(minutes=policy.escalation.after_minutes)
    if now - proposal.proposed_at < window:
        return None
    return EscalationEvent(
        site_id=proposal.site_id,
        action_id=proposal.action_id,
        escalated_at=now,
        waited_minutes=proposal.minutes_waiting(now),
        window_minutes=policy.escalation.after_minutes,
        notified_role=policy.escalation.notify_role,
        emergency_contacts=tuple(policy.escalation.emergency_contacts),
        wbgt_c=proposal.wbgt_c,
        band_at_proposal=proposal.band_at_proposal,
    )
