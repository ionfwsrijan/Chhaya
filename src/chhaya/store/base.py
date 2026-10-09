"""Persistence contract: advisories, action records, exposure events.

One protocol, two implementations: a local JSON-file store that keeps
``make local`` fully offline, and a DynamoDB store for the AWS deployment.
The orchestrator never imports either — it takes a ``HeatStore``, so the same
loop runs in a demo, in CI, and in production.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from chhaya.safety.actions import ActionRecord
from chhaya.safety.advisory import StandingAdvisory
from chhaya.safety.escalate import PendingProposal
from chhaya.safety.exposure import CoverageEvent, ExposureEvent

__all__ = ["CoverageEvent", "ExposureEvent", "HeatStore", "PendingProposal"]


@runtime_checkable
class HeatStore(Protocol):
    """Everything the safety loop needs to remember, and nothing more.

    Implementations must be idempotent on ``put_advisory`` and
    ``put_action_record``: writing the same object twice is not an error, so
    a retried Lambda does not duplicate history.
    """

    # --- advisories -----------------------------------------------------

    def put_advisory(self, advisory: StandingAdvisory) -> None:
        """Create or overwrite an advisory by ``advisory_id``."""

    def get_advisory(self, advisory_id: str) -> StandingAdvisory | None:
        """One advisory by id, or None."""

    def list_advisories(
        self,
        site_id: str,
        *,
        active_only: bool = False,
        now: datetime | None = None,
    ) -> list[StandingAdvisory]:
        """Advisories for a site, newest first. ``active_only`` filters out
        expired and revoked ones as of ``now``."""

    def revoke_advisory(
        self,
        advisory_id: str,
        *,
        revoked_at: datetime,
        revoked_by: str,
        reason: str,
    ) -> StandingAdvisory:
        """Mark an advisory revoked. Raises KeyError if unknown."""

    # --- action records -------------------------------------------------

    def put_action_record(self, record: ActionRecord) -> None:
        """Create or overwrite an action record by ``record_id``."""

    def list_action_records(
        self, site_id: str, *, since: datetime | None = None
    ) -> list[ActionRecord]:
        """Action records for a site, newest first."""

    # --- exposure ledger ------------------------------------------------

    def append_exposure(self, event: ExposureEvent) -> None:
        """Append one observed exposure interval."""

    def append_coverage(self, event: CoverageEvent) -> None:
        """Append one verified advisory-coverage interval."""

    def list_exposure(self, site_id: str, *, since: datetime | None = None) -> list[ExposureEvent]:
        """Exposure events for a site, oldest first."""

    def list_coverage(self, site_id: str, *, since: datetime | None = None) -> list[CoverageEvent]:
        """Coverage events for a site, oldest first."""

    # --- pending proposals (the escalation clock) -----------------------

    def put_pending(self, pending: PendingProposal) -> None:
        """Create or overwrite a pending proposal by ``proposal_id``."""

    def get_pending(self, proposal_id: str) -> PendingProposal | None:
        """One pending proposal by id, or None."""

    def list_pending(self, site_id: str, *, unresolved_only: bool = False) -> list[PendingProposal]:
        """Pending proposals for a site, oldest first. ``unresolved_only``
        drops the ones a human has already answered."""
