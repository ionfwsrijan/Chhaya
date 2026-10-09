"""NIOSH work/rest schedule — the published table that needs no modelling.

Transcribed from NIOSH, *Criteria for a Recommended Standard: Occupational
Exposure to Heat and Hot Environments*, DHHS (NIOSH) Publication No. 2016-106,
Table 6-2 (page 76), which is the same table the agency later reprints as a
standalone flyer, *Heat Stress: Work/Rest Schedules*, DHHS (NIOSH) Publication
No. 2017-127. Table 6-2 is used as the source here because it is the fuller of
the two: the flyer prints humidity corrections only for 40 %, 50 % and 60 %,
whereas the criteria document also publishes the *subtractions* for dry air
(10 % and 20 %), and dry afternoons are exactly the hours when a construction
site in Hyderabad or Phoenix is still pouring concrete past the noon ban.

Everything about this engine is deliberately boring: an air temperature, two
additive corrections, and a lookup. No globe model, no wet-bulb fit, no
opinion. That is the point — it is the cross-check Chhaya runs against the
modelled WBGT, and if the two ever disagree the stricter one wins.

Assumptions stated by NIOSH for this table: workers are physically fit,
well-rested, fully hydrated, under 40, with adequate water intake, and the
baseline is 30 % relative humidity and natural ventilation with perceptible
air movement. The table is adapted from EPA (1993).

The three worked reference points Chhaya tests against are all printed in the
primary sources:
- heavy work at 104 F returns 20/40 (2017-127, and 2016-106 Table 6-2);
- moderate work at 108 F returns Caution;
- 90 F under partly cloudy skies at 50 % humidity adjusts to 103 F and
  therefore returns 30/30 for moderate work (the worked example printed on the
  2017-127 flyer).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from chhaya.domain.heat import HeatInputError, HeatInputs
from chhaya.domain.workload import WORKLOAD_NOTES, Workload

__all__ = [
    "FULL_SUN_F",
    "HUMIDITY_STEPS",
    "NO_SHADOW_F",
    "PARTLY_CLOUDY_F",
    "SCHEDULE_F",
    "SCHEDULE_MAX_F",
    "SCHEDULE_MIN_F",
    "SUPPORTED_WORKLOADS",
    "WORKLOAD_NOTES",
    "Decision",
    "WorkRest",
    "Workload",
    "c_to_f",
    "evaluate",
    "f_to_c",
    "humidity_adjustment_f",
    "sun_adjustment_f",
]


class Decision(StrEnum):
    """What the schedule says to do with this hour."""

    NORMAL = "normal"
    SCHEDULED = "scheduled"
    CAUTION = "caution"


@dataclass(frozen=True, slots=True)
class WorkRest:
    """One cell of the NIOSH table.

    Attributes:
        decision: ``normal`` to work without a special cycle, ``scheduled`` to
            follow ``work_min``/``rest_min``, ``caution`` to stop and get the
            worker out of the heat.
        work_min: Minutes of work per cycle, or ``None``.
        rest_min: Minutes of rest per cycle, or ``None``.
        source_temperature_f: The adjusted temperature that selected this cell.
        unadjusted_f: The dry-bulb temperature before sun and humidity
            corrections.
        adjustments_f: The corrections that were applied, as a display string.
    """

    decision: Decision
    work_min: int | None
    rest_min: int | None
    source_temperature_f: float
    unadjusted_f: float
    adjustments_f: str

    @property
    def label(self) -> str:
        if self.decision is Decision.NORMAL:
            return "Normal work"
        if self.decision is Decision.CAUTION:
            return "Caution — stop work"
        return f"{self.work_min}/{self.rest_min}"

    @property
    def is_stop(self) -> bool:
        return self.decision is Decision.CAUTION

    @property
    def duty_cycle(self) -> float:
        """Fraction of the hour a worker spends working, 0 to 1."""
        if self.decision is Decision.NORMAL:
            return 1.0
        if self.decision is Decision.CAUTION:
            return 0.0
        if self.work_min is None or self.rest_min is None:
            raise ValueError("scheduled cells must carry a work and rest duration")
        total = self.work_min + self.rest_min
        return self.work_min / total if total else 0.0


def _cycle(work: int, rest: int) -> tuple[Decision, int, int]:
    return Decision.SCHEDULED, work, rest


def _row(*cells: tuple[Decision, int, int] | str) -> dict[Workload, tuple[Decision, int, int]]:
    normal = (Decision.NORMAL, 0, 0)
    caution = (Decision.CAUTION, 0, 0)
    ordered: list[tuple[Decision, int, int]] = []
    for cell in cells:
        if cell == "normal":
            ordered.append(normal)
        elif cell == "caution":
            ordered.append(caution)
        else:
            assert isinstance(cell, tuple)
            ordered.append(cell)
    return dict(zip((Workload.LIGHT, Workload.MODERATE, Workload.HEAVY), ordered, strict=True))


# Exact transcription of NIOSH 2016-106 Table 6-2, rows 90-112 F. Verified
# against the CDC-hosted PDF of the criteria document rather than a search
# snippet, and cross-checked against the identical table reprinted in the
# 2017-127 flyer; six rows of the snippet Chhaya first transcribed from a
# search result were wrong (see docs/LEARNINGS.md).
SCHEDULE_F: dict[int, dict[Workload, tuple[Decision, int, int]]] = {
    90: _row("normal", "normal", "normal"),
    91: _row("normal", "normal", "normal"),
    92: _row("normal", "normal", "normal"),
    93: _row("normal", "normal", "normal"),
    94: _row("normal", "normal", "normal"),
    95: _row("normal", "normal", _cycle(45, 15)),
    96: _row("normal", "normal", _cycle(45, 15)),
    97: _row("normal", "normal", _cycle(40, 20)),
    98: _row("normal", "normal", _cycle(35, 25)),
    99: _row("normal", "normal", _cycle(35, 25)),
    100: _row("normal", _cycle(45, 15), _cycle(30, 30)),
    101: _row("normal", _cycle(40, 20), _cycle(30, 30)),
    102: _row("normal", _cycle(35, 25), _cycle(25, 35)),
    103: _row("normal", _cycle(30, 30), _cycle(20, 40)),
    104: _row("normal", _cycle(30, 30), _cycle(20, 40)),
    105: _row("normal", _cycle(25, 35), _cycle(15, 45)),
    106: _row(_cycle(45, 15), _cycle(20, 40), "caution"),
    107: _row(_cycle(40, 20), _cycle(15, 45), "caution"),
    108: _row(_cycle(35, 25), "caution", "caution"),
    109: _row(_cycle(30, 30), "caution", "caution"),
    110: _row(_cycle(15, 45), "caution", "caution"),
    111: _row("caution", "caution", "caution"),
    112: _row("caution", "caution", "caution"),
}

SCHEDULE_MIN_F = min(SCHEDULE_F)
SCHEDULE_MAX_F = max(SCHEDULE_F)

FULL_SUN_F = 13.0
PARTLY_CLOUDY_F = 7.0
NO_SHADOW_F = 0.0

# NIOSH 2016-106 Table 6-2 footnote, transcribed in full. The table is
# relative to a 30 % baseline, so dry air *subtracts* and humid air adds. The
# 2017-127 flyer omits the two dry-air steps, which is why Chhaya cites the
# criteria document rather than the flyer.
HUMIDITY_STEPS: tuple[tuple[float, float], ...] = (
    (10.0, -8.0),
    (20.0, -4.0),
    (30.0, 0.0),
    (40.0, 3.0),
    (50.0, 6.0),
    (60.0, 9.0),
)

# Below this irradiance the sun is not on the site (night, or effectively so).
NIGHT_WM2 = 50.0
# Cloud-cover cut points for the three sun corrections NIOSH publishes.
FULL_SUN_CLOUD_MAX = 25.0
NO_SHADOW_CLOUD_MIN = 95.0

# Table 6-2 only tabulates these three workloads. "Resting" cannot exceed the
# schedule by construction, and "very heavy" sits above the table's top band,
# so Chhaya refuses to guess at either rather than silently clamping.
SUPPORTED_WORKLOADS: frozenset[Workload] = frozenset(
    {Workload.LIGHT, Workload.MODERATE, Workload.HEAVY}
)


def c_to_f(celsius: float) -> float:
    return celsius * 9.0 / 5.0 + 32.0


def f_to_c(fahrenheit: float) -> float:
    return (fahrenheit - 32.0) * 5.0 / 9.0


def sun_adjustment_f(inputs: HeatInputs) -> tuple[float, str]:
    """NIOSH's three-way sun correction, in Fahrenheit.

    NIOSH words the categories by shadow: *full sun (no clouds)* +13 F,
    *partly cloudy/overcast* +7 F, *no shadows visible, in the shade, or at
    night* +0. Cloud cover maps onto those words directly; when a forecast
    supplies only irradiance the same three bands are recovered from it.
    """
    if inputs.shortwave_wm2 < NIGHT_WM2:
        return NO_SHADOW_F, "night"
    if inputs.cloud_cover is not None:
        if inputs.cloud_cover <= FULL_SUN_CLOUD_MAX:
            return FULL_SUN_F, "full sun"
        if inputs.cloud_cover >= NO_SHADOW_CLOUD_MIN:
            return NO_SHADOW_F, "no shadows"
        return PARTLY_CLOUDY_F, "partly cloudy"
    if inputs.shortwave_wm2 >= 700.0:
        return FULL_SUN_F, "full sun"
    if inputs.shortwave_wm2 >= 250.0:
        return PARTLY_CLOUDY_F, "partly cloudy"
    return NO_SHADOW_F, "weak sun"


def humidity_adjustment_f(relative_humidity: float) -> tuple[float, str]:
    """NIOSH humidity correction, from the Table 6-2 footnote.

    The table is written against a 30 % relative-humidity baseline, so dry air
    *subtracts* from the adjusted temperature and humid air adds: -8 F at 10 %,
    -4 F at 20 %, no adjustment at 30 %, +3 F at 40 %, +6 F at 50 %, +9 F at
    60 % or more. NIOSH prints the corrections at those six points only, so a
    reading between two of them takes the last published step at or below it —
    the same convention a printed correction table asks a human to apply.
    """
    step = 0.0
    label = "30 % baseline"
    for threshold, adjustment in HUMIDITY_STEPS:
        if relative_humidity >= threshold:
            step = adjustment
            label = f"{threshold:.0f} %"
    return step, label


def evaluate(inputs: HeatInputs, workload: Workload) -> WorkRest:
    """Run one hour through the NIOSH work/rest schedule.

    Raises:
        HeatInputError: if the weather falls outside the engine's stated
            validity range, or if the workload is one Table 6-2 does not
            tabulate. Chhaya would rather fail loudly than invent a duty
            cycle for a worker.
    """
    if workload not in SUPPORTED_WORKLOADS:
        supported = ", ".join(
            w.value for w in sorted(SUPPORTED_WORKLOADS, key=lambda w: w.sort_key)
        )
        raise HeatInputError(
            f"workload {workload.value!r} is not tabulated by NIOSH 2016-106 "
            f"Table 6-2, which covers only: {supported}"
        )
    inputs.validate()
    unadjusted = c_to_f(inputs.air_c)
    sun_f, sun_label = sun_adjustment_f(inputs)
    humidity_f, humidity_label = humidity_adjustment_f(inputs.relative_humidity)
    adjusted = unadjusted + sun_f + humidity_f

    row_key = round(adjusted)
    clamped = row_key != max(SCHEDULE_MIN_F, min(SCHEDULE_MAX_F, row_key))
    row_key = max(SCHEDULE_MIN_F, min(SCHEDULE_MAX_F, row_key))
    cell = SCHEDULE_F[row_key][workload]
    decision, work_min, rest_min = cell
    adjustments = (
        f"base {unadjusted:.0f} F + {sun_f:+.0f} F ({sun_label})"
        f" + {humidity_f:+.0f} F ({humidity_label})"
    )
    if clamped:
        adjustments += f" (adjusted {adjusted:.0f} F clamped to table range)"
    return WorkRest(
        decision=decision,
        work_min=work_min,
        rest_min=rest_min,
        source_temperature_f=adjusted,
        unadjusted_f=unadjusted,
        adjustments_f=adjustments,
    )
