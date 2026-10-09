"""The ``chhaya`` command line: serve the console, or run one step by hand.

``chhaya serve`` is what ``make local`` runs. ``chhaya triage`` and
``chhaya escalate`` are the same functions the Lambda handlers call, so an
operator can reproduce a scheduled invocation from a laptop. ``chhaya demo``
walks the whole loop against the deterministic fake source and prints the
receipts — the fallback when Bedrock or the network is unavailable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from chhaya.domain.bands import Band
from chhaya.handlers.escalation import run_escalation_check
from chhaya.handlers.triage import run_scheduled_triage
from chhaya.runtime import Runtime, build_runtime
from chhaya.safety import approve_and_execute, record_coverage

__all__ = ["main"]


def _dump(payload: object) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True))


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    uvicorn.run(
        "chhaya.api.app:app_factory",
        factory=True,
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def _cmd_triage(args: argparse.Namespace) -> int:
    runtime = build_runtime()
    when = datetime.now().replace(microsecond=0) if args.when is None else args.when
    _dump(run_scheduled_triage(runtime, when))
    return 0


def _cmd_escalate(_args: argparse.Namespace) -> int:
    runtime = build_runtime()
    _dump(run_escalation_check(runtime, datetime.now().replace(microsecond=0)))
    return 0


@dataclass(slots=True)
class _DemoHour:
    label: str
    at: datetime


def _cmd_demo(_args: argparse.Namespace) -> int:
    from chhaya.settings import load_settings

    # A fully deterministic, quiet demo: fake weather, JSON-log notifications.
    demo_settings = load_settings(
        {"CHHAYA_MODE": "local", "CHHAYA_SOURCE": "fake", "CHHAYA_NOTIFIER": "log"}
    )
    runtime = build_runtime(demo_settings)
    # Flip the shipped dry-run default off so the demo shows a real advisory
    # and the coverage ledger ticking; the banner says so out loud.
    from chhaya.policy import load_policy_text

    live_text = demo_settings.policy_path.read_text(encoding="utf-8").replace(
        "dry_run: true", "dry_run: false"
    )
    policy = load_policy_text(live_text)
    runtime.policy = policy
    site_id = runtime.policy.site.id
    day = datetime(2026, 5, 18, 0, 0, 0)
    hours = [
        _DemoHour("late morning", day.replace(hour=11)),
        _DemoHour("early afternoon", day.replace(hour=13)),
        _DemoHour("peak", day.replace(hour=15)),
    ]

    print(f"Chhaya demo — {runtime.policy.site.name}")
    print(
        f"policy {runtime.policy.version} | dry_run="
        f"{runtime.policy.policy.dry_run}"
        " (demo flips the shipped dry-run default to show execution)"
    )
    print("-" * 68)

    hottest = None
    for entry in hours:
        summary = run_scheduled_triage(runtime, entry.at)
        band = str(summary["band"])
        print(f"{entry.at:%H:%M} {entry.label:<16} band={band:<8} wbgt={summary['wbgt_c']} C")
        proposals = summary["proposals"]
        assert isinstance(proposals, list)
        if proposals:
            for proposal in proposals:
                if isinstance(proposal, dict):
                    print(
                        f"    proposes {proposal['action_id']} — phrase: {proposal['approval_phrase']}"
                    )
            if hottest is None and band == Band.STOP.value:
                hottest = (entry.at, summary)

    if hottest is not None:
        when, _summary = hottest
        print("-" * 68)
        print("Supervisor answers with the exact phrase from the policy:")
        _run_approval(runtime, when)

    print("-" * 68)
    print("Escalation clock, thirty minutes on (nothing was approved):")
    later = day.replace(hour=15) + timedelta(minutes=30)
    _dump(run_escalation_check(runtime, later))

    print("-" * 68)
    from chhaya.safety import ledger_from_store

    _dump(ledger_from_store(runtime.store, site_id).as_summary())
    return 0


def _run_approval(runtime: Runtime, when: datetime) -> None:
    # Re-derive the proposal the policy names, exactly as triage would.
    from chhaya.safety import propose_actions, record_pending, resolve_pending, triage

    inputs = asyncio.run(runtime.source.current(runtime.policy.site.id, when))
    result = triage(runtime.policy, inputs)
    proposal = next(
        p
        for p in propose_actions(runtime.policy, result, now=when)
        if p.action_id == "move_to_shade"
    )
    pending = record_pending(runtime.store, proposal)
    execution = approve_and_execute(
        runtime.policy,
        proposal,
        store=runtime.store,
        now=when,
        raw_message=proposal.approval_phrase,
        approver_name="Ravi Kumar",
        approver_roles=("site_supervisor",),
        notifier_acknowledged=True,
    )
    resolve_pending(runtime.store, pending)
    coverage = record_coverage(
        execution,
        workers=proposal.triage.workers,
        minutes_covered=float(proposal.spec.ttl_minutes or 60),
    )
    if coverage is not None:
        runtime.store.append_coverage(coverage)
    print(f"    phrase  : {proposal.approval_phrase!r}")
    print(f"    outcome : {execution.record.outcome.value}")
    print(f"    advisory: {None if execution.advisory is None else execution.advisory.advisory_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="chhaya", description="Chhaya heat-safety toolkit")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the console API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=_cmd_serve)

    triage_parser = sub.add_parser("triage", help="run one scheduled triage")
    triage_parser.add_argument("--when", default=None, help="ISO timestamp; defaults to now")
    triage_parser.set_defaults(func=_cmd_triage)

    sub.add_parser("escalate", help="run one escalation-clock check").set_defaults(
        func=_cmd_escalate
    )

    demo = sub.add_parser("demo", help="walk the whole loop against the fake source")
    demo.set_defaults(func=_cmd_demo)

    args = parser.parse_args(argv)
    func = args.func
    return int(func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
