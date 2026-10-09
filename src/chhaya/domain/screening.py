"""WBGT screening limits: NIOSH RAL/REL, plus the cross-standard comparison.

Two things live here, both transcribed from NIOSH, *Criteria for a
Recommended Standard: Occupational Exposure to Heat and Hot Environments*,
DHHS (NIOSH) Publication No. 2016-106, and both public domain.

1. The closed-form Recommended Alert Limit and Recommended Exposure Limit
   (section 8.1, page 93)::

       RAL [C-WBGT] = 59.9 - 14.1 * log10(M [W])
       REL [C-WBGT] = 56.7 - 11.5 * log10(M [W])

   RAL protects *unacclimatized* workers, REL protects *acclimatized* ones.
   Both are written for a full 60 minutes of work in each hour, which is the
   strictest duty cycle NIOSH publishes; shorter work periods are allowed
   higher limits on Figures 8-1 and 8-2, and Chhaya deliberately does not
   take that relaxation by default. A supervisor who wants it must ask for it
   in the policy file.

2. Table 5-1 (page 70), which sets five standards' WBGT screening values side
   by side for the same five metabolic bands. Chhaya reads the *lowest*
   published value in each band as the screen, because when ACGIH says 26.7 C
   and AIHA says 25.0 C for the same heavy job, the safe answer to "is this
   over the limit" is the lower of the two.

Calibration notes, all of which are reproduced as tests:

- The REL regression reproduces the document's own worked example (page 4)
  to within 0.34 C, and its own Table 5-1 NIOSH column to within 1.0 C across
  all five bands. The worked example is read off a printed figure and the
  regression is a fit to the same data, so a small gap is expected.
- RAL sits 2.2 to 4.4 C below REL at every metabolic rate, which is the
  direction safety requires: an unacclimatized worker gets a lower limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from chhaya.domain.heat import HeatInputError
from chhaya.domain.workload import (
    ACGIH_BAND_KCAL_H,
    NIOSH_BAND_KCAL_H,
    NIOSH_BAND_W_TABLE,
    WORKLOAD_LABELS,
    Workload,
    upper_metabolic_w,
)

REL_INTERCEPT = 56.7
REL_SLOPE = 11.5
RAL_INTERCEPT = 59.9
RAL_SLOPE = 14.1

# The smallest metabolic rate the log10 fit is meaningful at. Below about
# 50 W the regression extrapolates past the data it was fitted to.
MIN_METABOLIC_W = 50.0
MAX_METABOLIC_W = 1000.0

# MSHA defines a hot worksite as a WBGT above this (2016-106 section 8.5.4).
MSHA_HOT_SITE_WBGT_C = 26.0


class Acclimatization(StrEnum):
    """Whether the crew has been working in the heat long enough to adapt."""

    ACCLIMATIZED = "acclimatized"
    UNACCLIMATIZED = "unacclimatized"


class Standard(StrEnum):
    """The five standards NIOSH 2016-106 Table 5-1 compares."""

    ACGIH = "acgih"
    AIHA = "aiha"
    OSHA = "osha"
    ISO = "iso"
    NIOSH = "niosh"


STANDARD_LABELS: dict[Standard, str] = {
    Standard.ACGIH: "ACGIH TLV",
    Standard.AIHA: "AIHA",
    Standard.OSHA: "OSHA SACHS",
    Standard.ISO: "ISO 7243",
    Standard.NIOSH: "NIOSH REL",
}

STANDARD_NOTES: dict[Standard, str] = {
    Standard.ACGIH: "American Conference of Governmental Industrial Hygienists "
    "Threshold Limit Value, the most widely cited industrial screening value.",
    Standard.AIHA: "American Industrial Hygiene Association summary; NIOSH "
    "notes the values are 'basically equivalent' across standards.",
    Standard.OSHA: "Occupational Safety and Health Administration Standards "
    "Advisory Committee on Heat Stress (SACHS, 1973). Never promulgated as a "
    "standard, which is precisely the gap Chhaya exists to fill.",
    Standard.ISO: "ISO 7243, Ergonomics of the thermal environment: assessment "
    "of heat stress using the WBGT index.",
    Standard.NIOSH: "National Institute for Occupational Safety and Health "
    "Recommended Exposure Limit for acclimatized workers.",
}

# Table 5-1, acclimatized workers. Where a standard prints both a low- and a
# high-velocity figure, the low-velocity (stricter) one is stored. A missing
# entry means the standard does not tabulate that band in Table 5-1.
CROSS_STANDARD_WBGT_C: dict[Standard, dict[Workload, float]] = {
    Standard.ACGIH: {
        Workload.RESTING: 32.2,
        Workload.LIGHT: 30.0,
        Workload.MODERATE: 26.7,
        Workload.HEAVY: 26.1,
        Workload.VERY_HEAVY: 25.0,
    },
    Standard.AIHA: {
        Workload.RESTING: 33.0,
        Workload.LIGHT: 30.0,
        Workload.MODERATE: 26.7,
        Workload.HEAVY: 25.0,
        Workload.VERY_HEAVY: 23.0,
    },
    Standard.OSHA: {
        Workload.LIGHT: 30.0,
        Workload.MODERATE: 27.8,
        Workload.HEAVY: 26.0,
        Workload.VERY_HEAVY: 25.0,
    },
    Standard.ISO: {
        Workload.LIGHT: 30.0,
        Workload.MODERATE: 28.0,
    },
    Standard.NIOSH: {
        Workload.LIGHT: 30.0,
        Workload.MODERATE: 28.0,
    },
}

# Table 5-1's high-velocity figures, for the console's comparison view.
CROSS_STANDARD_WBGT_HIGH_VELOCITY_C: dict[Standard, dict[Workload, float]] = {
    Standard.OSHA: {
        Workload.LIGHT: 32.2,
        Workload.MODERATE: 30.6,
    },
    Standard.ACGIH: {
        Workload.HEAVY: 28.9,
    },
    Standard.AIHA: {
        Workload.HEAVY: 26.0,
        Workload.VERY_HEAVY: 25.0,
    },
}

# The widest spread between any two standards inside a single band, in
# degrees C. This is the number Chhaya's amber band is sized on: within this
# distance of a limit, the published standards disagree about whether the hour
# is safe, which is exactly when a human should look rather than a machine
# deciding on its own.
STANDARD_SPREAD_C: dict[Workload, float] = {
    Workload.RESTING: 0.8,
    Workload.LIGHT: 0.0,
    Workload.MODERATE: 1.3,
    Workload.HEAVY: 1.1,
    Workload.VERY_HEAVY: 2.0,
}


def _checked_metabolic_w(metabolic_w: float) -> float:
    if not MIN_METABOLIC_W <= metabolic_w <= MAX_METABOLIC_W:
        raise HeatInputError(
            f"metabolic rate {metabolic_w} W outside the range the NIOSH "
            f"RAL/REL regressions were fitted over ({MIN_METABOLIC_W}-"
            f"{MAX_METABOLIC_W} W)"
        )
    return metabolic_w


def rel_wbgt_c(metabolic_w: float) -> float:
    """NIOSH REL, degrees C WBGT, for an *acclimatized* worker at 60 min/h.

    NIOSH 2016-106 section 8.1: ``REL = 56.7 - 11.5 * log10(M)`` with M in
    watts.
    """
    return REL_INTERCEPT - REL_SLOPE * math.log10(_checked_metabolic_w(metabolic_w))


def ral_wbgt_c(metabolic_w: float) -> float:
    """NIOSH RAL, degrees C WBGT, for an *unacclimatized* worker at 60 min/h.

    NIOSH 2016-106 section 8.1: ``RAL = 59.9 - 14.1 * log10(M)`` with M in
    watts. Always lower than the REL for the same workload, because a worker
    who has not yet adapted to the heat should be given a smaller heat load.
    """
    return RAL_INTERCEPT - RAL_SLOPE * math.log10(_checked_metabolic_w(metabolic_w))


def limit_wbgt_c(metabolic_w: float, acclimatization: Acclimatization) -> float:
    """The NIOSH regression limit for this crew and this metabolic rate."""
    if acclimatization is Acclimatization.ACCLIMATIZED:
        return rel_wbgt_c(metabolic_w)
    return ral_wbgt_c(metabolic_w)


def lowest_published_wbgt_c(workload: Workload) -> tuple[float, Standard]:
    """The strictest value Table 5-1 publishes for this band, and who said it.

    Returns the lowest low-velocity WBGT any of the five standards tabulates
    for the workload, and the standard that set it. If two standards tie, the
    first in :class:`Standard` declaration order is named.
    """
    published = [
        (limit, standard)
        for standard in Standard
        if (limit := CROSS_STANDARD_WBGT_C[standard].get(workload)) is not None
    ]
    if not published:
        raise HeatInputError(
            f"no standard in NIOSH 2016-106 Table 5-1 tabulates a WBGT limit "
            f"for workload {workload.value!r}"
        )
    return min(published, key=lambda pair: (pair[0], pair[1].value))


@dataclass(frozen=True, slots=True)
class Screening:
    """One WBGT reading screened against one published limit.

    Attributes:
        wbgt_c: The WBGT that was screened, degrees Celsius.
        limit_c: The limit it was screened against.
        margin_c: ``limit_c - wbgt_c``. Positive means under the limit.
        metabolic_w: The metabolic rate used, watts.
        acclimatization: Which NIOSH regression produced the limit.
        limit_source: Human-readable citation for the limit.
        band_limit_c: The lowest of the five Table 5-1 band values, if the
            workload is tabulated there; otherwise ``None``.
        band_standard: Which standard set ``band_limit_c``.
        exceeded: True when the reading is at or above ``limit_c``.
    """

    wbgt_c: float
    limit_c: float
    margin_c: float
    metabolic_w: float
    acclimatization: Acclimatization
    limit_source: str
    band_limit_c: float | None
    band_standard: Standard | None
    exceeded: bool

    @property
    def strictest_limit_c(self) -> float:
        """The lower of the continuous regression limit and the band limit."""
        if self.band_limit_c is None:
            return self.limit_c
        return min(self.limit_c, self.band_limit_c)

    def as_dict(self) -> dict[str, float | bool | str | None]:
        return {
            "wbgt_c": round(self.wbgt_c, 2),
            "limit_c": round(self.limit_c, 2),
            "margin_c": round(self.margin_c, 2),
            "strictest_limit_c": round(self.strictest_limit_c, 2),
            "metabolic_w": round(self.metabolic_w, 1),
            "acclimatization": self.acclimatization.value,
            "limit_source": self.limit_source,
            "band_limit_c": (None if self.band_limit_c is None else round(self.band_limit_c, 2)),
            "band_standard": (None if self.band_standard is None else self.band_standard.value),
            "exceeded": self.exceeded,
        }


def screen(
    wbgt_c: float,
    workload: Workload,
    acclimatization: Acclimatization,
    metabolic_w: float | None = None,
) -> Screening:
    """Screen one WBGT reading against the NIOSH limits.

    Args:
        wbgt_c: WBGT in degrees Celsius, measured or modelled.
        workload: The job's metabolic band.
        acclimatization: Whether the crew has adapted to the heat. Chhaya
            defaults sites to ``unacclimatized`` in the policy file, because
            daily-wage crews turn over constantly and the RAL is the limit
            that protects a worker on their first week.
        metabolic_w: An explicit metabolic rate. Defaults to the upper edge of
            the NIOSH band for the workload, which is the conservative choice
            within a band.
    """
    if metabolic_w is None:
        metabolic_w = upper_metabolic_w(workload)
    metabolic_w = _checked_metabolic_w(metabolic_w)
    limit = limit_wbgt_c(metabolic_w, acclimatization)
    try:
        band_limit, band_standard = lowest_published_wbgt_c(workload)
    except HeatInputError:
        band_limit, band_standard = None, None
    source = (
        f"NIOSH 2016-106 s8.1 {'REL' if acclimatization is Acclimatization.ACCLIMATIZED else 'RAL'}"
        f" at M={metabolic_w:.0f} W"
    )
    return Screening(
        wbgt_c=wbgt_c,
        limit_c=limit,
        margin_c=limit - wbgt_c,
        metabolic_w=metabolic_w,
        acclimatization=acclimatization,
        limit_source=source,
        band_limit_c=band_limit,
        band_standard=band_standard,
        exceeded=wbgt_c >= limit,
    )


def cross_standard_row(workload: Workload) -> dict[str, object]:
    """Everything Table 5-1 says about one band, for the console's table."""
    metabolic_kcal = NIOSH_BAND_KCAL_H[workload]
    return {
        "workload": workload.value,
        "label": WORKLOAD_LABELS[workload],
        "niosh_band_kcal_h": [metabolic_kcal[0], metabolic_kcal[1]],
        "niosh_band_w": list(NIOSH_BAND_W_TABLE[workload]),
        "acgih_band_kcal_h": [
            ACGIH_BAND_KCAL_H[workload][0],
            ACGIH_BAND_KCAL_H[workload][1],
        ],
        "limits": {
            standard.value: CROSS_STANDARD_WBGT_C[standard].get(workload) for standard in Standard
        },
        "limits_high_velocity": {
            standard.value: CROSS_STANDARD_WBGT_HIGH_VELOCITY_C.get(standard, {}).get(workload)
            for standard in Standard
        },
        "spread_c": STANDARD_SPREAD_C[workload],
    }
