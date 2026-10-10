"""Tests for the HTTP API, driven through FastAPI's TestClient.

The end-to-end test is the one that matters: triage a hot hour, get a token,
refuse a paraphrase, approve the exact phrase, see the dry-run record, watch
the token burn.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chhaya.api.app import create_app
from chhaya.notify import ConsoleNotifier
from chhaya.runtime import build_runtime
from chhaya.settings import load_settings

# 15:00 is the FakeSource peak; the shipped policy defaults to heavy /
# unacclimatized, so this hour lands in the stop band.
HOT_HOUR = datetime(2026, 5, 18, 15, 0, 0)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    settings = load_settings({"CHHAYA_STORE_DIR": str(tmp_path / "store")})
    runtime = build_runtime(settings)
    runtime.notifier = ConsoleNotifier(stream=io.StringIO())
    app = create_app(runtime)
    return TestClient(app)


def _triage(client: TestClient) -> dict:
    response = client.post("/api/triage", json={"when": HOT_HOUR.isoformat()})
    assert response.status_code == 200
    return response.json()


class TestReadEndpoints:
    def test_health(self, client: TestClient) -> None:
        body = client.get("/api/health").json()
        assert body == {"status": "ok", "mode": "local"}

    def test_config(self, client: TestClient) -> None:
        body = client.get("/api/config").json()
        assert body["site_id"] == "hyderabad-miyapur-site-b"
        assert body["dry_run"] is True
        assert body["agent"] == "LocalAgent"

    def test_policy_lists_the_ladder(self, client: TestClient) -> None:
        body = client.get("/api/policy").json()
        assert body["proposals"]["stop"] == ["move_to_shade", "stop_work"]
        assert {action["id"] for action in body["actions"]} >= {"stop_work", "hydrate"}
        assert body["escalation"]["after_minutes"] == 10

    def test_ledger_starts_empty(self, client: TestClient) -> None:
        body = client.get("/api/ledger").json()
        assert body["hazardous_worker_hours_avoided_estimate"] == 0.0
        assert "counterfactual" in str(body["avoided_estimate_assumption"])

    def test_index_when_no_console(self, client: TestClient) -> None:
        assert client.get("/").status_code == 200


class TestTriage:
    def test_hot_hour_is_stop_with_two_proposals(self, client: TestClient) -> None:
        body = _triage(client)
        assert body["triage"]["verdict"]["band"] == "stop"
        assert [p["action_id"] for p in body["proposals"]] == ["move_to_shade", "stop_work"]
        assert all(p["token"] for p in body["proposals"])
        assert body["dry_run"] is True

    def test_triage_includes_reasoning(self, client: TestClient) -> None:
        body = _triage(client)
        assert body["backend"] == "local-scripted"
        assert "stop_work" in body["reasoning"]

    def test_bad_workers_is_422(self, client: TestClient) -> None:
        response = client.post("/api/triage", json={"when": HOT_HOUR.isoformat(), "workers": 0})
        assert response.status_code == 422

    def test_unknown_workload_is_422(self, client: TestClient) -> None:
        response = client.post(
            "/api/triage", json={"when": HOT_HOUR.isoformat(), "workload": "levitation"}
        )
        assert response.status_code == 422

    def test_night_hour_for_a_mild_site_is_normal(self, tmp_path: Path) -> None:
        # The hot scenario's trough is still 34 C, so a genuinely cool site
        # needs its own scenario. 03:00 on a 28 C day is comfortably normal.
        from chhaya.api.app import create_app as _create_app
        from chhaya.sources import FakeSource, Scenario

        settings = load_settings({"CHHAYA_STORE_DIR": str(tmp_path / "store")})
        runtime = build_runtime(settings)
        runtime.source = FakeSource(
            {
                "hyderabad-miyapur-site-b": Scenario(
                    peak_air_c=28.0, humidity=45.0, wind_ms=2.0, peak_shortwave_wm2=800.0
                )
            }
        )
        runtime.notifier = ConsoleNotifier(stream=io.StringIO())
        mild = TestClient(_create_app(runtime))
        response = mild.post("/api/triage", json={"when": datetime(2026, 5, 18, 3, 0).isoformat()})
        assert response.status_code == 200
        assert response.json()["triage"]["verdict"]["band"] == "normal"
        assert response.json()["proposals"] == []


class TestApprove:
    def _stop_work_token(self, client: TestClient) -> str:
        body = _triage(client)
        return next(p["token"] for p in body["proposals"] if p["action_id"] == "stop_work")

    def test_exact_phrase_is_recorded_as_a_dry_run(self, client: TestClient) -> None:
        token = self._stop_work_token(client)
        response = client.post(
            "/api/approve",
            json={
                "token": token,
                "raw_message": "APPROVE STOP WORK",
                "approver_name": "Ravi Kumar",
                "approver_roles": ["site_supervisor"],
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["record"]["outcome"] == "dry_run"
        assert body["record"]["dry_run"] is True
        assert body["advisory"] is None

    def test_paraphrase_is_denied(self, client: TestClient) -> None:
        token = self._stop_work_token(client)
        body = client.post(
            "/api/approve",
            json={
                "token": token,
                "raw_message": "stop work now",
                "approver_name": "Ravi Kumar",
                "approver_roles": ["site_supervisor"],
            },
        ).json()
        assert body["record"]["outcome"] == "denied"
        assert body["record"]["approval"]["phrase_matched"] is False

    def test_wrong_role_is_denied(self, client: TestClient) -> None:
        token = self._stop_work_token(client)
        body = client.post(
            "/api/approve",
            json={
                "token": token,
                "raw_message": "APPROVE STOP WORK",
                "approver_name": "Visitor",
                "approver_roles": ["visitor"],
            },
        ).json()
        assert body["record"]["outcome"] == "denied"

    def test_token_is_single_use(self, client: TestClient) -> None:
        token = self._stop_work_token(client)
        payload = {
            "token": token,
            "raw_message": "APPROVE STOP WORK",
            "approver_name": "Ravi Kumar",
            "approver_roles": ["site_supervisor"],
        }
        assert client.post("/api/approve", json=payload).status_code == 200
        assert client.post("/api/approve", json=payload).status_code == 404

    def test_unknown_token_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/approve",
            json={
                "token": "not-a-token",
                "raw_message": "APPROVE STOP WORK",
                "approver_name": "Ravi",
                "approver_roles": ["site_supervisor"],
            },
        )
        assert response.status_code == 404


class TestStateAndLedger:
    def test_state_reflects_records(self, client: TestClient) -> None:
        body = _triage(client)
        token = next(p["token"] for p in body["proposals"] if p["action_id"] == "stop_work")
        client.post(
            "/api/approve",
            json={
                "token": token,
                "raw_message": "APPROVE STOP WORK",
                "approver_name": "Ravi",
                "approver_roles": ["site_supervisor"],
            },
        )
        state = client.get("/api/state").json()
        assert len(state["records"]) == 1
        assert state["records"][0]["outcome"] == "dry_run"

    def test_exposure_updates_the_ledger(self, client: TestClient) -> None:
        response = client.post(
            "/api/exposure",
            json={"when": HOT_HOUR.isoformat(), "minutes_observed": 60, "workers": 30},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["event"]["band"] == "stop"
        assert body["ledger"]["hazardous_worker_hours_observed"] == 30.0


class TestRevoke:
    def test_unknown_advisory_is_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/revoke",
            json={"advisory_id": "nope", "revoked_by": "Ravi", "reason": "x"},
        )
        assert response.status_code == 404


class TestEscalation:
    def test_outstanding_stop_proposals_escalate_past_the_window(self, client: TestClient) -> None:
        _triage(client)
        later = HOT_HOUR + timedelta(minutes=15)
        body = client.post("/api/escalations/check", json={"now": later.isoformat()}).json()
        assert body["checked_window_minutes"] == 10
        assert len(body["fired"]) >= 1
        assert body["fired"][0]["delivery"]["channel"] == "console"
        assert "HEAT ESCALATION" in body["fired"][0]["message"]

    def test_within_the_window_nothing_fires(self, client: TestClient) -> None:
        _triage(client)
        body = client.post(
            "/api/escalations/check",
            json={"now": (HOT_HOUR + timedelta(minutes=2)).isoformat()},
        ).json()
        assert body["fired"] == []


class TestAwareTimestamps:
    """A client that sends a ``Z`` timestamp must not poison the naive clock."""

    def test_aware_when_is_stored_naive(self, client: TestClient) -> None:
        body = client.post("/api/triage", json={"when": "2026-05-18T15:00:00Z"}).json()
        assert body["proposals"]
        for proposal in body["proposals"]:
            assert datetime.fromisoformat(proposal["proposed_at"]).tzinfo is None

    def test_aware_triage_then_naive_escalation_check(self, client: TestClient) -> None:
        client.post("/api/triage", json={"when": "2026-05-18T15:00:00Z"})
        response = client.post(
            "/api/escalations/check",
            json={"now": (HOT_HOUR + timedelta(minutes=15)).isoformat()},
        )
        assert response.status_code == 200
        assert len(response.json()["fired"]) >= 1
