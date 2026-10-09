"""What an agent backend must promise: a plan, with provenance, never the
authority to act on it.

Chhaya's agent does not get tools that change the world. It reads weather and
policy, drafts a structured proposal, and hands it to the safety loop, which
owns consent, execution, verification and the audit trail. The interface here
is deliberately tiny so the same three methods serve a Bedrock runtime, a
scripted local runner, and a test double.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from chhaya.policy import HeatPolicy
from chhaya.safety import Proposal, TriageResult

__all__ = ["AgentBackend", "AgentError", "AgentTurn"]


class AgentError(RuntimeError):
    """The agent backend failed safely; the safety loop stays authoritative."""


class AgentTurn:
    """One agent response: the proposal it drafted and why it is trustworthy."""

    def __init__(
        self,
        proposals: tuple[Proposal, ...],
        reasoning: str,
        backend: str,
        model_id: str | None = None,
    ) -> None:
        self.proposals = proposals
        self.reasoning = reasoning
        self.backend = backend
        self.model_id = model_id

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "model_id": self.model_id,
            "reasoning": self.reasoning,
            "proposals": [p.as_dict() for p in self.proposals],
        }


@runtime_checkable
class AgentBackend(Protocol):
    """Reads triage results, drafts proposals, never executes them."""

    async def plan(
        self,
        policy: HeatPolicy,
        triage_result: TriageResult,
        now: datetime,
    ) -> AgentTurn:
        """Draft a proposal for *triage_result* under *policy*.

        Backends must only suggest actions the policy allows, must attach the
        approval phrase from the policy (not invent one), and must raise
        AgentError rather than guess.
        """
        ...
