"""The Bedrock agent: the same policy-driven plan, with a model writing the
supervisor-facing explanation.

The critical property: the *action list* is identical to the local backend,
because both call :func:`~chhaya.safety.propose_actions`, which reads only the
policy YAML. The model is asked to narrate *why* those actions are warranted —
it is never asked which actions to take, and its output is never parsed for
action ids. If Bedrock is unavailable the backend raises AgentError and the
caller falls back to LocalAgent; nothing about the safety loop changes.
"""

from __future__ import annotations

import json
from datetime import datetime

from chhaya.agent.base import AgentError, AgentTurn
from chhaya.policy import HeatPolicy
from chhaya.policy.loader import policy_fingerprint
from chhaya.safety import TriageResult, propose_actions

__all__ = ["BedrockAgent"]

_INSTRUCTION = """You are the Chhaya heat-safety co-pilot for a construction site supervisor.

You will receive a triage result (heat indices, the NIOSH work/rest schedule
decision, the WBGT screening decision, and the merged stricter-wins verdict)
and the list of actions the site's policy file says to propose for that verdict.

Write a short explanation (3 sentences maximum) a supervisor can read at a
glance: what the weather is, which engine triggered, and what approving the
proposed actions will do. Do NOT invent actions, thresholds, or phrases.
Do NOT give medical advice. If the merged band is normal, say so plainly."""

_PROMPT_TEMPLATE = """Policy fingerprint: {fingerprint}

Triage result (JSON):
{triage_json}

Proposed actions (from the policy file, JSON):
{proposals_json}

Write the supervisor-facing explanation now."""


class BedrockAgent:
    """A :class:`~chhaya.agent.base.AgentBackend` over Amazon Bedrock.

    The Bedrock client is injectable so tests can supply a stub; boto3 is
    imported lazily so local mode never needs the SDK installed.
    """

    def __init__(
        self,
        client: object | None = None,
        model_id: str = "anthropic.claude-3-haiku-20240307-v1:0",
    ) -> None:
        self._client = client
        self._model_id = model_id

    def _bedrock(self) -> object:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as exc:
            raise AgentError("boto3 is not installed; use LocalAgent or install boto3") from exc
        self._client = boto3.client("bedrock-runtime")
        return self._client

    async def plan(
        self,
        policy: HeatPolicy,
        triage_result: TriageResult,
        now: datetime,
    ) -> AgentTurn:
        proposals = propose_actions(policy, triage_result, now=now)
        prompt = _PROMPT_TEMPLATE.format(
            fingerprint=policy_fingerprint(policy),
            triage_json=json.dumps(triage_result.as_dict(), indent=2, sort_keys=True),
            proposals_json=json.dumps([p.as_dict() for p in proposals], indent=2, sort_keys=True),
        )
        try:
            reasoning = await _invoke(self._bedrock(), self._model_id, _INSTRUCTION, prompt)
        except Exception as exc:
            raise AgentError(f"Bedrock invocation failed: {exc}") from exc
        return AgentTurn(
            proposals=proposals,
            reasoning=reasoning,
            backend="bedrock",
            model_id=self._model_id,
        )


async def _invoke(client: object, model_id: str, system: str, prompt: str) -> str:
    """Call Bedrock's Converse API and return the text of the first message.

    Extracted as a module-level coroutine so tests can monkeypatch it without
    building a fake boto3 client.
    """
    import asyncio

    def _call() -> dict[str, object]:
        response = client.converse(  # type: ignore[attr-defined]
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": 300, "temperature": 0.2},
        )
        return response  # type: ignore[no-any-return]

    response = await asyncio.to_thread(_call)
    try:
        output = response["output"]
        assert isinstance(output, dict)
        message = output["message"]
        assert isinstance(message, dict)
        blocks = message["content"]
        assert isinstance(blocks, list)
        texts = [
            str(block["text"]) for block in blocks if isinstance(block, dict) and "text" in block
        ]
    except (KeyError, TypeError, AssertionError) as exc:
        raise AgentError(f"unexpected Bedrock response shape: {response!r}") from exc
    if not texts:
        raise AgentError("Bedrock returned no text content")
    return "\n".join(texts).strip()
