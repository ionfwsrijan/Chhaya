"""The exposure ledger: what the site actually experienced, and what was covered.

Two kinds of fact live here, and Chhaya keeps them apart on purpose:

- :class:`ExposureEvent` is *observed*: the site sat in a given band for a
  given number of minutes with a given crew size. This is measurement.
- :class:`CoverageEvent` is *action*: an approved, verified action kept the
  crew out of harm's way for a period. This is also measurement.

The headline number — worker-hours of hazardous exposure avoided — is a
*modelled counterfactual* built from the two, and :meth:`ExposureLedger.avoided_hazardous_worker_hours`
states its assumption in the returned dict rather than burying it. The
assumption is that without the verified action the crew would have worked
the full interval in the Stop band; that is the number a site manager needs
to justify a rest cycle to a contractor, and it is labelled as an estimate
everywhere it appears.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from chhaya.domain.bands import Band

__all__ = ["CoverageEvent", "ExposureEvent", "ExposureLedger"]


@dataclass(frozen=True, slots=True)
class ExposureEvent:
    """One observed interval in one band.

    Attributes:
        event_id: Unique id (generated if not supplied).
        site_id: Which site this happened at.
        observed_at: Start of the interval.
        band: The verdict band the site was in.
        wbgt_c: The WBGT that produced the band.
        workload: The crew's metabolic band during the interval.
        workers: How many people were on site.
        minutes_observed: How long the site sat in this band.
    """

    site_id: str
    observed_at: datetime
    band: Band
    wbgt_c: float
    workload: str
    workers: int
    minutes_observed: float
    event_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            object.__setattr__(self, "event_id", uuid4().hex)

    def worker_hours(self) -> float:
        return self.workers * self.minutes_observed / 60.0

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "site_id": self.site_id,
            "observed_at": self.observed_at.isoformat(),
            "band": self.band.value,
            "wbgt_c": round(self.wbgt_c, 2),
            "workload": self.workload,
            "workers": self.workers,
            "minutes_observed": round(self.minutes_observed, 1),
            "worker_hours": round(self.worker_hours(), 3),
        }


@dataclass(frozen=True, slots=True)
class CoverageEvent:
    """One interval during which a verified action protected the crew.

    Attributes:
        event_id: Unique id.
        site_id: Which site.
        action_id: The action that provided the cover.
        advisory_id: The standing advisory the action created, if any.
        started_at: When the cover began.
        minutes_covered: How long the cover lasted.
        workers: How many people were covered.
        band_at_issue: The band that triggered the action — the hazard the
            cover is measured against.
    """

    site_id: str
    action_id: str
    started_at: datetime
    minutes_covered: float
    workers: int
    band_at_issue: Band
    advisory_id: str | None = None
    event_id: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            object.__setattr__(self, "event_id", uuid4().hex)

    def worker_hours(self) -> float:
        return self.workers * self.minutes_covered / 60.0

    def as_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "site_id": self.site_id,
            "action_id": self.action_id,
            "advisory_id": self.advisory_id,
            "started_at": self.started_at.isoformat(),
            "minutes_covered": round(self.minutes_covered, 1),
            "workers": self.workers,
            "band_at_issue": self.band_at_issue.value,
            "worker_hours": round(self.worker_hours(), 3),
        }


@dataclass
class ExposureLedger:
    """In-memory view over the store's exposure and coverage events.

    The store owns the data; this class owns the arithmetic and the honesty
    about what the arithmetic assumes. Both event lists are passed in, so the
    ledger works identically over the local JSON store and DynamoDB.
    """

    exposure: list[ExposureEvent]
    coverage: list[CoverageEvent]

    def worker_hours_in_band(self, band: Band) -> float:
        return sum(e.worker_hours() for e in self.exposure if e.band is band)

    def total_hazardous_worker_hours(self) -> float:
        """Worker-hours the site actually sat in the Stop band."""
        return self.worker_hours_in_band(Band.STOP)

    def covered_worker_hours(self) -> float:
        """Worker-hours kept out of the Stop band by a verified action."""
        return sum(c.worker_hours() for c in self.coverage if c.band_at_issue is Band.STOP)

    def avoided_hazardous_worker_hours(self) -> float:
        """Modelled counterfactual: hazardous hours avoided by verified actions.

        Equals the covered worker-hours. The assumption — that without the
        action the crew would have worked the full covered interval in the
        Stop band — is stated in :meth:`as_summary` wherever this number is
        reported, because a counterfactual presented as a measurement is a
        lie.
        """
        return self.covered_worker_hours()

    def as_summary(self) -> dict[str, object]:
        """The tally a supervisor or a judge reads, with assumptions attached."""
        by_band = {band.value: round(self.worker_hours_in_band(band), 2) for band in Band}
        return {
            "observed_worker_hours_by_band": by_band,
            "hazardous_worker_hours_observed": round(self.total_hazardous_worker_hours(), 2),
            "hazardous_worker_hours_covered": round(self.covered_worker_hours(), 2),
            "hazardous_worker_hours_avoided_estimate": round(
                self.avoided_hazardous_worker_hours(), 2
            ),
            "avoided_estimate_assumption": (
                "Estimates that, without each verified action, the crew would "
                "have worked the full covered interval in the Stop band. "
                "Observed and covered figures are measurements; avoided is a "
                "counterfactual model."
            ),
            "exposure_intervals": len(self.exposure),
            "coverage_intervals": len(self.coverage),
        }
