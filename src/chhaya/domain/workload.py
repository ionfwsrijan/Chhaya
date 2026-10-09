"""Workload categories and the metabolic rates that go with them.

One vocabulary, shared by both heat-stress engines, because the NIOSH
work/rest schedule and the NIOSH WBGT screening limits use the same words for
the same jobs and it would be a silent safety bug if the two engines disagreed
about what "heavy" means.

Metabolic rates come from NIOSH, *Criteria for a Recommended Standard:
Occupational Exposure to Heat and Hot Environments*, DHHS (NIOSH) Publication
No. 2016-106, Table 5-1 (page 70), which tabulates the metabolic bands each
standard uses. The conversion the criteria document states in section 1.1.3 is
1 kcal*h^-1 = 1.16 W; Table 5-1 itself rounds to 1.17, and both are reproduced
here so the rounding is visible rather than hidden.

Each workload also carries the plain-language task list NIOSH prints in the
2017-127 work/rest schedule flyer, because a site supervisor choosing a
workload on a phone needs to match a job description, not a wattage.
"""

from __future__ import annotations

from enum import StrEnum

from chhaya.domain.heat import HeatInputError

# NIOSH 2016-106 section 1.1.3: "1 kcal*h^-1 = 1.16 W".
KCAL_H_TO_W = 1.16
# The same document's Table 5-1 prints 117 W for 100 kcal*h^-1, i.e. 1.163.
KCAL_H_TO_W_TABLE = 1.163


class Workload(StrEnum):
    """Metabolic categories, ordered from coolest to hottest."""

    RESTING = "resting"
    LIGHT = "light"
    MODERATE = "moderate"
    HEAVY = "heavy"
    VERY_HEAVY = "very_heavy"

    @property
    def sort_key(self) -> int:
        return _ORDER[self]


_ORDER: dict[Workload, int] = {
    Workload.RESTING: 0,
    Workload.LIGHT: 1,
    Workload.MODERATE: 2,
    Workload.HEAVY: 3,
    Workload.VERY_HEAVY: 4,
}

# NIOSH 2016-106 Table 5-1, NIOSH column, as kcal*h^-1. The lower bound of
# each band is exclusive in the source ("201-300"), so the bands are stored as
# (upper bound inclusive) and the band below is recovered by subtracting one.
# The upper bound is what Chhaya uses, because a higher metabolic rate gives a
# *lower* WBGT limit and the strict reading of a band is the safe one.
_BAND_UPPER_KCAL_H: dict[Workload, float] = {
    Workload.RESTING: 100.0,
    Workload.LIGHT: 200.0,
    Workload.MODERATE: 300.0,
    Workload.HEAVY: 400.0,
    Workload.VERY_HEAVY: 500.0,
}

# The same bands as printed in NIOSH 2016-106 Table 5-1, NIOSH column, in
# kcal*h^-1 with the watts the table prints beside them. Kept verbatim for the
# cross-standard comparison the console shows.
NIOSH_BAND_KCAL_H: dict[Workload, tuple[float, float]] = {
    Workload.RESTING: (0.0, 100.0),
    Workload.LIGHT: (100.0, 200.0),
    Workload.MODERATE: (201.0, 300.0),
    Workload.HEAVY: (301.0, 400.0),
    Workload.VERY_HEAVY: (401.0, 500.0),
}

NIOSH_BAND_W_TABLE: dict[Workload, tuple[int, int]] = {
    Workload.RESTING: (0, 117),
    Workload.LIGHT: (117, 233),
    Workload.MODERATE: (234, 349),
    Workload.HEAVY: (350, 465),
    Workload.VERY_HEAVY: (466, 580),
}

# ACGIH's bands from the same table, which do not line up with NIOSH's.
ACGIH_BAND_KCAL_H: dict[Workload, tuple[float | None, float | None]] = {
    Workload.RESTING: (None, 100.0),
    Workload.LIGHT: (100.0, 200.0),
    Workload.MODERATE: (201.0, 350.0),
    Workload.HEAVY: (301.0, None),
    Workload.VERY_HEAVY: (350.0, 500.0),
}


