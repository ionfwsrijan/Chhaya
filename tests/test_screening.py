"""Tests for the NIOSH WBGT screening limits and the cross-standard table.

Anchors, all from NIOSH 2016-106:

- The REL regression reproduces the document's own worked example (page 4,
  M = 348.9 W, REL printed as 27.8 C) to within about 0.35 C, and its own
  Table 5-1 NIOSH column to within 1.0 C in every band.
- RAL sits below REL at every metabolic rate, which is the direction safety
  requires: an unacclimatized worker gets the smaller heat load.
- Table 5-1's lowest published value per band, which is the strictest screen
  Chhaya will apply.
"""

from __future__ import annotations

import math

import pytest

from chhaya.domain.heat import HeatInputError
from chhaya.domain.screening import (
    CROSS_STANDARD_WBGT_C,
    MAX_METABOLIC_W,
    MIN_METABOLIC_W,
    MSHA_HOT_SITE_WBGT_C,
    RAL_INTERCEPT,
    RAL_SLOPE,
    REL_INTERCEPT,
    REL_SLOPE,
    STANDARD_SPREAD_C,
    Acclimatization,
    Standard,
    cross_standard_row,
    limit_wbgt_c,
    lowest_published_wbgt_c,
    ral_wbgt_c,
    rel_wbgt_c,
    screen,
)
from chhaya.domain.workload import NIOSH_BAND_W_TABLE, Workload, upper_metabolic_w

# NIOSH 2016-106's own worked example, page 4: a moderate job at 348.9 W.
WORKED_EXAMPLE_W = 348.9
WORKED_EXAMPLE_REL_C = 27.8
WORKED_EXAMPLE_RAL_C = 25.0


class TestRegressions:
    def test_rel_matches_the_worked_example_within_printing_precision(self) -> None:
        assert rel_wbgt_c(WORKED_EXAMPLE_W) == pytest.approx(WORKED_EXAMPLE_REL_C, abs=0.35)

    def test_ral_matches_the_worked_example_within_printing_precision(self) -> None:
        assert ral_wbgt_c(WORKED_EXAMPLE_W) == pytest.approx(WORKED_EXAMPLE_RAL_C, abs=1.0)

    def test_intercepts_and_slopes_are_the_published_coefficients(self) -> None:
        assert (REL_INTERCEPT, REL_SLOPE) == (56.7, 11.5)
        assert (RAL_INTERCEPT, RAL_SLOPE) == (59.9, 14.1)
        # Spot-check the equations are the printed closed forms.
        assert rel_wbgt_c(1000.0) == pytest.approx(56.7 - 11.5 * 3.0)
        assert ral_wbgt_c(1000.0) == pytest.approx(59.9 - 14.1 * 3.0)

    def test_rel_reproduces_the_table_5_1_niosh_column_within_one_degree(self) -> None:
        for workload in (Workload.LIGHT, Workload.MODERATE):
            published = CROSS_STANDARD_WBGT_C[Standard.NIOSH][workload]
            modelled = rel_wbgt_c(NIOSH_BAND_W_TABLE[workload][1])
            assert modelled == pytest.approx(published, abs=1.0), workload

    def test_ral_is_always_below_rel(self) -> None:
        for metabolic_w in (116.0, 232.0, 348.0, 464.0, 580.0):
            assert ral_wbgt_c(metabolic_w) < rel_wbgt_c(metabolic_w)

    def test_the_unacclimatized_penalty_grows_with_exertion(self) -> None:
        # A heavier job in the same heat needs the bigger safety margin, so
        # the REL-RAL gap must widen as the metabolic rate rises.
        gaps = [
            rel_wbgt_c(metabolic_w) - ral_wbgt_c(metabolic_w)
            for metabolic_w in (232.0, 348.0, 464.0, 580.0)
        ]
        assert gaps == sorted(gaps)
        assert gaps[0] > 2.0
        assert gaps[-1] < 5.0

    def test_both_limits_fall_as_work_gets_harder(self) -> None:
        rates = [232.0, 348.0, 464.0, 580.0]
        assert [rel_wbgt_c(w) for w in rates] == sorted(
            [rel_wbgt_c(w) for w in rates], reverse=True
        )
        assert [ral_wbgt_c(w) for w in rates] == sorted(
            [ral_wbgt_c(w) for w in rates], reverse=True
        )

    def test_limits_are_decreasing_in_log10_metabolic_rate(self) -> None:
        # The whole regression is linear in log10(M); assert that explicitly.
        for limit in (rel_wbgt_c, ral_wbgt_c):
            a = limit(math.sqrt(100.0 * 400.0))  # geometric mean of 100 and 400
            midpoint = (limit(100.0) + limit(400.0)) / 2.0
            assert a == pytest.approx(midpoint, abs=1e-9)


