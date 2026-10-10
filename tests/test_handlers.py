"""Tests for the Lambda entry points.

The handlers are deliberately thin, so the assertions are about the durable,
testable seams: the clock read out of the event, the pending proposals written
for a Stop band, the exposure appended, and the escalation clock firing once —
not twice — per unanswered proposal.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

from chhaya.agent import LocalAgent
from chhaya.domain.bands import Band
from chhaya.domain.heat import HeatInputs
from chhaya.domain.workload import Workload
from chhaya.handlers.api import _strip_stage
from chhaya.handlers.escalation import run_escalation_check
from chhaya.handlers.triage import event_time, run_scheduled_triage
from chhaya.notify import LogNotifier
from chhaya.policy import load_policy
from chhaya.runtime import Runtime
from chhaya.safety import PendingProposal
from chhaya.settings import load_settings
from chhaya.sources.fake import FakeSource, Scenario
from chhaya.store import LocalStore

POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "heat_policy_v1.yaml"
SITE = "hyderabad-miyapur-site-b"


def _runtime(tmp_path: Path, *, peak_air_c: float) -> Runtime:
    policy = load_policy(POLICY_PATH)
    return Runtime(
        settings=load_settings(),
        policy=policy,
        source=FakeSource({SITE: Scenario(peak_air_c=peak_air_c, humidity=40.0, wind_ms=1.5)}),
        store=LocalStore(tmp_path / "store"),
        agent=LocalAgent(),
        notifier=LogNotifier(stream=io.StringIO()),
    )


class TestEventTime:
    def test_eventbridge_timestamp(self) -> None:
        event = {
            "time": "2026-05-18T09:30:15Z",
            "source": "aws.scheduler",
            "detail": {},
        }
        when = event_time(event, fallback=datetime(2020, 1, 1))
        assert when == datetime(2026, 5, 18, 9, 30, 15)

    def test_falls_back_when_event_has_no_clock(self) -> None:
        assert event_time({}, fallback=datetime(2026, 5, 18, 9, 30)) == datetime(
            2026, 5, 18, 9, 30, 0
        )


class TestApiHandlerPath:
    def test_strips_named_stage_prefix(self) -> None:
        event = {
            "version": "2.0",
            "rawPath": "/prod/api/health",
            "requestContext": {"stage": "prod", "http": {"path": "/prod/api/health"}},
        }
        _strip_stage(event)
        assert event["rawPath"] == "/api/health"
        assert event["requestContext"]["http"]["path"] == "/api/health"

    def test_strips_stage_to_root(self) -> None:
        event = {"rawPath": "/prod", "requestContext": {"stage": "prod", "http": {"path": "/prod"}}}
        _strip_stage(event)
        assert event["rawPath"] == "/"

    def test_leaves_default_stage_alone(self) -> None:
        event = {
            "rawPath": "/api/health",
            "requestContext": {"stage": "$default", "http": {"path": "/api/health"}},
        }
        _strip_stage(event)
        assert event["rawPath"] == "/api/health"

    def test_no_stage_is_a_noop(self) -> None:
        event = {"rawPath": "/api/health", "requestContext": {}}
        _strip_stage(event)
        assert event["rawPath"] == "/api/health"


class TestScheduledTriage:
    def test_normal_hour_records_exposure_and_proposes_nothing(self, tmp_path: Path) -> None:
        runtime = _runtime(tmp_path, peak_air_c=30.0)  # ~20 C and dark at 03:00
        when = datetime(2026, 5, 18, 3, 0, 0)
        summary = run_scheduled_triage(runtime, when)
        assert summary["band"] == Band.NORMAL.value
        assert summary["proposals"] == []
        exposure = runtime.store.list_exposure(SITE)
        assert len(exposure) == 1
        assert exposure[0].band is Band.NORMAL

    def test_stop_hour_persists_pending_proposals(self, tmp_path: Path) -> None:
        runtime = _runtime(tmp_path, peak_air_c=44.0)
        when = datetime(2026, 5, 18, 15, 0, 0)
        summary = run_scheduled_triage(runtime, when)
        proposals = summary["proposals"]
        assert isinstance(proposals, list)
        assert len(proposals) == 2
        ids = {p["pending_id"] for p in proposals}
        assert len(ids) == 2
        for proposal in proposals:
            assert runtime.store.get_pending(str(proposal["pending_id"])) is not None
        assert all(str(p["escalates_at"]) for p in proposals)

    def test_escalates_at_lands_past_the_policy_window(self, tmp_path: Path) -> None:
        runtime = _runtime(tmp_path, peak_air_c=44.0)
        when = datetime(2026, 5, 18, 15, 0, 0)
        summary = run_scheduled_triage(runtime, when)
        escalates = [p["escalates_at"] for p in summary["proposals"]]
        expected = when + timedelta(minutes=runtime.policy.escalation.after_minutes)
        assert all(datetime.fromisoformat(str(ts)) == expected for ts in escalates)


class TestEscalationClock:
    def test_fires_once_per_unanswered_stop_proposal(self, tmp_path: Path) -> None:
        runtime = _runtime(tmp_path, peak_air_c=44.0)
        when = datetime(2026, 5, 18, 15, 0, 0)
        run_scheduled_triage(runtime, when)

        later = when + timedelta(minutes=30)
        first = run_escalation_check(runtime, later)
        assert len(first["fired"]) == 2
        for entry in first["fired"]:
            assert entry["delivery"]["delivered"] is True
            assert entry["notified_role"] == "owner"

        # The clock fired; the next check stays quiet.
        second = run_escalation_check(runtime, later + timedelta(minutes=5))
        assert second["fired"] == []

    def test_approved_proposal_never_fires(self, tmp_path: Path) -> None:
        runtime = _runtime(tmp_path, peak_air_c=44.0)
        policy = runtime.policy
        when = datetime(2026, 5, 18, 15, 0, 0)

        from chhaya.safety import propose_actions, record_pending, triage

        humid = HeatInputs(air_c=30.0, relative_humidity=70.0, wind_ms=0.5, shortwave_wm2=100.0)
        result = triage(policy, humid, workload=Workload.MODERATE)
        proposal = next(
            p for p in propose_actions(policy, result, now=when) if p.action_id == "stop_work"
        )
        pending = record_pending(runtime.store, proposal)
        runtime.store.put_pending(pending.answered())

        first = run_escalation_check(runtime, when + timedelta(hours=1))
        assert first["fired"] == []


class TestTimezoneNormalisation:
    """An aware timestamp must never leak into the naive clock the engines use.

    A client that sends ``2026-05-18T15:00:00Z`` used to write an aware
    ``proposed_at`` into the store; the scheduled escalation check then read it
    back aware and crashed subtracting it from EventBridge's naive clock. Both
    the request boundary and the store's deserialiser now fold the offset down.
    """

    def _aware_pending(self) -> PendingProposal:
        return PendingProposal(
            site_id=SITE,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=35.0,
            proposed_at=datetime(2026, 5, 18, 15, 0, 0, tzinfo=UTC),
            requires_stop=True,
        )

    def test_aware_pending_reads_back_naive(self, tmp_path: Path) -> None:
        runtime = _runtime(tmp_path, peak_air_c=44.0)
        runtime.store.put_pending(self._aware_pending())
        read = runtime.store.list_pending(SITE, unresolved_only=True)[0]
        assert read.proposed_at == datetime(2026, 5, 18, 15, 0, 0)
        assert read.proposed_at.tzinfo is None

    def test_escalation_survives_an_aware_pending(self, tmp_path: Path) -> None:
        runtime = _runtime(tmp_path, peak_air_c=44.0)
        runtime.store.put_pending(self._aware_pending())
        fired = run_escalation_check(runtime, datetime(2026, 5, 18, 15, 30, 0))
        assert len(fired["fired"]) == 1
