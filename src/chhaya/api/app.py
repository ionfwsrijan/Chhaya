"""The Chhaya HTTP API: a thin shell over the safety loop.

Every endpoint either reads the store or calls exactly one orchestrator
function. There is no business logic here, no action is invented, and no
proposal is trusted from the client — a supervisor approves a token the
server issued. That is deliberate: the safety properties live in tested
domain code, and the API only exposes them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from chhaya.api.models import (
    ApproveRequest,
    EscalationCheckRequest,
    ExposureRequest,
    RevokeRequest,
    TriageRequest,
)
from chhaya.api.session import ProposalRegistry
from chhaya.domain.screening import Acclimatization
from chhaya.domain.workload import parse_workload
from chhaya.runtime import Runtime, build_runtime
from chhaya.safety import (
    approve_and_execute,
    check_escalation,
    ledger_from_store,
    record_coverage,
    record_exposure,
    record_pending,
    resolve_pending,
    revoke_advisory,
    triage,
)

__all__ = ["create_app"]


def _find_web_dir() -> Path | None:
    """Locate ``web/index.html`` under the repo or a Lambda bundle.

    Local installs keep ``web/`` at the repository root; Lambda unpacks the
    same tree under ``/var/task``. Walking the parents handles both layouts
    instead of assuming a fixed depth.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "web" / "index.html"
        if candidate.is_file():
            return candidate.parent
    return None


_WEB_DIR = _find_web_dir()


def _now() -> datetime:
    return datetime.now().replace(microsecond=0)


