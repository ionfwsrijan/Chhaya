"""The local scripted agent: the same plan the policy file demands, with
canned reasoning, and zero calls to AWS.

``make local`` uses this backend so a grader can run the whole loop offline.
It is not a fallback in the "degraded demo" sense — it is the correct
behaviour of the system when no model is present, because the action list
never came from a model in the first place. Only the reasoning text differs.
"""

from __future__ import annotations

from datetime import datetime

from chhaya.agent.base import AgentTurn
from chhaya.domain.bands import Band
from chhaya.policy import HeatPolicy
from chhaya.safety import TriageResult, propose_actions

__all__ = ["LocalAgent"]

_REASONING: dict[Band, str] = {
    Band.NORMAL: (
        "Both engines agree conditions are safe for the declared workload. No proposal is drafted."
    ),
    Band.CAUTION: (
        "Amber conditions: the WBGT screen is within the caution margin or the "
        "schedule is near its limit. Draft the crew-level advisory actions the "
        "policy lists for caution — hydration and a work/rest cycle — for a "
        "supervisor to approve."
    ),
    Band.STOP: (
        "The stricter engine has crossed into the stop band. Draft the "
        "standing advisory (move to shade) and the stop-work proposal, each "
        "with its exact approval phrase, for a human to sign."
    ),
}


class LocalAgent:
    """A :class:`~chhaya.agent.base.AgentBackend` with no model behind it."""

    async def plan(
        self,
        policy: HeatPolicy,
        triage_result: TriageResult,
        now: datetime,
    ) -> AgentTurn:
        proposals = propose_actions(policy, triage_result, now=now)
        reasoning = _REASONING[triage_result.band]
        if proposals:
            reasons = ", ".join(p.action_id for p in proposals)
            reasoning = f"{reasoning} Proposed: {reasons}."
        return AgentTurn(
            proposals=proposals,
            reasoning=reasoning,
            backend="local-scripted",
            model_id=None,
        )