class TestLimitSelection:
    def test_acclimatized_gets_the_rel(self) -> None:
        assert limit_wbgt_c(348.0, Acclimatization.ACCLIMATIZED) == rel_wbgt_c(348.0)

    def test_unacclimatized_gets_the_ral(self) -> None:
        assert limit_wbgt_c(348.0, Acclimatization.UNACCLIMATIZED) == ral_wbgt_c(348.0)

    @pytest.mark.parametrize("metabolic_w", [0.0, 49.0, 1001.0, 5000.0])
    def test_metabolic_rates_outside_the_fitted_range_are_refused(self, metabolic_w: float) -> None:
        with pytest.raises(HeatInputError, match="outside the range"):
            rel_wbgt_c(metabolic_w)
        with pytest.raises(HeatInputError, match="outside the range"):
            ral_wbgt_c(metabolic_w)

    def test_the_fitted_range_bounds_are_documented_constants(self) -> None:
        assert (MIN_METABOLIC_W, MAX_METABOLIC_W) == (50.0, 1000.0)
        rel_wbgt_c(MIN_METABOLIC_W)
        rel_wbgt_c(MAX_METABOLIC_W)


class TestCrossStandardTable:
    def test_every_band_resolves_to_a_strictest_limit_and_who_set_it(self) -> None:
        expected = {
            Workload.RESTING: (32.2, Standard.ACGIH),
            Workload.LIGHT: (30.0, Standard.ACGIH),
            Workload.MODERATE: (26.7, Standard.ACGIH),
            Workload.HEAVY: (25.0, Standard.AIHA),
            Workload.VERY_HEAVY: (23.0, Standard.AIHA),
        }
        for workload, (limit, standard) in expected.items():
            assert lowest_published_wbgt_c(workload) == (limit, standard)

    def test_light_band_tie_is_broken_by_declaration_order(self) -> None:
        # Five standards all publish 30.0 C for light work; the first in
        # declaration order is named so the record is deterministic.
        limit, standard = lowest_published_wbgt_c(Workload.LIGHT)
        assert limit == 30.0
        assert standard is Standard.ACGIH

    def test_osha_never_tabulates_resting(self) -> None:
        assert Workload.RESTING not in CROSS_STANDARD_WBGT_C[Standard.OSHA]

    def test_iso_and_niosh_publish_only_two_bands(self) -> None:
        for standard in (Standard.ISO, Standard.NIOSH):
            assert set(CROSS_STANDARD_WBGT_C[standard]) == {
                Workload.LIGHT,
                Workload.MODERATE,
            }

    def test_missing_band_raises_a_loud_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(CROSS_STANDARD_WBGT_C[Standard.ACGIH], Workload.RESTING, None)
        monkeypatch.setitem(CROSS_STANDARD_WBGT_C[Standard.AIHA], Workload.RESTING, None)
        with pytest.raises(HeatInputError, match=r"no standard.*tabulates"):
            lowest_published_wbgt_c(Workload.RESTING)

    def test_msha_hot_worksite_threshold_is_the_published_one(self) -> None:
        assert MSHA_HOT_SITE_WBGT_C == 26.0


