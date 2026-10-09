"""The proposal registry: short-lived tokens that tie a human's approval back
to the exact proposal the system drafted.

A token is created when triage runs and consumed when a supervisor approves.
Holding proposals server-side (rather than trusting the client to echo the
proposal back) means a client cannot swap in an action the policy never
proposed, and cannot replay an approval after the window closes. The token map
is in-memory: local mode is one process. What must survive a restart — the
escalation clock — lives in the store instead, as a pending proposal the token
carries a reference to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import uuid4

from chhaya.safety import PendingProposal, Proposal

__all__ = ["ProposalRegistry", "RegisteredProposal"]


@dataclass(slots=True)
class RegisteredProposal:
    """A proposal plus the token and clock that govern it."""

    token: str
    proposal: Proposal
    created_at: datetime
    pending: PendingProposal | None = None
    consumed: bool = False

    def expired(self, now: datetime, window_minutes: int) -> bool:
        return now - self.created_at > timedelta(minutes=window_minutes)


@dataclass(slots=True)
class ProposalRegistry:
    """An in-memory token -> proposal map with single-use semantics."""

    _by_token: dict[str, RegisteredProposal] = field(default_factory=dict)

    def register(
        self, proposal: Proposal, *, now: datetime, pending: PendingProposal | None = None
    ) -> RegisteredProposal:
        token = uuid4().hex
        entry = RegisteredProposal(token=token, proposal=proposal, created_at=now, pending=pending)
        self._by_token[token] = entry
        return entry

    def claim(self, token: str) -> RegisteredProposal:
        """Fetch and consume a token. Raises KeyError if unknown or reused."""
        entry = self._by_token.get(token)
        if entry is None:
            raise KeyError(f"unknown proposal token {token}")
        if entry.consumed:
            raise KeyError(f"proposal token {token} was already used")
        entry.consumed = True
        return entry

    def peek(self, token: str) -> RegisteredProposal | None:
        return self._by_token.get(token)

    def outstanding(self) -> list[RegisteredProposal]:
        return [entry for entry in self._by_token.values() if not entry.consumed]

    def clear(self) -> None:
        self._by_token.clear()
