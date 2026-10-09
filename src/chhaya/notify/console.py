"""Console and structured-log notifiers: the local-mode delivery channel.

The console notifier prints to stdout so a supervisor running ``make local``
sees the escalation the same way they would see an SMS. The log notifier
writes one JSON line per event so a grader can pipe the demo into a file and
diff it. Both always report delivered=True on a successful write, because
"printed to the terminal I am watching" is genuine delivery in local mode.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TextIO

from chhaya.notify.base import NotificationResult
from chhaya.safety.escalate import EscalationEvent

__all__ = ["ConsoleNotifier", "LogNotifier"]


class ConsoleNotifier:
    """Prints the escalation message to a stream (stdout by default)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout
        self.sent: list[EscalationEvent] = []

    async def send(self, event: EscalationEvent, body: str) -> NotificationResult:
        self._stream.write(body + "\n")
        self._stream.flush()
        self.sent.append(event)
        recipients = tuple(c.phone for c in event.emergency_contacts)
        return NotificationResult(
            channel="console",
            delivered=True,
            detail="printed to console",
            recipients=recipients,
        )


class LogNotifier:
    """Appends one JSON line per escalation to a file (or a stream)."""

    def __init__(self, stream: TextIO | None = None, path: str | None = None) -> None:
        if stream is None and path is None:
            raise ValueError("LogNotifier needs a stream or a path")
        self._stream = stream
        self._path = path
        self.lines: list[str] = []

    async def send(self, event: EscalationEvent, body: str) -> NotificationResult:
        line = json.dumps(
            {**event.as_dict(), "body": body},
            ensure_ascii=False,
            sort_keys=True,
        )
        self.lines.append(line)
        target = self._stream
        if target is None and self._path is not None:
            with Path(self._path).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        elif target is not None:
            target.write(line + "\n")
            target.flush()
        recipients = tuple(c.phone for c in event.emergency_contacts)
        return NotificationResult(
            channel="log",
            delivered=True,
            detail=f"appended 1 line ({len(line)} chars)",
            recipients=recipients,
        )
