"""Verdict bands, and the rule that the stricter engine wins.

Chhaya runs two independent published screens over the same hour of weather:

- the NIOSH work/rest schedule (:mod:`chhaya.domain.niosh`), which is an air
  temperature, two corrections and a lookup, with no model at all; and
- the NIOSH WBGT screening limits (:mod:`chhaya.domain.screening`), which
  needs a modelled or measured WBGT and a metabolic rate.

They can disagree, and when they do the disagreement is information. A hot,
dry afternoon in Hyderabad can push the air-temperature schedule into Caution
while the WBGT screen stays under its limit, because the humidity correction
subtracts. A still, humid coastal afternoon can do the reverse. Chhaya never
averages them and never picks the convenient one: the stricter verdict wins,
and the losing engine's number is kept in the record so a supervisor can see
*why* the site stopped.

The band names come from NIOSH's own three-way vocabulary in Table 6-2:
``Normal``, a work/rest cycle, and ``Caution`` (which the criteria document
glosses as "High levels of heat stress; consider rescheduling activities").
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from chhaya.domain.niosh import Decision, WorkRest
from chhaya.domain.screening import STANDARD_SPREAD_C, Screening
from chhaya.domain.workload import Workload

__all__ = [
    "Band",
    "EngineFinding",
    "Verdict",
    "band_from_margin",
    "band_from_schedule",
    "merge",
]

# NIOSH's own wording for the top band, quoted from 2016-106 Table 6-2.
STOP_REASON = "High levels of heat stress; consider rescheduling activities."

# Default width of the amber band, in degrees C. Sized on the widest spread
# between any two of the five standards NIOSH 2016-106 Table 5-1 compares
# inside a single metabolic band (2.0 C, in the very-heavy band). Inside that
# distance of a limit the published standards disagree with each other, which
# is the point at which a human supervisor should look rather than a machine
# deciding alone. Sites can override this per workload in the policy file.
DEFAULT_CAUTION_MARGIN_C = 2.0


class Band(StrEnum):
    """How bad this hour is, from the three-way vocabulary NIOSH uses."""

    NORMAL = "normal"
    CAUTION = "caution"
    STOP = "stop"

    @property
    def sort_key(self) -> int:
        return _BAND_ORDER[self]

    @property
    def label(self) -> str:
        return _BAND_LABELS[self]


_BAND_ORDER: dict[Band, int] = {Band.NORMAL: 0, Band.CAUTION: 1, Band.STOP: 2}

_BAND_LABELS: dict[Band, str] = {
    Band.NORMAL: "Normal work",
    Band.CAUTION: "Caution — restrict work",
    Band.STOP: "Stop work",
}


def band_from_schedule(decision: Decision) -> Band:
    """Map a NIOSH Table 6-2 cell onto a verdict band.

    ``normal`` means carry on. ``scheduled`` is a real restriction on what a
    crew may do, so it is Caution rather than Normal, even though work
    continues. ``caution`` in the table is the criteria document's own stop
    wording, so it maps to Stop.
    """
    if decision is Decision.NORMAL:
        return Band.NORMAL
    if decision is Decision.SCHEDULED:
        return Band.CAUTION
    return Band.STOP


def band_from_margin(
    margin_c: float,
    caution_margin_c: float = DEFAULT_CAUTION_MARGIN_C,
) -> Band:
    """Map a WBGT screening margin onto a verdict band.

    Args:
        margin_c: ``limit - wbgt``. Positive is under the limit.
        caution_margin_c: Width of the amber band below the limit.
    """
    if margin_c <= 0.0:
        return Band.STOP
    if margin_c <= caution_margin_c:
        return Band.CAUTION
    return Band.NORMAL


def caution_margin_for(workload: Workload) -> float:
    """The amber-band width to use for this workload.

    Uses the widest disagreement between the five standards in Table 5-1 for
    the workload's band, falling back to the default where Table 5-1 does not
    publish a spread.
    """
    return STANDARD_SPREAD_C.get(workload, DEFAULT_CAUTION_MARGIN_C)


@dataclass(frozen=True, slots=True)
class EngineFinding:
    """What one engine said about one hour.

    Attributes:
        engine: Short identifier, ``niosh_schedule`` or ``wbgt_screen``.
        band: The band this engine produced.
        margin_c: Margin in degrees C where the engine has one, else ``None``.
        summary: One sentence a supervisor can read aloud.
        detail: The engine's own arithmetic, kept so the verdict is auditable.
        citation: The published source the engine implements.
    """

    engine: str
    band: Band
    margin_c: float | None
    summary: str
    detail: str
    citation: str


def finding_from_schedule(schedule: WorkRest) -> EngineFinding:
    """Build the schedule engine's finding from its output."""
    band = band_from_schedule(schedule.decision)
    if schedule.decision is Decision.CAUTION:
        summary = f"{STOP_REASON} Adjusted temperature {schedule.source_temperature_f:.0f} F."
    elif schedule.decision is Decision.SCHEDULED:
        summary = (
            f"Work {schedule.work_min} min, rest {schedule.rest_min} min each hour. "
            f"Adjusted temperature {schedule.source_temperature_f:.0f} F."
        )
    else:
        summary = f"Normal work. Adjusted temperature {schedule.source_temperature_f:.0f} F."
    return EngineFinding(
        engine="niosh_schedule",
        band=band,
        margin_c=None,
        summary=summary,
        detail=schedule.adjustments_f,
        citation="NIOSH 2016-106 Table 6-2",
    )


