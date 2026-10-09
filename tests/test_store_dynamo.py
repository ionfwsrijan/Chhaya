"""Tests for the DynamoDB store, run against moto's in-memory AWS.

The same behaviours the local store is held to are asserted here, because the
two stores are interchangeable behind ``HeatStore``. moto means these run in
CI with no credentials and no network.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

from chhaya.domain.bands import Band
from chhaya.policy import HeatPolicy, action_by_id, load_policy
from chhaya.safety import (
    ActionOutcome,
    AdvisoryManager,
    CoverageEvent,
    ExposureEvent,
    PendingProposal,
    StandingAdvisory,
    VerificationOutcome,
    approve_and_execute,
    propose_actions,
    triage,
)
from chhaya.store import DynamoStore, HeatStore
from chhaya.store.dynamo import ensure_table, table_definition

POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "heat_policy_v1.yaml"
NOW = datetime(2026, 5, 18, 13, 30, 0)
SITE = "hyderabad-miyapur-site-b"
TABLE = "chhaya-heat-test"


@pytest.fixture
def policy() -> HeatPolicy:
    return load_policy(POLICY_PATH)


@pytest.fixture
def store() -> DynamoStore:
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="ap-south-1")
        ensure_table(resource.meta.client, TABLE)
        yield DynamoStore(table_name=TABLE, table=resource.Table(TABLE))


def _advisory(policy: HeatPolicy, *, offset_minutes: int = 0, ttl: int = 120) -> StandingAdvisory:
    manager = AdvisoryManager(policy)
    spec = action_by_id(policy, "move_to_shade")
    return manager.issue(
        spec,
        site_id=SITE,
        band_at_issue=Band.STOP,
        wbgt_c=29.0,
        issued_at=NOW + timedelta(minutes=offset_minutes),
        issued_by="Ravi Kumar",
        reason="conditions crossed the stop threshold",
        policy_fingerprint="fingerprint",
        ttl_minutes=ttl,
    )


def test_table_definition_has_one_table_and_a_gsi() -> None:
    definition = table_definition(TABLE)
    assert definition["TableName"] == TABLE
    assert [index["IndexName"] for index in definition["GlobalSecondaryIndexes"]] == ["gsi1"]
    assert definition["BillingMode"] == "PAY_PER_REQUEST"


def test_ensure_table_is_idempotent(store: DynamoStore) -> None:
    ensure_table(store._table.meta.client, TABLE)  # second call must not raise


class TestAdvisories:
    def test_round_trips(self, store: DynamoStore, policy: HeatPolicy) -> None:
        advisory = _advisory(policy)
        store.put_advisory(advisory)
        assert store.get_advisory(advisory.advisory_id) == advisory

    def test_put_is_idempotent(self, store: DynamoStore, policy: HeatPolicy) -> None:
        advisory = _advisory(policy)
        store.put_advisory(advisory)
        store.put_advisory(advisory)
        assert len(store.list_advisories(SITE)) == 1

    def test_list_newest_first(self, store: DynamoStore, policy: HeatPolicy) -> None:
        store.put_advisory(_advisory(policy, offset_minutes=0))
        store.put_advisory(_advisory(policy, offset_minutes=30))
        rows = store.list_advisories(SITE)
        assert rows[0].issued_at > rows[1].issued_at

    def test_active_only_filters_expired(self, store: DynamoStore, policy: HeatPolicy) -> None:
        store.put_advisory(_advisory(policy, offset_minutes=0, ttl=15))
        store.put_advisory(_advisory(policy, offset_minutes=120, ttl=180))
        active = store.list_advisories(SITE, active_only=True, now=NOW + timedelta(minutes=130))
        assert len(active) == 1

    def test_revoke_unknown_raises(self, store: DynamoStore) -> None:
        with pytest.raises(KeyError):
            store.revoke_advisory("nope", revoked_at=NOW, revoked_by="R", reason="x")

    def test_revoke_persists(self, store: DynamoStore, policy: HeatPolicy) -> None:
        advisory = _advisory(policy)
        store.put_advisory(advisory)
        revoked = store.revoke_advisory(
            advisory.advisory_id, revoked_at=NOW, revoked_by="Ravi", reason="crew moved"
        )
        assert revoked.revoked
        assert store.get_advisory(advisory.advisory_id).revoke_reason == "crew moved"  # type: ignore[union-attr]

    def test_double_revoke_raises(self, store: DynamoStore, policy: HeatPolicy) -> None:
        advisory = _advisory(policy)
        store.put_advisory(advisory)
        store.revoke_advisory(advisory.advisory_id, revoked_at=NOW, revoked_by="R", reason="r")
        with pytest.raises(ValueError, match="already revoked"):
            store.revoke_advisory(
                advisory.advisory_id, revoked_at=NOW, revoked_by="R", reason="again"
            )


class TestLedger:
    def test_exposure_oldest_first(self, store: DynamoStore) -> None:
        store.append_exposure(ExposureEvent(SITE, NOW, Band.STOP, 29.0, "heavy", 10, 60))
        store.append_exposure(
            ExposureEvent(SITE, NOW - timedelta(hours=2), Band.NORMAL, 20.0, "heavy", 10, 60)
        )
        events = store.list_exposure(SITE)
        assert events[0].band is Band.NORMAL

    def test_coverage_round_trips(self, store: DynamoStore) -> None:
        store.append_coverage(
            CoverageEvent(
                site_id=SITE,
                action_id="move_to_shade",
                started_at=NOW,
                minutes_covered=90,
                workers=25,
                band_at_issue=Band.STOP,
            )
        )
        events = store.list_coverage(SITE)
        assert events[0].minutes_covered == 90.0
        assert events[0].workers == 25

    def test_since_filter(self, store: DynamoStore) -> None:
        store.append_exposure(ExposureEvent(SITE, NOW, Band.STOP, 29.0, "heavy", 10, 60))
        store.append_exposure(
            ExposureEvent(SITE, NOW - timedelta(hours=3), Band.NORMAL, 20.0, "heavy", 10, 60)
        )
        recent = store.list_exposure(SITE, since=NOW - timedelta(hours=1))
        assert len(recent) == 1


class TestEndToEndOnDynamo:
    def test_full_loop_writes_every_collection(
        self, store: DynamoStore, policy: HeatPolicy
    ) -> None:
        from chhaya.domain.heat import HeatInputs
        from chhaya.domain.workload import Workload

        humid = HeatInputs(air_c=30.0, relative_humidity=70.0, wind_ms=0.5, shortwave_wm2=100.0)
        result = triage(policy, humid, workload=Workload.MODERATE)
        proposal = next(
            p for p in propose_actions(policy, result, now=NOW) if p.action_id == "stop_work"
        )
        execution = approve_and_execute(
            policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="APPROVE STOP WORK",
            approver_name="Ravi Kumar",
            approver_roles=("site_supervisor",),
        )
        assert execution.record.outcome is ActionOutcome.DRY_RUN
        records = store.list_action_records(SITE)
        assert len(records) == 1
        assert records[0].verification_outcome is VerificationOutcome.PENDING

    def test_record_with_float_and_null_survives(
        self, store: DynamoStore, policy: HeatPolicy
    ) -> None:
        from chhaya.domain.heat import HeatInputs

        humid = HeatInputs(air_c=30.0, relative_humidity=70.0, wind_ms=0.5, shortwave_wm2=100.0)
        result = triage(policy, humid)
        proposal = next(
            p for p in propose_actions(policy, result, now=NOW) if p.action_id == "move_to_shade"
        )
        approve_and_execute(
            policy,
            proposal,
            store=store,
            now=NOW,
            raw_message="APPROVE MOVE TO SHADE",
            approver_name="Ravi",
            approver_roles=("site_supervisor",),
        )
        record = store.list_action_records(SITE)[0]
        assert record.verified_at is None  # dry-run never verifies
        assert record.executed_at == NOW
        assert record.wbgt_c > 0


def test_store_satisfies_the_protocol(store: DynamoStore) -> None:
    assert isinstance(store, HeatStore)


class TestPending:
    def test_round_trips_by_id(self, store: DynamoStore) -> None:
        pending = PendingProposal(
            site_id=SITE,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=31.0,
            proposed_at=NOW,
            requires_stop=True,
        )
        store.put_pending(pending)
        assert store.get_pending(pending.proposal_id) == pending

    def test_put_is_idempotent(self, store: DynamoStore) -> None:
        pending = PendingProposal(
            site_id=SITE,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=31.0,
            proposed_at=NOW,
            requires_stop=True,
        )
        store.put_pending(pending)
        store.put_pending(pending)
        assert len(store.list_pending(SITE)) == 1

    def test_unresolved_only_filters(self, store: DynamoStore) -> None:
        pending = PendingProposal(
            site_id=SITE,
            action_id="stop_work",
            band_at_proposal=Band.STOP,
            wbgt_c=31.0,
            proposed_at=NOW,
            requires_stop=True,
        )
        store.put_pending(pending)
        store.put_pending(pending.answered())
        assert store.list_pending(SITE, unresolved_only=True) == []
        assert len(store.list_pending(SITE)) == 1

    def test_unknown_id_is_none(self, store: DynamoStore) -> None:
        assert store.get_pending("nope") is None
