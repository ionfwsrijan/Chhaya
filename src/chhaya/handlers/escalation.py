"""Scheduled escalation check: silence is not consent.

EventBridge Scheduler calls this every five minutes. It reads the pending
proposals the triage handler wrote, and for any Stop-band proposal that has
waited past the policy's window without a human answer, it notifies the
policy's escalation role, records the delivery, and marks the clock as fired
so the next check does not notify the same silence twice.

Only Stop-band proposals escalate. A missed water break is not an emergency; a
missed stop-work is.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from chhaya.runtime import Runtime, build_runtime
from chhaya.safety import check_escalation

__all__ = ["handler", "run_escalation_check"]


def run_escalation_check(runtime: Runtime, now: datetime) -> dict[str, object]:
    """Fire every due escalation once, and return what was sent."""
    window = runtime.policy.escalation.after_minutes
    site_id = runtime.policy.site.id
    fired: list[dict[str, object]] = []

    for pending in runtime.store.list_pending(site_id, unresolved_only=True):
        if not pending.requires_stop or pending.escalated:
            continue
        event = check_escalation(runtime.policy, pending, now=now)
        if event is None:
            continue
        delivery = asyncio.run(runtime.notifier.send(event, event.message()))
        runtime.store.put_pending(pending.fired())
        fired.append(
            {
                **event.as_dict(),
                "message": event.message(),
                "delivery": delivery.as_dict(),
            }
        )

    return {
        "site_id": site_id,
        "at": now.isoformat(),
        "checked_window_minutes": window,
        "fired": fired,
    }


def handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, object]:
    """Lambda entry point: check the clock and escalate anything overdue."""
    from chhaya.handlers.triage import event_time

    runtime = build_runtime()
    now = event_time(event, fallback=datetime.now())
    return run_escalation_check(runtime, now)
