"""Tests for the three notifier channels.

SNS is tested with a stub client so the suite never touches AWS. The property
that matters: every channel reports an honest NotificationResult, and a hard
failure raises NotifierError rather than pretending the message went out.
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path

import pytest

from chhaya.domain.bands import Band
from chhaya.notify import ConsoleNotifier, LogNotifier, Notifier, NotifierError, SnsNotifier
from chhaya.safety.escalate import EscalationEvent

NOW = datetime(2026, 5, 18, 13, 45, 0)


class _Contact:
    def __init__(self, name: str, phone: str, role: str) -> None:
        self.name = name
        self.phone = phone
        self.role = role


def _event() -> EscalationEvent:
    return EscalationEvent(
        site_id="hyderabad-miyapur-site-b",
        action_id="stop_work",
        escalated_at=NOW,
        waited_minutes=12.5,
        window_minutes=10.0,
        notified_role="owner",
        emergency_contacts=(_Contact("Site Owner", "+911100000000", "owner"),),
        wbgt_c=29.5,
        band_at_proposal=Band.STOP,
    )


class TestConsoleNotifier:
    @pytest.mark.asyncio
    async def test_prints_the_body(self) -> None:
        buffer = io.StringIO()
        notifier = ConsoleNotifier(stream=buffer)
        event = _event()
        result = await notifier.send(event, event.message())
        assert result.delivered
        assert result.channel == "console"
        assert "HEAT ESCALATION" in buffer.getvalue()
        assert notifier.sent == [event]

    @pytest.mark.asyncio
    async def test_recipients_are_the_contact_phones(self) -> None:
        notifier = ConsoleNotifier(stream=io.StringIO())
        result = await notifier.send(_event(), "body")
        assert result.recipients == ("+911100000000",)

    def test_notifies_the_protocol(self) -> None:
        assert isinstance(ConsoleNotifier(stream=io.StringIO()), Notifier)


class TestLogNotifier:
    @pytest.mark.asyncio
    async def test_writes_one_json_line(self) -> None:
        buffer = io.StringIO()
        notifier = LogNotifier(stream=buffer)
        event = _event()
        result = await notifier.send(event, event.message())
        assert result.delivered
        lines = buffer.getvalue().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["site_id"] == "hyderabad-miyapur-site-b"
        assert row["body"].startswith("HEAT ESCALATION")

    @pytest.mark.asyncio
    async def test_appends_to_a_path(self, tmp_path: Path) -> None:
        path = tmp_path / "escalations.jsonl"
        notifier = LogNotifier(path=str(path))
        await notifier.send(_event(), "one")
        await notifier.send(_event(), "two")
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["body"] == "one"

    @pytest.mark.asyncio
    async def test_needs_a_sink(self) -> None:
        with pytest.raises(ValueError, match="stream or a path"):
            LogNotifier()


class _StubSns:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def publish(self, **kwargs: object) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"MessageId": "msg-123"}


class _BrokenSns:
    def publish(self, **_kwargs: object) -> dict[str, str]:
        raise RuntimeError("topic gone")


class TestSnsNotifier:
    @pytest.mark.asyncio
    async def test_publishes_with_message_attributes(self) -> None:
        stub = _StubSns()
        notifier = SnsNotifier(topic_arn="arn:aws:sns:ap-south-1:1:heat", client=stub)
        event = _event()
        result = await notifier.send(event, event.message())
        assert result.delivered
        assert result.message_id == "msg-123"
        call = stub.calls[0]
        assert call["TopicArn"] == "arn:aws:sns:ap-south-1:1:heat"
        attrs = call["MessageAttributes"]
        assert isinstance(attrs, dict)
        assert attrs["site_id"]["StringValue"] == "hyderabad-miyapur-site-b"  # type: ignore[index]
        assert attrs["band"]["StringValue"] == "stop"  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_failure_raises_notifier_error(self) -> None:
        notifier = SnsNotifier(topic_arn="arn:aws:sns:ap-south-1:1:heat", client=_BrokenSns())
        with pytest.raises(NotifierError, match="publish failed"):
            await notifier.send(_event(), "body")

    def test_topic_arn_is_required(self) -> None:
        with pytest.raises(ValueError, match="topic_arn"):
            SnsNotifier(topic_arn="")

    def test_notifies_the_protocol(self) -> None:
        assert isinstance(SnsNotifier(topic_arn="arn:x", client=_StubSns()), Notifier)
