"""Local JSON-file persistence: the offline brain of ``make local``.

One JSON file per collection under a root directory. Not concurrent-safe —
it does not need to be: the local mode is a single supervisor's laptop, one
site, one demo. The DynamoDB store is what runs when more than one person is
looking. Keeping this honest about its limits is why the class docstring says
so.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from chhaya.safety.actions import ActionRecord
from chhaya.safety.advisory import StandingAdvisory
from chhaya.safety.escalate import PendingProposal
from chhaya.safety.exposure import CoverageEvent, ExposureEvent
from chhaya.store.rows import (
    advisory_from,
    coverage_from,
    exposure_from,
    pending_from,
    record_from,
)

__all__ = ["LocalStore"]


class LocalStore:
    """A :class:`~chhaya.store.base.HeatStore` backed by JSON files.

    Not safe for concurrent writers. Local mode is one person, one site, one
    process — if you need more than that, use the DynamoDB store.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.root / f"{name}.json"

    def _read(self, name: str) -> list[dict[str, Any]]:
        path = self._path(name)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        return rows

    def _write(self, name: str, rows: list[dict[str, Any]]) -> None:
        self._path(name).write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _upsert(self, name: str, key: str, row: dict[str, Any]) -> None:
        rows = self._read(name)
        rows = [r for r in rows if r.get(key) != row[key]]
        rows.append(row)
        self._write(name, rows)

    # --- advisories -----------------------------------------------------

    def put_advisory(self, advisory: StandingAdvisory) -> None:
        self._upsert("advisories", "advisory_id", advisory.as_dict())

    def get_advisory(self, advisory_id: str) -> StandingAdvisory | None:
        for row in self._read("advisories"):
            if row["advisory_id"] == advisory_id:
                return advisory_from(row)
        return None

    def list_advisories(
        self,
        site_id: str,
        *,
        active_only: bool = False,
        now: datetime | None = None,
    ) -> list[StandingAdvisory]:
        advisories = [
            advisory_from(row) for row in self._read("advisories") if row["site_id"] == site_id
        ]
        if active_only:
            as_of = now or datetime.now()
            advisories = [a for a in advisories if a.is_active_at(as_of)]
        return sorted(advisories, key=lambda a: a.issued_at, reverse=True)

    def revoke_advisory(
        self,
        advisory_id: str,
        *,
        revoked_at: datetime,
        revoked_by: str,
        reason: str,
    ) -> StandingAdvisory:
        advisory = self.get_advisory(advisory_id)
        if advisory is None:
            raise KeyError(f"unknown advisory {advisory_id}")
        if advisory.revoked:
            raise ValueError(f"advisory {advisory_id} is already revoked")
        revoked = StandingAdvisory(
            site_id=advisory.site_id,
            action_id=advisory.action_id,
            band_at_issue=advisory.band_at_issue,
            wbgt_c=advisory.wbgt_c,
            issued_at=advisory.issued_at,
            expires_at=advisory.expires_at,
            issued_by=advisory.issued_by,
            approval_phrase=advisory.approval_phrase,
            reason=advisory.reason,
            policy_fingerprint=advisory.policy_fingerprint,
            advisory_id=advisory.advisory_id,
            revoked_at=revoked_at,
            revoked_by=revoked_by,
            revoke_reason=reason,
        )
        self.put_advisory(revoked)
        return revoked

    # --- action records -------------------------------------------------

    def put_action_record(self, record: ActionRecord) -> None:
        self._upsert("action_records", "record_id", record.as_dict())

    def list_action_records(
        self, site_id: str, *, since: datetime | None = None
    ) -> list[ActionRecord]:
        records = [
            record_from(row) for row in self._read("action_records") if row["site_id"] == site_id
        ]
        if since is not None:
            records = [r for r in records if r.proposed_at >= since]
        return sorted(records, key=lambda r: r.proposed_at, reverse=True)

    # --- exposure ledger ------------------------------------------------

    def append_exposure(self, event: ExposureEvent) -> None:
        rows = self._read("exposure")
        rows.append(event.as_dict())
        self._write("exposure", rows)

    def append_coverage(self, event: CoverageEvent) -> None:
        rows = self._read("coverage")
        rows.append(event.as_dict())
        self._write("coverage", rows)

    def list_exposure(self, site_id: str, *, since: datetime | None = None) -> list[ExposureEvent]:
        events = [exposure_from(row) for row in self._read("exposure") if row["site_id"] == site_id]
        if since is not None:
            events = [e for e in events if e.observed_at >= since]
        return sorted(events, key=lambda e: e.observed_at)

    def list_coverage(self, site_id: str, *, since: datetime | None = None) -> list[CoverageEvent]:
        events = [coverage_from(row) for row in self._read("coverage") if row["site_id"] == site_id]
        if since is not None:
            events = [e for e in events if e.started_at >= since]
        return sorted(events, key=lambda e: e.started_at)

    # --- pending proposals ----------------------------------------------

    def put_pending(self, pending: PendingProposal) -> None:
        self._upsert("pending", "proposal_id", pending.as_dict())

    def get_pending(self, proposal_id: str) -> PendingProposal | None:
        for row in self._read("pending"):
            if row["proposal_id"] == proposal_id:
                return pending_from(row)
        return None

    def list_pending(self, site_id: str, *, unresolved_only: bool = False) -> list[PendingProposal]:
        pending = [pending_from(row) for row in self._read("pending") if row["site_id"] == site_id]
        if unresolved_only:
            pending = [p for p in pending if not p.resolved]
        return sorted(pending, key=lambda p: p.proposed_at)