def finding_from_screening(screening: Screening, caution_margin_c: float) -> EngineFinding:
    """Build the WBGT screen's finding from its output."""
    band = band_from_margin(screening.margin_c, caution_margin_c)
    if band is Band.STOP:
        summary = (
            f"WBGT {screening.wbgt_c:.1f} C is at or above the "
            f"{screening.acclimatization.value} limit of {screening.limit_c:.1f} C."
        )
    elif band is Band.CAUTION:
        summary = (
            f"WBGT {screening.wbgt_c:.1f} C is within {caution_margin_c:.1f} C of the "
            f"{screening.acclimatization.value} limit of {screening.limit_c:.1f} C."
        )
    else:
        summary = (
            f"WBGT {screening.wbgt_c:.1f} C is {screening.margin_c:.1f} C under the "
            f"{screening.acclimatization.value} limit of {screening.limit_c:.1f} C."
        )
    band_note = ""
    if screening.band_limit_c is not None and screening.band_standard is not None:
        band_note = (
            f" Lowest published Table 5-1 value for this band is "
            f"{screening.band_limit_c:.1f} C ({screening.band_standard.value})."
        )
    return EngineFinding(
        engine="wbgt_screen",
        band=band,
        margin_c=screening.margin_c,
        summary=summary + band_note,
        detail=screening.limit_source,
        citation="NIOSH 2016-106 s8.1 and Table 5-1",
    )


@dataclass(frozen=True, slots=True)
class Verdict:
    """The merged verdict for one hour at one site.

    Attributes:
        band: The strictest band any engine produced.
        deciding_engine: The engine that produced it.
        findings: Every engine's finding, in evaluation order.
        reason: Why the deciding engine won, in one sentence.
    """

    band: Band
    deciding_engine: str
    findings: tuple[EngineFinding, ...]
    reason: str

    @property
    def is_stop(self) -> bool:
        return self.band is Band.STOP

    @property
    def is_work_allowed(self) -> bool:
        return self.band is not Band.STOP

    def as_dict(self) -> dict[str, object]:
        return {
            "band": self.band.value,
            "label": self.band.label,
            "deciding_engine": self.deciding_engine,
            "reason": self.reason,
            "findings": [
                {
                    "engine": f.engine,
                    "band": f.band.value,
                    "margin_c": (None if f.margin_c is None else round(f.margin_c, 2)),
                    "summary": f.summary,
                    "detail": f.detail,
                    "citation": f.citation,
                }
                for f in self.findings
            ],
        }


def merge(findings: tuple[EngineFinding, ...]) -> Verdict:
    """Merge engine findings with the stricter-wins rule.

    The highest band wins. Ties are broken by the engine that produced the
    more restrictive *margin* where one exists, and otherwise by evaluation
    order, so the result is deterministic and the deciding engine can always
    be named.
    """
    if not findings:
        raise ValueError("cannot merge an empty set of engine findings")
    strictest = max(f.band.sort_key for f in findings)
    contenders = [f for f in findings if f.band.sort_key == strictest]

    def tie_break(finding: EngineFinding) -> tuple[float, int]:
        # A smaller (or missing) margin is more restrictive, so it sorts first.
        margin = finding.margin_c if finding.margin_c is not None else float("-inf")
        return (margin, -findings.index(finding))

    winner = min(contenders, key=tie_break)
    if len(contenders) == 1:
        reason = f"{winner.engine} is the strictest engine at {winner.band.value}."
    else:
        others = ", ".join(
            f"{f.engine} also at {f.band.value}" for f in contenders if f is not winner
        )
        reason = (
            f"{winner.engine} and {others} both reach {winner.band.value}; "
            "the stricter-wins rule keeps the most restrictive reading."
        )
    return Verdict(
        band=winner.band,
        deciding_engine=winner.engine,
        findings=findings,
        reason=reason,
    )
