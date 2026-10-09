"""What a notifier must promise: deliver the message, or fail loudly.

Chhaya never assumes a message was delivered. The notifier returns a result
the caller records in the audit trail, so "we tried to send an SMS" and "SNS
accepted the publish" are different facts, and only the second one counts as
notification in the verification step.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from chhaya.safety.escalate import EscalationEvent

__all__ = ["NotificationResult", "Notifier", "NotifierError"]


class NotifierError(RuntimeError):
    """The notifier could not deliver; the caller must record the failure."""


class NotificationResult:
    """One delivery attempt, in enough detail to audit."""

    def __init__(
        self,
        channel: str,
        delivered: bool,
        detail: str,
        recipients: tuple[str, ...] = (),
        message_id: str | None = None,
    ) -> None:
        self.channel = channel
        self.delivered = delivered
        self.detail = detail
        self.recipients = recipients
        self.message_id = message_id

    def as_dict(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "delivered": self.delivered,
            "detail": self.detail,
            "recipients": list(self.recipients),
            "message_id": self.message_id,
        }


@runtime_checkable
class Notifier(Protocol):
    """Delivers one escalation message to whoever the policy names."""

    async def send(self, event: EscalationEvent, body: str) -> NotificationResult:
        """Deliver *body* for *event*. Raise NotifierError on hard failure;
        return a result with delivered=False for a soft failure."""
        ...