def create_app(runtime: Runtime | None = None) -> FastAPI:
    """Build the FastAPI app around a :class:`~chhaya.runtime.Runtime`."""
    active = runtime or build_runtime()
    registry = ProposalRegistry()

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        aclose = getattr(active.source, "aclose", None)
        if aclose is not None:
            await aclose()
        aclose_notifier = getattr(active.notifier, "aclose", None)
        if aclose_notifier is not None:
            await aclose_notifier()

    app = FastAPI(
        title="Chhaya heat-safety API",
        version="1.0.0",
        description=(
            "Site-level heat action plans for outdoor workers: two published "
            "engines, stricter-wins, and human consent as an exact phrase."
        ),
        lifespan=lifespan,
    )
    app.state.runtime = active
    app.state.registry = registry

    # --- reading --------------------------------------------------------

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "mode": active.settings.mode}

    @app.get("/api/config")
    def config() -> dict[str, object]:
        return active.describe()

    @app.get("/api/policy")
    def policy_view() -> dict[str, object]:
        policy = active.policy
        return {
            "version": policy.version,
            "site": policy.site.model_dump(mode="json"),
            "dry_run": policy.policy.dry_run,
            "actions": [action.model_dump(mode="json") for action in policy.actions],
            "proposals": policy.proposals.model_dump(mode="json"),
            "escalation": policy.escalation.model_dump(mode="json"),
        }

    @app.get("/api/ledger")
    def ledger() -> dict[str, object]:
        summary = ledger_from_store(active.store, active.policy.site.id).as_summary()
        return summary

    @app.get("/api/state")
    def state() -> dict[str, object]:
        site_id = active.policy.site.id
        now = _now()
        advisories = [
            {**advisory.as_dict(), "active": advisory.is_active_at(now)}
            for advisory in active.store.list_advisories(site_id)
        ]
        records = [record.as_dict() for record in active.store.list_action_records(site_id)]
        summary = ledger_from_store(active.store, site_id).as_summary()
        return {
            "config": active.describe(),
            "advisories": advisories,
            "records": records,
            "ledger": summary,
        }

    # --- acting ---------------------------------------------------------

    @app.post("/api/triage")
    async def run_triage(body: TriageRequest) -> dict[str, object]:
        when = body.when or _now()
        inputs = await active.source.current(active.policy.site.id, when)
        try:
            workload = parse_workload(body.workload) if body.workload else None
            acclimatization = (
                Acclimatization(body.acclimatization) if body.acclimatization else None
            )
            result = triage(
                active.policy,
                inputs,
                workload=workload,
                acclimatization=acclimatization,
                workers=body.workers,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        turn = await active.agent.plan(active.policy, result, when)
        proposals = []
        for proposal in turn.proposals:
            pending = record_pending(active.store, proposal)
            entry = registry.register(proposal, now=when, pending=pending)
            proposals.append(
                {
                    **proposal.as_dict(),
                    "token": entry.token,
                    "pending_id": pending.proposal_id,
                    "escalates_at": (
                        None
                        if not pending.requires_stop
                        else (when + timedelta(minutes=active.policy.escalation.after_minutes))
                    ),
                }
            )
        return {
            "triage": result.as_dict(),
            "reasoning": turn.reasoning,
            "backend": turn.backend,
            "model_id": turn.model_id,
            "proposals": proposals,
            "dry_run": active.policy.policy.dry_run,
        }

    @app.post("/api/approve")
    def approve(body: ApproveRequest) -> dict[str, object]:
        try:
            entry = registry.claim(body.token)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        proposal = entry.proposal
        execution = approve_and_execute(
            active.policy,
            proposal,
            store=active.store,
            now=_now(),
            raw_message=body.raw_message,
            approver_name=body.approver_name,
            approver_roles=body.approver_roles,
            notifier_acknowledged=body.notifier_acknowledged,
        )
        if entry.pending is not None:
            resolve_pending(active.store, entry.pending)
        coverage = record_coverage(
            execution,
            workers=proposal.triage.workers,
            minutes_covered=float(proposal.spec.ttl_minutes or 60),
        )
        if coverage is not None:
            active.store.append_coverage(coverage)
        return {
            "record": execution.record.as_dict(),
            "advisory": None if execution.advisory is None else execution.advisory.as_dict(),
            "verification": (
                None if execution.verification is None else execution.verification.as_dict()
            ),
            "coverage": None if coverage is None else coverage.as_dict(),
        }

    @app.post("/api/revoke")
    def revoke(body: RevokeRequest) -> dict[str, object]:
        advisory = active.store.get_advisory(body.advisory_id)
        if advisory is None:
            raise HTTPException(status_code=404, detail=f"unknown advisory {body.advisory_id}")
        revoked = revoke_advisory(
            active.policy,
            advisory,
            store=active.store,
            now=_now(),
            revoked_by=body.revoked_by,
            reason=body.reason,
        )
        return revoked.as_dict()

    @app.post("/api/exposure")
    async def add_exposure(body: ExposureRequest) -> dict[str, object]:
        inputs = await active.source.current(active.policy.site.id, body.when)
        try:
            workload = parse_workload(body.workload) if body.workload else None
            acclimatization = (
                Acclimatization(body.acclimatization) if body.acclimatization else None
            )
            result = triage(
                active.policy,
                inputs,
                workload=workload,
                acclimatization=acclimatization,
                workers=body.workers,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        event = record_exposure(
            active.policy,
            result,
            store=active.store,
            now=body.when,
            minutes_observed=body.minutes_observed,
        )
        summary = ledger_from_store(active.store, active.policy.site.id).as_summary()
        return {"event": event.as_dict(), "ledger": summary}

    @app.post("/api/escalations/check")
    async def check_escalations(body: EscalationCheckRequest) -> dict[str, object]:
        now = body.now or _now()
        window = active.policy.escalation.after_minutes
        site_id = active.policy.site.id
        fired: list[dict[str, object]] = []
        for pending in active.store.list_pending(site_id, unresolved_only=True):
            if not pending.requires_stop or pending.escalated:
                continue
            event = check_escalation(active.policy, pending, now=now)
            if event is None:
                continue
            delivery = await active.notifier.send(event, event.message())
            active.store.put_pending(pending.fired())
            fired.append(
                {**event.as_dict(), "message": event.message(), "delivery": delivery.as_dict()}
            )
        return {"fired": fired, "checked_window_minutes": window}

    # --- the zero-build console ----------------------------------------

    if _WEB_DIR is not None:
        app.mount("/assets", StaticFiles(directory=_WEB_DIR), name="assets")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(_WEB_DIR / "index.html")

    else:

        @app.get("/")
        def index_missing() -> JSONResponse:
            return JSONResponse(
                status_code=200,
                content={"message": "Chhaya API is running; no web console found."},
            )

    return app


def app_factory() -> FastAPI:
    """ASGI entry point for ``uvicorn chhaya.api.app:app_factory --factory``."""
    return create_app()
