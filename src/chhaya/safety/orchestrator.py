"""The safety loop: triage, propose, approve, execute, verify, record.

The loop is deliberately boring. It takes one hour of weather and one crew,
runs two published engines over them, and if the stricter verdict is not
Normal it proposes the actions the *policy file* names for that band — never
actions the model invents. A human approves with an exact phrase. The action
executes (or is recorded as a dry run). Its post-condition is verified. Every
step is written down with the policy fingerprint that was in force.

The orchestrator holds no state and opens no files: the policy, the store,
and the clock are all passed in, so the same loop runs identically under
pytest, under ``make local``, and under Lambda.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from chhaya.domain.bands import Band, EngineFinding, Verdict, caution_margin_for, merge
from chhaya.domain.heat import HeatInputs, HeatStress, analyse
from chhaya.domain.niosh import WorkRest, evaluate
from chhaya.domain.screening import Acclimatization, Screening, screen
from chhaya.domain.workload import Workload
from chhaya.policy.engine import action_by_id, check_approval
from chhaya.policy.loader import policy_fingerprint
from chhaya.policy.models import ActionSpec, HeatPolicy
from chhaya.safety.actions import ActionOutcome, ActionRecord, VerificationOutcome
from chhaya.safety.advisory import AdvisoryManager, StandingAdvisory
from chhaya.safety.escalate import EscalationEvent, PendingProposal
from chhaya.safety.exposure import CoverageEvent, ExposureEvent, ExposureLedger
from chhaya.safety.verify import Verification, verify_action
from chhaya.store.base import HeatStore

__all__ = [
    "ActionOutcome",
    "ExecutionResult",
    "ExposureLedger",
    "PendingProposal",
    "Proposal",
    "TriageResult",
    "approve_and_execute",
    "ledger_from_store",
    "propose_actions",
    "record_coverage",
    "record_exposure",
    "record_pending",
    "resolve_pending",
    "revoke_advisory",
    "triage",
]


@dataclass(frozen=True, slots=True)
class TriageResult:
    """One hour, one crew, both engines, and the merged verdict.

    Attributes:
        site_id: Which site.
        inputs: The weather that was screened.
        analysis: Every heat index computed from it.
        workload: The crew's metabolic band.
        acclimatization: Whether the crew has adapted.
        workers: How many people were on site.
        schedule: The NIOSH work/rest schedule engine's output.
        screening: The WBGT screening engine's output.
        verdict: The stricter-wins merge of both engines.
    """

    site_id: str
    inputs: HeatInputs
    analysis: HeatStress
    workload: Workload
    acclimatization: Acclimatization
    workers: int
    schedule: WorkRest
    screening: Screening
    verdict: Verdict

    @property
    def band(self) -> Band:
        return self.verdict.band

    def as_dict(self) -> dict[str, object]:
        return {
            "site_id": self.site_id,
            "workload": self.workload.value,
            "acclimatization": self.acclimatization.value,
            "workers": self.workers,
            "indices": self.analysis.as_dict(),
            "schedule": {
                "label": self.schedule.label,
                "decision": self.schedule.decision.value,
                "work_min": self.schedule.work_min,
                "rest_min": self.schedule.rest_min,
                "source_temperature_f": round(self.schedule.source_temperature_f, 1),
                "adjustments": self.schedule.adjustments_f,
            },
            "screening": self.screening.as_dict(),
            "verdict": self.verdict.as_dict(),
        }


def triage(
    policy: HeatPolicy,
    inputs: HeatInputs,
    *,
    workload: Workload | None = None,
    acclimatization: Acclimatization | None = None,
    workers: int = 20,
) -> TriageResult:
    """Run one hour through both engines and merge them, stricter-wins."""
    if not 1 <= workers <= 10000:
        raise ValueError(f"workers must be between 1 and 10000, got {workers}")
    workload = workload or policy.crew.default_workload
    acclimatization = acclimatization or policy.crew.default_acclimatization
    analysis = analyse(inputs)
    schedule = evaluate(inputs, workload)
    screening = screen(
        analysis.wbgt_c,
        workload,
        acclimatization,
    )
    findings: tuple[EngineFinding, ...] = (
        _finding_from_schedule(schedule),
        _finding_from_screening(screening, caution_margin_for(workload)),
    )
    verdict = merge(findings)
    return TriageResult(
        site_id=policy.site.id,
        inputs=inputs,
        analysis=analysis,
        workload=workload,
        acclimatization=acclimatization,
        workers=workers,
        schedule=schedule,
        screening=screening,
        verdict=verdict,
    )


def _finding_from_schedule(schedule: WorkRest) -> EngineFinding:
    from chhaya.domain.bands import finding_from_schedule

    return finding_from_schedule(schedule)


def _finding_from_screening(screening: Screening, caution: float) -> EngineFinding:
    from chhaya.domain.bands import finding_from_screening

    return finding_from_screening(screening, caution)


@dataclass(frozen=True, slots=True)
class Proposal:
    """One action the policy says to propose for this band, awaiting a human.

    Attributes:
        spec: The allowlisted action.
        triage: The hour that prompted it.
        approval_phrase: What the supervisor must type, verbatim.
        approver_roles: Who may type it.
        standing: True if this may become a standing advisory.
        proposed_at: When the system proposed it.
    """

    spec: ActionSpec
    triage: TriageResult
    approval_phrase: str
    approver_roles: tuple[str, ...]
    standing: bool
    proposed_at: datetime

    @property
    def action_id(self) -> str:
        return self.spec.id

    def pending(self) -> PendingProposal:
        return PendingProposal(
            site_id=self.triage.site_id,
            action_id=self.spec.id,
            band_at_proposal=self.triage.band,
            wbgt_c=self.triage.analysis.wbgt_c,
            proposed_at=self.proposed_at,
            requires_stop=self.triage.band is Band.STOP,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "action_id": self.spec.id,
            "label": self.spec.label,
            "approval_phrase": self.approval_phrase,
            "approver_roles": list(self.approver_roles),
            "standing": self.standing,
            "verify": self.spec.verify.value,
            "ttl_minutes": self.spec.ttl_minutes,
            "proposed_at": self.proposed_at.isoformat(),
            "band": self.triage.band.value,
        }


def propose_actions(
    policy: HeatPolicy,
    triage_result: TriageResult,
    *,
    now: datetime,
) -> tuple[Proposal, ...]:
    """The actions the *policy file* says to propose for this band.

    Nothing here is chosen by a model. The band comes from the engines, the
    action list comes from ``proposals.<band>`` in the YAML, and anything the
    policy does not name is never proposed.
    """
    action_ids = policy.proposed_actions_for(triage_result.band)
    proposals = []
    for action_id in action_ids:
        spec = action_by_id(policy, action_id)
        proposals.append(
            Proposal(
                spec=spec,
                triage=triage_result,
                approval_phrase=spec.phrase,
                approver_roles=tuple(spec.approver_roles),
                standing=spec.standing_allowed and triage_result.band is not Band.NORMAL,
                proposed_at=now,
            )
        )
    return tuple(proposals)


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    """What happened after a human approved (or denied) a proposal.

    Attributes:
        record: The audit trail entry, always written.
        advisory: The standing advisory created, if any.
        verification: The post-condition check, if execution was attempted.
        escalation: The escalation fired, if the approval clock ran out.
    """

    record: ActionRecord
    advisory: StandingAdvisory | None
    verification: Verification | None
    escalation: EscalationEvent | None

    @property
    def executed(self) -> bool:
        return self.record.outcome in {ActionOutcome.EXECUTED, ActionOutcome.VERIFIED}


def approve_and_execute(
    policy: HeatPolicy,
    proposal: Proposal,
    *,
    store: HeatStore,
    now: datetime,
    raw_message: str,
    approver_name: str,
    approver_roles: tuple[str, ...] | list[str],
    notifier_acknowledged: bool | None = None,
) -> ExecutionResult:
    """Check consent, execute within the policy's dry-run flag, verify, record.

    This is the only place an action is allowed to change the world, and it
    does so only when :func:`~chhaya.policy.engine.check_approval` returned
    ``allowed=True`` against the raw message. In dry-run mode — which the
    shipped policy defaults to — nothing is executed; the record says so, and
    the advisory is not created.
    """
    fingerprint = policy_fingerprint(policy)
    decision = check_approval(
        policy,
        proposal.spec,
        raw_message,
        approver_roles,
        standing=proposal.standing,
    )
    excerpt = raw_message[:200]

    if not decision.allowed:
        record = ActionRecord(
            site_id=proposal.triage.site_id,
            action_id=proposal.spec.id,
            band_at_proposal=proposal.triage.band,
            wbgt_c=proposal.triage.analysis.wbgt_c,
            proposed_at=proposal.proposed_at,
            approver=approver_name,
            approver_roles=tuple(approver_roles),
            decision=decision,
            outcome=ActionOutcome.DENIED,
            approval_excerpt=excerpt,
            policy_fingerprint=fingerprint,
            dry_run=policy.policy.dry_run,
        )
        store.put_action_record(record)
        return ExecutionResult(record=record, advisory=None, verification=None, escalation=None)

    if policy.policy.dry_run:
        record = ActionRecord(
            site_id=proposal.triage.site_id,
            action_id=proposal.spec.id,
            band_at_proposal=proposal.triage.band,
            wbgt_c=proposal.triage.analysis.wbgt_c,
            proposed_at=proposal.proposed_at,
            approver=approver_name,
            approver_roles=tuple(approver_roles),
            decision=decision,
            outcome=ActionOutcome.DRY_RUN,
            approval_excerpt=excerpt,
            executed_at=now,
            verification_outcome=VerificationOutcome.PENDING,
            verification_detail="Dry run: nothing was executed.",
            policy_fingerprint=fingerprint,
            dry_run=True,
        )
        store.put_action_record(record)
        return ExecutionResult(record=record, advisory=None, verification=None, escalation=None)

    advisory: StandingAdvisory | None = None
    if proposal.standing:
        manager = AdvisoryManager(policy)
        advisory = manager.issue(
            proposal.spec,
            site_id=proposal.triage.site_id,
            band_at_issue=proposal.triage.band,
            wbgt_c=proposal.triage.analysis.wbgt_c,
            issued_at=now,
            issued_by=approver_name,
            reason=proposal.triage.verdict.reason,
            policy_fingerprint=fingerprint,
        )
        store.put_advisory(advisory)

    verification = verify_action(
        proposal.spec,
        now=now,
        advisory=advisory,
        notifier_acknowledged=notifier_acknowledged,
    )
    if verification.passed:
        outcome = ActionOutcome.VERIFIED
    elif verification.outcome is VerificationOutcome.FAIL:
        outcome = ActionOutcome.VERIFICATION_FAILED
    else:
        outcome = ActionOutcome.EXECUTED

    record = ActionRecord(
        site_id=proposal.triage.site_id,
        action_id=proposal.spec.id,
        band_at_proposal=proposal.triage.band,
        wbgt_c=proposal.triage.analysis.wbgt_c,
        proposed_at=proposal.proposed_at,
        approver=approver_name,
        approver_roles=tuple(approver_roles),
        decision=decision,
        outcome=outcome,
        approval_excerpt=excerpt,
        executed_at=now,
        verified_at=(now if verification.outcome is not VerificationOutcome.PENDING else None),
        verification_outcome=verification.outcome,
        verification_detail=verification.detail,
        advisory_id=(None if advisory is None else advisory.advisory_id),
        policy_fingerprint=fingerprint,
        dry_run=False,
    )
    store.put_action_record(record)
    return ExecutionResult(
        record=record, advisory=advisory, verification=verification, escalation=None
    )


def record_exposure(
    policy: HeatPolicy,
    triage_result: TriageResult,
    *,
    store: HeatStore,
    now: datetime,
    minutes_observed: float = 60.0,
) -> ExposureEvent:
    """Append this hour to the exposure ledger. Measurement, not model."""
    event = ExposureEvent(
        site_id=policy.site.id,
        observed_at=now,
        band=triage_result.band,
        wbgt_c=triage_result.analysis.wbgt_c,
        workload=triage_result.workload.value,
        workers=triage_result.workers,
        minutes_observed=minutes_observed,
    )
    store.append_exposure(event)
    return event


def record_coverage(
    result: ExecutionResult,
    *,
    workers: int,
    minutes_covered: float,
) -> CoverageEvent | None:
    """Append verified advisory coverage to the ledger, if there was any."""
    if result.advisory is None or result.verification is None or not result.verification.passed:
        return None
    return CoverageEvent(
        site_id=result.record.site_id,
        action_id=result.record.action_id,
        started_at=result.record.executed_at or result.record.proposed_at,
        minutes_covered=minutes_covered,
        workers=workers,
        band_at_issue=result.record.band_at_proposal,
        advisory_id=result.advisory.advisory_id,
    )


def ledger_from_store(store: HeatStore, site_id: str) -> ExposureLedger:
    """Build the tally from whatever the store has seen."""
    return ExposureLedger(
        exposure=store.list_exposure(site_id),
        coverage=store.list_coverage(site_id),
    )


def record_pending(store: HeatStore, proposal: Proposal) -> PendingProposal:
    """Persist a proposal against the escalation clock.

    The clock must outlive the process that started it: a supervisor may
    answer ten minutes later, or the next scheduled check may run in a
    different Lambda invocation. So the pending proposal goes to the store,
    not to memory.
    """
    pending = proposal.pending()
    store.put_pending(pending)
    return pending


def resolve_pending(store: HeatStore, pending: PendingProposal) -> PendingProposal:
    """Disarm the escalation clock once a human has answered."""
    resolved = pending.answered()
    store.put_pending(resolved)
    return resolved


def revoke_advisory(
    policy: HeatPolicy,
    advisory: StandingAdvisory,
    *,
    store: HeatStore,
    now: datetime,
    revoked_by: str,
    reason: str,
) -> StandingAdvisory:
    """Revoke through the manager and persist the revocation."""
    manager = AdvisoryManager(policy)
    revoked = manager.revoke(advisory, revoked_at=now, revoked_by=revoked_by, reason=reason)
    store.put_advisory(revoked)
    return revoked