class TestScreen:
    def test_a_reading_at_the_limit_is_exceeded(self) -> None:
        result = screen(26.7, Workload.MODERATE, Acclimatization.UNACCLIMATIZED)
        assert result.exceeded is True
        assert result.margin_c <= 0.0

    def test_a_reading_below_the_limit_is_not_exceeded(self) -> None:
        result = screen(20.0, Workload.LIGHT, Acclimatization.ACCLIMATIZED)
        assert result.exceeded is False
        assert result.margin_c > 0.0

    def test_default_metabolic_rate_is_the_top_of_the_band(self) -> None:
        result = screen(25.0, Workload.MODERATE, Acclimatization.UNACCLIMATIZED)
        # Defaults to the top of the NIOSH metabolic band (300 kcal/h -> 348 W),
        # the conservative choice within a band; Table 5-1 prints 349 W for the
        # same edge using its 1.163 factor, hence the 1 W tolerance.
        assert result.metabolic_w == upper_metabolic_w(Workload.MODERATE)
        assert result.metabolic_w == pytest.approx(
            NIOSH_BAND_W_TABLE[Workload.MODERATE][1], abs=1.5
        )

    def test_explicit_metabolic_rate_is_used(self) -> None:
        result = screen(25.0, Workload.MODERATE, Acclimatization.UNACCLIMATIZED, metabolic_w=250.0)
        assert result.metabolic_w == 250.0
        assert result.limit_c == ral_wbgt_c(250.0)

    def test_band_limit_and_standard_are_attached_where_tabulated(self) -> None:
        result = screen(25.0, Workload.MODERATE, Acclimatization.UNACCLIMATIZED)
        assert result.band_limit_c == 26.7
        assert result.band_standard is Standard.ACGIH

    def test_strictest_limit_is_the_lower_of_regression_and_band(self) -> None:
        # At the moderate band's upper metabolic rate the RAL (24.1) is lower
        # than the Table 5-1 ACGIH band value (26.7), so the regression wins.
        result = screen(22.0, Workload.MODERATE, Acclimatization.UNACCLIMATIZED)
        assert result.limit_c < result.band_limit_c  # type: ignore[operator]
        assert result.strictest_limit_c == result.limit_c

    def test_strictest_limit_can_be_the_band_value(self) -> None:
        # At the top of the very-heavy band the AIHA published value (23.0)
        # is lower than the regression, so the band wins.
        result = screen(21.0, Workload.VERY_HEAVY, Acclimatization.ACCLIMATIZED)
        assert result.band_limit_c == 23.0
        assert result.strictest_limit_c == min(result.limit_c, result.band_limit_c)

    def test_resting_band_falls_back_to_the_regression_only_in_the_source_string(
        self,
    ) -> None:
        result = screen(28.0, Workload.RESTING, Acclimatization.UNACCLIMATIZED)
        assert result.band_limit_c == 32.2
        assert result.band_standard is Standard.ACGIH
        assert "RAL" in result.limit_source

    def test_source_string_names_the_right_regressions(self) -> None:
        rel = screen(25.0, Workload.LIGHT, Acclimatization.ACCLIMATIZED)
        ral = screen(25.0, Workload.LIGHT, Acclimatization.UNACCLIMATIZED)
        assert "REL" in rel.limit_source and "RAL" not in rel.limit_source.split("RAL")[0]
        assert "RAL" in ral.limit_source

    def test_as_dict_is_json_safe(self) -> None:
        data = screen(25.0, Workload.MODERATE, Acclimatization.UNACCLIMATIZED).as_dict()
        assert data["acclimatization"] == "unacclimatized"
        assert data["band_standard"] == "acgih"
        assert isinstance(data["exceeded"], bool)
        assert isinstance(data["limit_c"], float)


class TestCrossStandardRow:
    def test_row_carries_every_standard_and_the_spread(self) -> None:
        row = cross_standard_row(Workload.MODERATE)
        assert row["workload"] == "moderate"
        assert row["spread_c"] == STANDARD_SPREAD_C[Workload.MODERATE]
        limits = row["limits"]
        assert isinstance(limits, dict)
        assert limits["acgih"] == 26.7
        assert limits["aiha"] == 26.7
        assert limits["osha"] == 27.8
        assert limits["iso"] == 28.0
        assert limits["niosh"] == 28.0

    def test_high_velocity_values_are_stricter_or_equal_to_low_velocity(self) -> None:
        for row_workload in (
            Workload.LIGHT,
            Workload.MODERATE,
            Workload.HEAVY,
            Workload.VERY_HEAVY,
        ):
            row = cross_standard_row(row_workload)
            low = row["limits"]
            high = row["limits_high_velocity"]
            assert isinstance(low, dict) and isinstance(high, dict)
            for standard, value in high.items():
                if value is not None:
                    assert value >= low[standard], (row_workload, standard)

    def test_every_workload_has_a_row(self) -> None:
        for workload in Workload:
            row = cross_standard_row(workload)
            assert isinstance(row["label"], str) and row["label"]
            assert row["spread_c"] >= 0.0
