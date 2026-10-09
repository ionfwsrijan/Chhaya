"""Tests for the two agent backends.

The property that matters: both backends propose *exactly* the actions the
policy file names for the band — the model never widens or narrows the set.
Bedrock is tested with a stub client so the suite never touches AWS.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from chhaya.agent import AgentError, BedrockAgent, LocalAgent
from chhaya.domain.bands import Band
from chhaya.domain.heat import HeatInputs
from chhaya.domain.workload import Workload
from chhaya.policy import load_policy, load_policy_text
from chhaya.safety import propose_actions, triage

POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "heat_policy_v1.yaml"
NOW = datetime(2026, 5, 18, 13, 30, 0)

HUMID = HeatInputs(air_c=30.0, relative_humidity=70.0, wind_ms=0.5, shortwave_wm2=100.0)
HOT = HeatInputs(
    air_c=44.0, relative_humidity=10.0, wind_ms=4.0, shortwave_wm2=950.0, cloud_cover=0.0
)
MILD = HeatInputs(
    air_c=26.0, relative_humidity=45.0, wind_ms=1.5, shortwave_wm2=300.0, cloud_cover=60.0
)


@pytest.fixture
def policy() -> Any:
    return load_policy(POLICY_PATH)


class TestLocalAgent:
    @pytest.mark.asyncio
    async def test_stop_band_proposes_exactly_the_policy_ladder(self, policy: Any) -> None:
        result = triage(policy, HUMID, workload=None)
        turn = await LocalAgent().plan(policy, result, now=NOW)
        assert [p.action_id for p in turn.proposals] == ["move_to_shade", "stop_work"]
        assert turn.backend == "local-scripted"
        assert turn.model_id is None

    @pytest.mark.asyncio
    async def test_matches_propose_actions_directly(self, policy: Any) -> None:
        result = triage(policy, HUMID)
        turn = await LocalAgent().plan(policy, result, now=NOW)
        direct = propose_actions(policy, result, now=NOW)
        assert [p.action_id for p in turn.proposals] == [p.action_id for p in direct]

    @pytest.mark.asyncio
    async def test_normal_band_proposes_nothing(self, policy: Any) -> None:
        result = triage(policy, MILD, workload=Workload.LIGHT)
        assert result.band is Band.NORMAL
        turn = await LocalAgent().plan(policy, result, now=NOW)
        assert turn.proposals == ()
        assert "normal" in turn.reasoning.lower() or "safe" in turn.reasoning.lower()

    @pytest.mark.asyncio
    async def test_reasoning_mentions_the_proposals(self, policy: Any) -> None:
        result = triage(policy, HOT)
        turn = await LocalAgent().plan(policy, result, now=NOW)
        assert "move_to_shade" in turn.reasoning
        assert "stop_work" in turn.reasoning

    @pytest.mark.asyncio
    async def test_as_dict_is_json_safe(self, policy: Any) -> None:
        result = triage(policy, HUMID)
        turn = await LocalAgent().plan(policy, result, now=NOW)
        json.dumps(turn.as_dict())

    @pytest.mark.asyncio
    async def test_caution_band(self, policy: Any) -> None:
        caution_weather = HeatInputs(27.0, 50.0, 1.5, 50.0, cloud_cover=90.0)
        result = triage(policy, caution_weather)
        turn = await LocalAgent().plan(policy, result, now=NOW)
        assert [p.action_id for p in turn.proposals] == ["hydrate", "start_rest_cycle"]


class _StubBedrock:
    """A minimal boto3-bedrock-runtime-shaped client for tests."""

    def __init__(
        self,
        text: str = "The WBGT screen crossed the stop limit. Approving shade and stop-work moves the crew indoors.",
    ) -> None:
        self._text = text
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "output": {
                "message": {
                    "content": [{"text": self._text}],
                }
            }
        }


class _BrokenBedrock:
    def converse(self, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("throttled")


class _EmptyBedrock:
    def converse(self, **_kwargs: Any) -> dict[str, Any]:
        return {"output": {"message": {"content": []}}}


class TestBedrockAgent:
    @pytest.mark.asyncio
    async def test_same_proposals_as_local(self, policy: Any) -> None:
        stub = _StubBedrock()
        result = triage(policy, HUMID)
        bedrock_turn = await BedrockAgent(client=stub).plan(policy, result, now=NOW)
        local_turn = await LocalAgent().plan(policy, result, now=NOW)
        assert [p.action_id for p in bedrock_turn.proposals] == [
            p.action_id for p in local_turn.proposals
        ]

    @pytest.mark.asyncio
    async def test_model_writes_the_reasoning(self, policy: Any) -> None:
        stub = _StubBedrock("Hot and humid; the WBGT screen triggered the stop band.")
        result = triage(policy, HUMID)
        turn = await BedrockAgent(client=stub).plan(policy, result, now=NOW)
        assert "WBGT screen" in turn.reasoning
        assert turn.backend == "bedrock"
        assert turn.model_id is not None

    @pytest.mark.asyncio
    async def test_prompt_contains_policy_fingerprint_and_triage(self, policy: Any) -> None:
        stub = _StubBedrock()
        result = triage(policy, HUMID)
        await BedrockAgent(client=stub).plan(policy, result, now=NOW)
        call = stub.calls[0]
        assert call["modelId"]
        system_text = call["system"][0]["text"]
        user_text = call["messages"][0]["content"][0]["text"]
        assert "supervisor" in system_text.lower()
        assert "site_id" in user_text
        assert "move_to_shade" in user_text

    @pytest.mark.asyncio
    async def test_bedrock_failure_raises_agent_error(self, policy: Any) -> None:
        result = triage(policy, HUMID)
        with pytest.raises(AgentError, match="failed"):
            await BedrockAgent(client=_BrokenBedrock()).plan(policy, result, now=NOW)

    @pytest.mark.asyncio
    async def test_empty_response_raises(self, policy: Any) -> None:
        result = triage(policy, HUMID)
        with pytest.raises(AgentError, match="no text"):
            await BedrockAgent(client=_EmptyBedrock()).plan(policy, result, now=NOW)

    @pytest.mark.asyncio
    async def test_fallback_to_local_recovers_the_turn(self, policy: Any) -> None:
        """The documented fallback: Bedrock fails, LocalAgent takes over."""
        result = triage(policy, HUMID)
        try:
            turn = await BedrockAgent(client=_BrokenBedrock()).plan(policy, result, now=NOW)
        except AgentError:
            turn = await LocalAgent().plan(policy, result, now=NOW)
        assert turn.backend == "local-scripted"
        assert [p.action_id for p in turn.proposals] == ["move_to_shade", "stop_work"]

    @pytest.mark.asyncio
    async def test_dry_run_policy_does_not_change_agent_behaviour(self, tmp_path: Path) -> None:
        live_text = POLICY_PATH.read_text(encoding="utf-8").replace(
            "dry_run: true", "dry_run: false"
        )
        live_policy = load_policy_text(live_text)
        dry_policy = load_policy(POLICY_PATH)
        result = triage(live_policy, HUMID)
        dry_turn = await LocalAgent().plan(dry_policy, result, now=NOW)
        live_turn = await LocalAgent().plan(live_policy, result, now=NOW)
        assert [p.action_id for p in dry_turn.proposals] == [
            p.action_id for p in live_turn.proposals
        ]
