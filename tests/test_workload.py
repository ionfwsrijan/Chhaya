"""Tests for the shared workload vocabulary and metabolic rates."""

from __future__ import annotations

import pytest

from chhaya.domain.heat import HeatInputError
from chhaya.domain.workload import (
    ACGIH_BAND_KCAL_H,
    KCAL_H_TO_W,
    NIOSH_BAND_KCAL_H,
    NIOSH_BAND_W_TABLE,
    WORKLOAD_LABELS,
    WORKLOAD_NOTES,
    Workload,
    metabolic_w,
    parse_workload,
    upper_metabolic_kcal_h,
    upper_metabolic_w,
)


class TestWorkloadOrdering:
    def test_every_workload_has_a_label_and_a_note(self) -> None:
        for workload in Workload:
            assert WORKLOAD_LABELS[workload]
            assert WORKLOAD_NOTES[workload]

    def test_sort_key_is_monotonic(self) -> None:
        ordered = sorted(Workload, key=lambda w: w.sort_key)
        assert ordered == [
            Workload.RESTING,
            Workload.LIGHT,
            Workload.MODERATE,
            Workload.HEAVY,
            Workload.VERY_HEAVY,
        ]

    def test_upper_metabolic_rate_increases_with_workload(self) -> None:
        values = [upper_metabolic_w(w) for w in sorted(Workload, key=lambda w: w.sort_key)]
        assert values == sorted(values)


class TestMetabolicRates:
    def test_niosh_conversion_factor_is_the_published_one(self) -> None:
        # NIOSH 2016-106 section 1.1.3 states 1 kcal*h^-1 = 1.16 W.
        assert KCAL_H_TO_W == 1.16
        assert metabolic_w(100.0) == pytest.approx(116.0)
        assert metabolic_w(300.0) == pytest.approx(348.0)

    def test_band_upper_edges_are_the_niosh_table_edges(self) -> None:
        assert upper_metabolic_kcal_h(Workload.RESTING) == 100.0
        assert upper_metabolic_kcal_h(Workload.LIGHT) == 200.0
        assert upper_metabolic_kcal_h(Workload.MODERATE) == 300.0
        assert upper_metabolic_kcal_h(Workload.HEAVY) == 400.0
        assert upper_metabolic_kcal_h(Workload.VERY_HEAVY) == 500.0

    def test_niosh_band_table_watts_match_the_conversion(self) -> None:
        # Table 5-1 prints 117 W for 100 kcal*h^-1 (i.e. 1.163); Chhaya uses
        # the 1.16 factor the body text states, so allow the table's rounding.
        for workload, (lo_kcal, hi_kcal) in NIOSH_BAND_KCAL_H.items():
            lo_w, hi_w = NIOSH_BAND_W_TABLE[workload]
            assert lo_w == pytest.approx(lo_kcal * KCAL_H_TO_W, abs=1.5)
            assert hi_w == pytest.approx(hi_kcal * KCAL_H_TO_W, abs=1.5)

    def test_acgih_bands_differ_from_niosh_bands(self) -> None:
        # ACGIH splits the moderate band wider than NIOSH does; the difference
        # is why Chhaya screens against several standards rather than one.
        assert ACGIH_BAND_KCAL_H[Workload.MODERATE] == (201.0, 350.0)
        assert NIOSH_BAND_KCAL_H[Workload.MODERATE] == (201.0, 300.0)


class TestParseWorkload:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("resting", Workload.RESTING),
            ("light", Workload.LIGHT),
            ("moderate", Workload.MODERATE),
            ("heavy", Workload.HEAVY),
            ("very_heavy", Workload.VERY_HEAVY),
            ("Heavy Work", Workload.HEAVY),
            ("heavy work", Workload.HEAVY),
            ("LIGHT WORK", Workload.LIGHT),
            ("very heavy workload", Workload.VERY_HEAVY),
            ("Moderate-Work", Workload.MODERATE),
            ("  heavy  ", Workload.HEAVY),
            ("rest", Workload.RESTING),
            ("sedentary", Workload.RESTING),
            ("inspection", Workload.LIGHT),
            ("climbing", Workload.HEAVY),
            ("extreme", Workload.VERY_HEAVY),
        ],
    )
    def test_accepts_real_world_spellings(self, raw: str, expected: Workload) -> None:
        assert parse_workload(raw) is expected

    @pytest.mark.parametrize("raw", ["running", "", "  ", "sprint", "1", "jogging"])
    def test_rejects_unknown_workloads_loudly(self, raw: str) -> None:
        with pytest.raises(HeatInputError, match="unknown workload"):
            parse_workload(raw)

    def test_error_message_lists_the_valid_options(self) -> None:
        with pytest.raises(HeatInputError) as excinfo:
            parse_workload("flying")
        for workload in Workload:
            assert workload.value in str(excinfo.value)
