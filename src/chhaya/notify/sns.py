"""SNS delivery: the Ship-It notification channel.

The SNS client is injectable so tests never touch AWS, and boto3 is imported
lazily so local mode never loads it. The notifier only ever publishes; it has
no read permissions by design, mirroring the write-only IAM role in the SAM
template.
"""

from __future__ import annotations

from typing import Any

from chhaya.notify.base import NotificationResult, NotifierError
from chhaya.safety.escalate import EscalationEvent

__all__ = ["SnsNotifier"]


class SnsNotifier:
    """A :class:`~chhaya.notify.base.Notifier` over an AWS SNS topic."""

    def __init__(
        self,
        topic_arn: str,
        client: Any | None = None,
        subject: str = "Chhaya heat escalation",
    ) -> None:
        if not topic_arn:
            raise ValueError("topic_arn is required")
        self._topic_arn = topic_arn
        self._subject = subject
        self._client = client
        self._owns_client = client is None

    def _sns(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:
            raise NotifierError("boto3 is not installed; install boto3 for SNS delivery") from exc
        self._client = boto3.client("sns")
        return self._client

    async def send(self, event: EscalationEvent, body: str) -> NotificationResult:
        import asyncio

        def _publish() -> dict[str, Any]:
            response: dict[str, Any] = self._sns().publish(
                TopicArn=self._topic_arn,
                Subject=self._subject,
                Message=body,
                MessageAttributes={
                    "site_id": {"DataType": "String", "StringValue": event.site_id},
                    "band": {
                        "DataType": "String",
                        "StringValue": event.band_at_proposal.value,
                    },
                },
            )
            return response

        try:
            response = await asyncio.to_thread(_publish)
        except Exception as exc:
            raise NotifierError(f"SNS publish failed for {event.site_id}: {exc}") from exc
        message_id = response.get("MessageId") if isinstance(response, dict) else None
        recipients = tuple(c.phone for c in event.emergency_contacts)
        return NotificationResult(
            channel="sns",
            delivered=True,
            detail="SNS accepted the publish",
            recipients=recipients,
            message_id=message_id,
        )

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            close = getattr(self._client, "close", None)
            if close is not None:
                await close()
