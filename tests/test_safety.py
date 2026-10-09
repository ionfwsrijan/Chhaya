"""Tests for the safety loop: exposure ledger, advisories, verification,
escalation, the local store, and the end-to-end orchestrator.

The end-to-end tests are the ones a judge would run: propose at a Stop-band
hour, refuse a paraphrase, approve the exact phrase, see the advisory created
in dry-run mode as a dry run, flip dry_run off, see it executed and verified,
and confirm the ledger's counterfactual carries its assumption.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from chhaya.domain.bands import Band
from chhaya.domain.heat import HeatInputs
from chhaya.domain.workload import Workload
from chhaya.policy import (
    HeatPolicy,
    PolicyError,
    action_by_id,
    load_policy,
    policy_fingerprint,
)
from chhaya.safety import (
    ActionOutcome,
    AdvisoryError,
    AdvisoryManager,
    CoverageEvent,
    ExposureEvent,
    PendingProposal,
    VerificationOutcome,
    approve_and_execute,
    check_escalation,
    ledger_from_store,
    propose_actions,
    record_coverage,
    record_exposure,
    record_pending,
    resolve_pending,
    revoke_advisory,
    triage,
    verify_action,
)
from chhaya.store import HeatStore, LocalStore

POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "heat_policy_v1.yaml"
NOW = datetime(2026, 5, 18, 13, 30, 0)  # a Hyderabad May afternoon

HUMID = HeatInputs(air_c=30.0, relative_humidity=70.0, wind_ms=0.5, shortwave_wm2=100.0)
HOT = HeatInputs(
    air_c=44.0, relative_humidity=10.0, wind_ms=4.0, shortwave_wm2=950.0, cloud_cover=0.0
)
MILD = HeatInputs(
    air_c=26.0, relative_humidity=45.0, wind_ms=1.5, shortwave_wm2=300.0, cloud_cover=60.0
)


@pytest.fixture
def policy() -> HeatPolicy:
    return load_policy(POLICY_PATH)


@pytest.fixture
def live_policy() -> HeatPolicy:
    """The shipped policy with dry_run switched off, for execution tests."""
    text = POLICY_PATH.read_text(encoding="utf-8").replace("dry_run: true", "dry_run: false")
    from chhaya.policy import load_policy_text

    return load_policy_text(text)


@pytest.fixture
def store(tmp_path: Path) -> LocalStore:
    return LocalStore(tmp_path / "chhaya-store")


class TestTriage:
    def test_humid_afternoon_stops_by_the_wbgt_screen(self, policy: HeatPolicy) -> None:
        result = triage(policy, HUMID, workload=Workload.MODERATE, workers=35)
        assert result.band is Band.STOP
        assert result.verdict.deciding_engine == "wbgt_screen"
        assert result.workers == 35

    def test_hot_dry_afternoon_stops_by_the_schedule(self, policy: HeatPolicy) -> None:
        result = triage(policy, HOT, workload=Workload.LIGHT)
        assert result.band is Band.STOP
        assert result.verdict.deciding_engine == "niosh_schedule"

    def test_mild_day_is_normal(self, policy: HeatPolicy) -> None:
        result = triage(policy, MILD, workload=Workload.LIGHT)
        assert result.band is Band.NORMAL

    def test_defaults_come_from_the_policy(self, policy: HeatPolicy) -> None:
        result = triage(policy, HUMID)
        assert result.workload is Workload.HEAVY
        assert result.acclimatization.value == "unacclimatized"

    def test_as_dict_is_json_safe(self, policy: HeatPolicy) -> None:
        data = triage(policy, HUMID).as_dict()
        assert data["site_id"] == policy.site.id
        assert "verdict" in data and "schedule" in data and "screening" in data

    def test_absurd_worker_count_is_refused(self, policy: HeatPolicy) -> None:
        with pytest.raises(ValueError, match="workers"):
            triage(policy, MILD, workers=0)
        with pytest.raises(ValueError, match="workers"):
            triage(policy, MILD, workers=10001)


class TestProposals:
    def test_normal_band_proposes_nothing(self, policy: HeatPolicy) -> None:
        result = triage(policy, MILD, workload=Workload.LIGHT)
        assert propose_actions(policy, result, now=NOW) == ()

    def test_stop_band_proposes_the_policy_ladder(self, policy: HeatPolicy) -> None:
        result = triage(policy, HUMID, workload=Workload.MODERATE)
        proposals = propose_actions(policy, result, now=NOW)
        assert [p.action_id for p in proposals] == ["move_to_shade", "stop_work"]
        assert proposals[0].approval_phrase == "APPROVE MOVE TO SHADE"
        assert proposals[1].standing is False

    def test_caution_band_proposes_the_crew_actions(self, policy: HeatPolicy) -> None:
        # 27 C / 50 % RH with weak sun: heavy work sits ~0.9 C under the RAL,
        # inside the 1.1 C amber band Table 5-1's spread defines.
        caution_weather = HeatInputs(27.0, 50.0, 1.5, 50.0, cloud_cover=90.0)
        result = triage(policy, caution_weather, workload=Workload.HEAVY)
        assert result.band is Band.CAUTION
        proposals = propose_actions(policy, result, now=NOW)
        assert [p.action_id for p in proposals] == ["hydrate", "start_rest_cycle"]

    def test_proposals_only_reference_allowlisted_actions(self, policy: HeatPolicy) -> None:
        result = triage(policy, HUMID, workload=Workload.MODERATE)
        for proposal in propose_actions(policy, result, now=NOW):
            assert proposal.spec in policy.actions

    def test_pending_proposal_flags_stop_band(self, policy: HeatPolicy) -> None:
        result = triage(policy, HUMID, workload=Workload.MODERATE)
        stop = next(
            p for p in propose_actions(policy, result, now=NOW) if p.action_id == "stop_work"
        )
        assert stop.pending().requires_stop is True


class TestDryRunLoop:
    """The shipped policy has dry_run: true. Nothing may execute."""

    def test_exact_phrase_in_dry_run_is_recorded_not_executed(
        self, policy: HeatPolicy, store: LocalStore
    ) -> None:
        result = triage(policy, HUMID, workload=Workload.MODERATE)
        proposal = next(
            p for p in propose_actions(policy, result, now=NOW) if p.action_id == "stop_work"
        )
        execution = approve_and_execute(
            policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="Crew is exhausted. APPROVE STOP WORK",
            approver_name="Ravi Kumar",
            approver_roles=("site_supervisor",),
        )
        assert execution.record.outcome is ActionOutcome.DRY_RUN
        assert execution.advisory is None
        assert execution.record.dry_run is True
        assert execution.executed is False

    def test_paraphrase_in_dry_run_is_denied_and_recorded(
        self, policy: HeatPolicy, store: LocalStore
    ) -> None:
        result = triage(policy, HUMID, workload=Workload.MODERATE)
        proposal = next(
            p for p in propose_actions(policy, result, now=NOW) if p.action_id == "stop_work"
        )
        execution = approve_and_execute(
            policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="yeah stop everything",
            approver_name="Ravi Kumar",
            approver_roles=("site_supervisor",),
        )
        assert execution.record.outcome is ActionOutcome.DENIED
        assert execution.record.decision.phrase_matched is False
        assert store.list_action_records(policy.site.id)[0].outcome is ActionOutcome.DENIED


class TestLiveLoop:
    def test_exact_phrase_executes_and_verifies_via_notifier(
        self, live_policy: HeatPolicy, store: LocalStore
    ) -> None:
        result = triage(live_policy, HUMID, workload=Workload.MODERATE)
        proposal = next(
            p for p in propose_actions(live_policy, result, now=NOW) if p.action_id == "stop_work"
        )
        execution = approve_and_execute(
            live_policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="APPROVE STOP WORK",
            approver_name="Ravi Kumar",
            approver_roles=("site_supervisor",),
            notifier_acknowledged=True,
        )
        assert execution.record.outcome is ActionOutcome.VERIFIED
        assert execution.verification is not None and execution.verification.passed
        assert execution.record.verification_outcome is VerificationOutcome.PASS

    def test_notifier_rejection_is_a_failed_verification(
        self, live_policy: HeatPolicy, store: LocalStore
    ) -> None:
        result = triage(live_policy, HUMID, workload=Workload.MODERATE)
        proposal = next(
            p for p in propose_actions(live_policy, result, now=NOW) if p.action_id == "stop_work"
        )
        execution = approve_and_execute(
            live_policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="APPROVE STOP WORK",
            approver_name="Ravi Kumar",
            approver_roles=("site_supervisor",),
            notifier_acknowledged=False,
        )
        assert execution.record.outcome is ActionOutcome.VERIFICATION_FAILED
        assert execution.verification is not None and not execution.verification.passed

    def test_standing_advisory_is_created_with_a_bounded_ttl(
        self, live_policy: HeatPolicy, store: LocalStore
    ) -> None:
        result = triage(live_policy, HUMID, workload=Workload.MODERATE)
        proposal = next(
            p
            for p in propose_actions(live_policy, result, now=NOW)
            if p.action_id == "move_to_shade"
        )
        execution = approve_and_execute(
            live_policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="APPROVE MOVE TO SHADE",
            approver_name="Ravi Kumar",
            approver_roles=("site_supervisor",),
        )
        assert execution.advisory is not None
        advisory = execution.advisory
        assert advisory.is_active_at(NOW + timedelta(minutes=10))
        assert not advisory.is_active_at(NOW + timedelta(hours=25))
        assert advisory.expires_at <= NOW + timedelta(
            minutes=live_policy.policy.max_advisory_ttl_minutes
        )
        assert store.get_advisory(advisory.advisory_id) is not None

    def test_advisory_carries_the_policy_fingerprint(
        self, live_policy: HeatPolicy, store: LocalStore
    ) -> None:
        result = triage(live_policy, HUMID, workload=Workload.MODERATE)
        proposal = next(
            p
            for p in propose_actions(live_policy, result, now=NOW)
            if p.action_id == "move_to_shade"
        )
        execution = approve_and_execute(
            live_policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="APPROVE MOVE TO SHADE",
            approver_name="Ravi Kumar",
            approver_roles=("safety_officer",),
        )
        assert execution.advisory is not None
        assert execution.advisory.policy_fingerprint == policy_fingerprint(live_policy)
        assert execution.record.policy_fingerprint == policy_fingerprint(live_policy)

    def test_every_step_is_in_the_store(self, live_policy: HeatPolicy, store: LocalStore) -> None:
        result = triage(live_policy, HUMID, workload=Workload.MODERATE)
        proposals = propose_actions(live_policy, result, now=NOW)
        for proposal in proposals:
            approve_and_execute(
                live_policy,
                proposal,
                store=store,
                now=NOW,
                raw_message=proposal.approval_phrase,
                approver_name="Ravi Kumar",
                approver_roles=("site_supervisor",),
                notifier_acknowledged=True,
            )
        records = store.list_action_records(live_policy.site.id)
        assert len(records) == len(proposals)
        advisories = store.list_advisories(live_policy.site.id)
        assert len(advisories) == 1  # only move_to_shade is standing


class TestAdvisoryLifecycle:
    def test_issue_clamps_ttl_to_policy_max(self, policy: HeatPolicy) -> None:
        manager = AdvisoryManager(policy)
        spec = action_by_id(policy, "start_rest_cycle")
        advisory = manager.issue(
            spec,
            site_id=policy.site.id,
            band_at_issue=Band.STOP,
            wbgt_c=29.0,
            issued_at=NOW,
            issued_by="Ravi",
            reason="test",
            policy_fingerprint="abc",
            ttl_minutes=99999,
        )
        limit = NOW + timedelta(minutes=policy.policy.max_advisory_ttl_minutes)
        assert advisory.expires_at <= limit

    def test_non_standing_action_cannot_become_an_advisory(self, policy: HeatPolicy) -> None:
        manager = AdvisoryManager(policy)
        spec = action_by_id(policy, "stop_work")
        with pytest.raises(AdvisoryError, match="standing"):
            manager.issue(
                spec,
                site_id=policy.site.id,
                band_at_issue=Band.STOP,
                wbgt_c=29.0,
                issued_at=NOW,
                issued_by="Ravi",
                reason="test",
                policy_fingerprint="abc",
            )

    def test_revoke_is_recorded_not_deleted(
        self, live_policy: HeatPolicy, store: LocalStore
    ) -> None:
        result = triage(live_policy, HUMID, workload=Workload.MODERATE)
        proposal = next(
            p
            for p in propose_actions(live_policy, result, now=NOW)
            if p.action_id == "move_to_shade"
        )
        execution = approve_and_execute(
            live_policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="APPROVE MOVE TO SHADE",
            approver_name="Ravi Kumar",
            approver_roles=("safety_officer",),
        )
        assert execution.advisory is not None
        revoked = revoke_advisory(
            live_policy,
            execution.advisory,
            store=store,
            now=NOW + timedelta(minutes=5),
            revoked_by="Ravi Kumar",
            reason="Crew moved; conditions eased",
        )
        assert revoked.revoked is True
        assert not revoked.is_active_at(NOW + timedelta(minutes=6))
        assert (
            store.get_advisory(revoked.advisory_id).revoke_reason == "Crew moved; conditions eased"
        )  # type: ignore[union-attr]

    def test_double_revoke_is_refused(self, policy: HeatPolicy) -> None:
        manager = AdvisoryManager(policy)
        spec = action_by_id(policy, "move_to_shade")
        advisory = manager.issue(
            spec,
            site_id=policy.site.id,
            band_at_issue=Band.STOP,
            wbgt_c=29.0,
            issued_at=NOW,
            issued_by="Ravi",
            reason="test",
            policy_fingerprint="abc",
        )
        once = manager.revoke(advisory, revoked_at=NOW, revoked_by="Ravi", reason="r1")
        with pytest.raises(AdvisoryError, match="already revoked"):
            manager.revoke(once, revoked_at=NOW, revoked_by="Ravi", reason="r2")

    def test_expired_advisory_is_not_active_even_unrevoked(self, policy: HeatPolicy) -> None:
        manager = AdvisoryManager(policy)
        spec = action_by_id(policy, "move_to_shade")
        advisory = manager.issue(
            spec,
            site_id=policy.site.id,
            band_at_issue=Band.STOP,
            wbgt_c=29.0,
            issued_at=NOW,
            issued_by="Ravi",
            reason="test",
            policy_fingerprint="abc",
            ttl_minutes=30,
        )
        assert advisory.is_active_at(NOW + timedelta(minutes=29))
        assert not advisory.is_active_at(NOW + timedelta(minutes=31))
        assert advisory.remaining_minutes(NOW + timedelta(minutes=31)) == 0.0

    def test_validate_ttl_refuses_beyond_policy(self, policy: HeatPolicy) -> None:
        manager = AdvisoryManager(policy)
        with pytest.raises(PolicyError, match="exceeds"):
            manager.validate_ttl(9999)


class TestVerification:
    def test_store_present_passes_for_an_active_advisory(self, policy: HeatPolicy) -> None:
        manager = AdvisoryManager(policy)
        spec = action_by_id(policy, "start_rest_cycle")
        advisory = manager.issue(
            spec,
            site_id=policy.site.id,
            band_at_issue=Band.CAUTION,
            wbgt_c=27.0,
            issued_at=NOW,
            issued_by="Ravi",
            reason="r",
            policy_fingerprint="f",
        )
        verification = verify_action(spec, now=NOW + timedelta(minutes=1), advisory=advisory)
        assert verification.passed

    def test_store_present_fails_for_a_revoked_advisory(self, policy: HeatPolicy) -> None:
        manager = AdvisoryManager(policy)
        spec = action_by_id(policy, "start_rest_cycle")
        advisory = manager.issue(
            spec,
            site_id=policy.site.id,
            band_at_issue=Band.CAUTION,
            wbgt_c=27.0,
            issued_at=NOW,
            issued_by="Ravi",
            reason="r",
            policy_fingerprint="f",
        )
        revoked = manager.revoke(advisory, revoked_at=NOW, revoked_by="R", reason="done")
        verification = verify_action(spec, now=NOW, advisory=revoked)
        assert not verification.passed

    def test_supervisor_confirm_is_pending_not_guessed(self, policy: HeatPolicy) -> None:
        spec = action_by_id(policy, "hydrate")
        verification = verify_action(spec, now=NOW)
        assert verification.outcome is VerificationOutcome.PENDING

    def test_notifier_ack_pending_when_not_asked(self, policy: HeatPolicy) -> None:
        spec = action_by_id(policy, "stop_work")
        verification = verify_action(spec, now=NOW)
        assert verification.outcome is VerificationOutcome.PENDING


class TestEscalation:
    def test_stop_proposal_past_the_window_escalates(self, policy: HeatPolicy) -> None:
        proposal = PendingProposal(
            site_id=policy.site.id,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=30.0,
            proposed_at=NOW,
            requires_stop=True,
        )
        later = NOW + timedelta(minutes=policy.escalation.after_minutes + 1)
        event = check_escalation(policy, proposal, now=later)
        assert event is not None
        assert event.notified_role == "owner"
        assert len(event.emergency_contacts) == 2
        assert "HEAT ESCALATION" in event.message()

    def test_within_the_window_does_not_escalate(self, policy: HeatPolicy) -> None:
        proposal = PendingProposal(
            site_id=policy.site.id,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=30.0,
            proposed_at=NOW,
            requires_stop=True,
        )
        assert check_escalation(policy, proposal, now=NOW + timedelta(minutes=5)) is None

    def test_approved_proposal_disarms_the_clock(self, policy: HeatPolicy) -> None:
        proposal = PendingProposal(
            site_id=policy.site.id,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=30.0,
            proposed_at=NOW,
            requires_stop=True,
        )
        later = NOW + timedelta(minutes=60)
        assert check_escalation(policy, proposal, now=later, approved=True) is None

    def test_non_stop_proposals_never_escalate(self, policy: HeatPolicy) -> None:
        proposal = PendingProposal(
            site_id=policy.site.id,
            action_id="hydrate",
            band_at_proposal=Band.CAUTION,
            wbgt_c=27.0,
            proposed_at=NOW,
            requires_stop=False,
        )
        later = NOW + timedelta(hours=5)
        assert check_escalation(policy, proposal, now=later) is None

    def test_resolved_proposal_disarms_the_clock(self, policy: HeatPolicy) -> None:
        proposal = PendingProposal(
            site_id=policy.site.id,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=30.0,
            proposed_at=NOW,
            requires_stop=True,
        ).answered()
        later = NOW + timedelta(hours=2)
        assert check_escalation(policy, proposal, now=later) is None

    def test_record_and_resolve_pending_persist_the_clock(
        self, store: LocalStore, policy: HeatPolicy
    ) -> None:
        result = triage(policy, HUMID, workers=10)
        proposal = propose_actions(policy, result, now=NOW)[0]
        pending = record_pending(store, proposal)
        assert store.get_pending(pending.proposal_id) is not None
        resolve_pending(store, pending)
        assert store.get_pending(pending.proposal_id).resolved is True


class TestExposureLedger:
    def test_worker_hours_accumulate_by_band(self, store: LocalStore, policy: HeatPolicy) -> None:
        store.append_exposure(ExposureEvent(policy.site.id, NOW, Band.STOP, 29.0, "heavy", 30, 60))
        store.append_exposure(
            ExposureEvent(
                policy.site.id, NOW - timedelta(hours=1), Band.NORMAL, 22.0, "heavy", 30, 60
            )
        )
        ledger = ledger_from_store(store, policy.site.id)
        assert ledger.worker_hours_in_band(Band.STOP) == pytest.approx(30.0)
        assert ledger.worker_hours_in_band(Band.NORMAL) == pytest.approx(30.0)
        assert ledger.total_hazardous_worker_hours() == pytest.approx(30.0)

    def test_coverage_counts_toward_avoided(self, store: LocalStore, policy: HeatPolicy) -> None:
        store.append_coverage(
            CoverageEvent(
                site_id=policy.site.id,
                action_id="move_to_shade",
                started_at=NOW,
                minutes_covered=120,
                workers=30,
                band_at_issue=Band.STOP,
            )
        )
        ledger = ledger_from_store(store, policy.site.id)
        assert ledger.covered_worker_hours() == pytest.approx(60.0)
        assert ledger.avoided_hazardous_worker_hours() == pytest.approx(60.0)

    def test_summary_labels_the_counterfactual(self, store: LocalStore, policy: HeatPolicy) -> None:
        store.append_exposure(ExposureEvent(policy.site.id, NOW, Band.STOP, 29.0, "heavy", 30, 60))
        store.append_coverage(
            CoverageEvent(
                site_id=policy.site.id,
                action_id="move_to_shade",
                started_at=NOW,
                minutes_covered=60,
                workers=30,
                band_at_issue=Band.STOP,
            )
        )
        summary = ledger_from_store(store, policy.site.id).as_summary()
        assert summary["hazardous_worker_hours_avoided_estimate"] == 30.0
        assert "counterfactual" in str(summary["avoided_estimate_assumption"])

    def test_record_exposure_and_coverage_write_through(
        self, live_policy: HeatPolicy, store: LocalStore
    ) -> None:
        result = triage(live_policy, HUMID, workload=Workload.MODERATE)
        event = record_exposure(live_policy, result, store=store, now=NOW, minutes_observed=60)
        assert event.workers == 20
        proposal = next(
            p
            for p in propose_actions(live_policy, result, now=NOW)
            if p.action_id == "move_to_shade"
        )
        execution = approve_and_execute(
            live_policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="APPROVE MOVE TO SHADE",
            approver_name="Ravi",
            approver_roles=("safety_officer",),
        )
        coverage = record_coverage(execution, workers=20, minutes_covered=120)
        assert coverage is not None
        store.append_coverage(coverage)
        ledger = ledger_from_store(store, live_policy.site.id)
        assert ledger.covered_worker_hours() == pytest.approx(40.0)


class TestLocalStore:
    def test_advisory_round_trips(self, store: LocalStore, policy: HeatPolicy) -> None:
        manager = AdvisoryManager(policy)
        spec = action_by_id(policy, "move_to_shade")
        advisory = manager.issue(
            spec,
            site_id=policy.site.id,
            band_at_issue=Band.STOP,
            wbgt_c=29.0,
            issued_at=NOW,
            issued_by="Ravi",
            reason="r",
            policy_fingerprint="f",
        )
        store.put_advisory(advisory)
        loaded = store.get_advisory(advisory.advisory_id)
        assert loaded == advisory

    def test_list_advisories_filters_active(self, store: LocalStore, policy: HeatPolicy) -> None:
        manager = AdvisoryManager(policy)
        spec = action_by_id(policy, "move_to_shade")
        for offset in (0, 200):
            advisory = manager.issue(
                spec,
                site_id=policy.site.id,
                band_at_issue=Band.STOP,
                wbgt_c=29.0,
                issued_at=NOW + timedelta(minutes=offset),
                issued_by="Ravi",
                reason="r",
                policy_fingerprint="f",
                ttl_minutes=60,
            )
            store.put_advisory(advisory)
        active = store.list_advisories(
            policy.site.id, active_only=True, now=NOW + timedelta(minutes=120)
        )
        assert len(active) == 1

    def test_unknown_advisory_revoked_raises(self, store: LocalStore) -> None:
        with pytest.raises(KeyError):
            store.revoke_advisory("nope", revoked_at=NOW, revoked_by="R", reason="x")

    def test_exposure_is_oldest_first(self, store: LocalStore, policy: HeatPolicy) -> None:
        store.append_exposure(ExposureEvent(policy.site.id, NOW, Band.STOP, 29.0, "heavy", 10, 60))
        store.append_exposure(
            ExposureEvent(
                policy.site.id, NOW - timedelta(hours=2), Band.NORMAL, 20.0, "heavy", 10, 60
            )
        )
        events = store.list_exposure(policy.site.id)
        assert events[0].band is Band.NORMAL

    def test_store_satisfies_the_protocol(self, store: LocalStore) -> None:
        assert isinstance(store, HeatStore)

    def test_pending_proposal_round_trips(self, store: LocalStore, policy: HeatPolicy) -> None:
        pending = PendingProposal(
            site_id=policy.site.id,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=31.0,
            proposed_at=NOW,
            requires_stop=True,
        )
        store.put_pending(pending)
        assert store.get_pending(pending.proposal_id) == pending

    def test_pending_resolution_is_filterable(self, store: LocalStore, policy: HeatPolicy) -> None:
        pending = PendingProposal(
            site_id=policy.site.id,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=31.0,
            proposed_at=NOW,
            requires_stop=True,
        )
        store.put_pending(pending)
        store.put_pending(pending.answered())
        assert store.list_pending(policy.site.id, unresolved_only=True) == []
        assert len(store.list_pending(policy.site.id)) == 1

    def test_pending_clock_survives_a_restart(self, tmp_path: Path, policy: HeatPolicy) -> None:
        pending = PendingProposal(
            site_id=policy.site.id,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=31.0,
            proposed_at=NOW,
            requires_stop=True,
        )
        LocalStore(tmp_path / "s").put_pending(pending)
        reopened = LocalStore(tmp_path / "s")
        assert reopened.get_pending(pending.proposal_id) == pending