def upper_metabolic_kcal_h(workload: Workload) -> float:
    """Upper bound of the NIOSH band, kcal*h^-1 (the conservative choice)."""
    return _BAND_UPPER_KCAL_H[workload]


def upper_metabolic_w(workload: Workload) -> float:
    """Upper bound of the NIOSH band, watts, using NIOSH's own 1.16 factor."""
    return upper_metabolic_kcal_h(workload) * KCAL_H_TO_W


def metabolic_w(kcal_h: float) -> float:
    """Convert kcal*h^-1 to watts using NIOSH 2016-106 section 1.1.3."""
    return kcal_h * KCAL_H_TO_W


WORKLOAD_NOTES: dict[Workload, str] = {
    Workload.RESTING: "Seated, standing still, or very light hand work with no load carried.",
    Workload.LIGHT: "Operating equipment, inspection work, walking on flat "
    "level ground, light hand tools (wrench, pliers), travel by conveyance.",
    Workload.MODERATE: "Jack-leg drilling, installing ground support, loading "
    "explosives, carrying equipment or supplies weighing 20-40 pounds, hand "
    "tools (shovel, fin-hoe, scaling bar) for short periods.",
    Workload.HEAVY: "Climbing, carrying equipment or supplies weighing 40 "
    "pounds or more, installing utilities, hand tools for extended periods.",
    Workload.VERY_HEAVY: "Sustained work above the NIOSH heavy band. The 2016-106 "
    "criteria screen these workers by WBGT alone; there is no work/rest row "
    "for them in the schedule.",
}

WORKLOAD_LABELS: dict[Workload, str] = {
    Workload.RESTING: "Resting",
    Workload.LIGHT: "Light work",
    Workload.MODERATE: "Moderate work",
    Workload.HEAVY: "Heavy work",
    Workload.VERY_HEAVY: "Very heavy work",
}


def parse_workload(value: str) -> Workload:
    """Parse a workload from a policy file or an API request.

    Accepts the canonical snake_case values plus the handful of synonyms a
    site supervisor is likely to type, so that a mistyped policy file fails
    loudly with a helpful message rather than silently defaulting to the
    coolest band. A trailing "work" or "workload" is dropped first, because
    "Heavy Work" and "very heavy workload" are how people actually write it.
    """
    normalised = value.strip().lower().replace("-", "_").replace(" ", "_")
    for suffix in ("_workload", "_work"):
        stripped = normalised[: -len(suffix)]
        if normalised.endswith(suffix) and stripped:
            normalised = stripped
            break
    if normalised in _SYNONYMS:
        return _SYNONYMS[normalised]
    try:
        return Workload(normalised)
    except ValueError:
        allowed = ", ".join(w.value for w in Workload)
        raise HeatInputError(f"unknown workload {value!r}; expected one of: {allowed}") from None


_SYNONYMS: dict[str, Workload] = {
    "rest": Workload.RESTING,
    "sedentary": Workload.RESTING,
    "sitting": Workload.RESTING,
    "standing": Workload.RESTING,
    "walk": Workload.LIGHT,
    "walking": Workload.LIGHT,
    "inspection": Workload.LIGHT,
    "tool_light": Workload.LIGHT,
    "medium": Workload.MODERATE,
    "carrying_20_40_lb": Workload.MODERATE,
    "carrying_20_40_lbs": Workload.MODERATE,
    "hard": Workload.HEAVY,
    "carrying_40_lb": Workload.HEAVY,
    "carrying_40_lbs": Workload.HEAVY,
    "climbing": Workload.HEAVY,
    "veryhard": Workload.VERY_HEAVY,
    "extreme": Workload.VERY_HEAVY,
}
