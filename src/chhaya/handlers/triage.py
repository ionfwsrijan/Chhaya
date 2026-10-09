"""Scheduled triage: one hour of weather, every invocation.

EventBridge Scheduler calls this every fifteen minutes. It reads the hour,
runs the two published engines, appends the exposure to the ledger, and — if
the band warrants action — starts the escalation clock by persisting one
pending proposal per action the policy names.

The clock is the point. A pending proposal is written to DynamoDB, not to
memory, so it outlives this invocation and can be answered by the console or
fired by :mod:`chhaya.handlers.escalation` in a different container entirely.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any

from chhaya.domain.bands import Band
from chhaya.runtime import Runtime, build_runtime
from chhaya.safety import record_exposure, record_pending, triage

__all__ = ["event_time", "handler", "run_scheduled_triage"]


def event_time(event: dict[str, Any] | None, *, fallback: datetime) -> datetime:
    """Read the clock out of an EventBridge event, naive and to the second.

    EventBridge Scheduler sends an RFC 3339 timestamp in ``time`` (UTC). The
    engines work in naive local time, matching the recording format, so the
    offset is dropped the same way the console does. An event without a time —
    a manual ``aws lambda invoke`` — falls back to the supplied clock.
    """
    raw = None
    if isinstance(event, dict):
        raw = event.get("time") or event.get("when")
    if not raw:
        return fallback.replace(microsecond=0)
    text = str(raw).strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=None, microsecond=0)


def run_scheduled_triage(runtime: Runtime, when: datetime) -> dict[str, object]:
    """Run one scheduled hour against *runtime* and return a loggable summary."""
    inputs = asyncio.run(runtime.source.current(runtime.policy.site.id, when))
    result = triage(runtime.policy, inputs)
    exposure = record_exposure(runtime.policy, result, store=runtime.store, now=when)

    summary: dict[str, object] = {
        "site_id": runtime.policy.site.id,
        "at": when.isoformat(),
        "band": result.band.value,
        "wbgt_c": round(result.analysis.wbgt_c, 2),
        "workers": result.workers,
        "exposure_event_id": exposure.event_id,
        "proposals": [],
    }
    if result.band is Band.NORMAL:
        return summary

    turn = asyncio.run(runtime.agent.plan(runtime.policy, result, when))
    proposals: list[dict[str, object]] = []
    for proposal in turn.proposals:
        pending = record_pending(runtime.store, proposal)
        proposals.append(
            {
                **proposal.as_dict(),
                "pending_id": pending.proposal_id,
                "escalates_at": (
                    None
                    if not pending.requires_stop
                    else (
                        when + timedelta(minutes=runtime.policy.escalation.after_minutes)
                    ).isoformat()
                ),
            }
        )
    summary["proposals"] = proposals
    summary["reasoning"] = turn.reasoning
    summary["agent_backend"] = turn.backend
    return summary


def handler(event: dict[str, Any] | None, context: Any | None = None) -> dict[str, object]:
    """Lambda entry point: build the runtime, run the hour, return the summary."""
    runtime = build_runtime()
    when = event_time(event, fallback=datetime.now())
    return run_scheduled_triage(runtime, when)
