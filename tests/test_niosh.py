"""Tests for the NIOSH work/rest schedule.

The three reference points every assertion hangs on are printed in the primary
sources, not invented here:

- NIOSH 2016-106 Table 6-2 (and the identical table in the 2017-127 flyer):
  heavy work at 104 F is 20/40, moderate work at 108 F is Caution.
- The worked example printed on the 2017-127 flyer: 90 F, partly cloudy,
  50 % humidity adjusts to 103 F, where moderate work is 30/30.

The humidity table is asserted across all six published steps, because the
dry-air subtractions are the ones the shorter flyer omits and the ones Chhaya
most needs to get right on a Hyderabad afternoon.
"""

from __future__ import annotations

import pytest

from chhaya.domain.heat import HeatInputError, HeatInputs
from chhaya.domain.niosh import (
    HUMIDITY_STEPS,
    SCHEDULE_F,
    SCHEDULE_MAX_F,
    SCHEDULE_MIN_F,
    SUPPORTED_WORKLOADS,
    Decision,
    WorkRest,
    c_to_f,
    evaluate,
    f_to_c,
    humidity_adjustment_f,
    sun_adjustment_f,
)
from chhaya.domain.workload import Workload


def _shade(air_c: float, rh: float, wind: float = 1.0) -> HeatInputs:
    """A shaded or night-time reading: no shortwave, fully overcast."""
    return HeatInputs(
        air_c=air_c, relative_humidity=rh, wind_ms=wind, shortwave_wm2=0.0, cloud_cover=100.0
    )


class TestTemperatureConversions:
    def test_c_to_f_and_back(self) -> None:
        assert c_to_f(100.0) == pytest.approx(212.0)
        assert f_to_c(32.0) == pytest.approx(0.0)
        for celsius in (-40.0, 0.0, 20.0, 37.0, 50.0):
            assert f_to_c(c_to_f(celsius)) == pytest.approx(celsius)


class TestPublishedReferencePoints:
    def test_heavy_work_at_104f_is_20_40(self) -> None:
        result = evaluate(_shade(f_to_c(104.0), 30.0), Workload.HEAVY)
        assert result.decision is Decision.SCHEDULED
        assert (result.work_min, result.rest_min) == (20, 40)
        assert result.label == "20/40"

    def test_moderate_work_at_108f_is_caution(self) -> None:
        result = evaluate(_shade(f_to_c(108.0), 30.0), Workload.MODERATE)
        assert result.decision is Decision.CAUTION
        assert result.is_stop is True

    def test_light_work_stays_normal_at_105f_shade(self) -> None:
        result = evaluate(_shade(f_to_c(105.0), 30.0), Workload.LIGHT)
        assert result.decision is Decision.NORMAL

    def test_flyer_case_study_90f_partly_cloudy_50pct_adjusts_to_103f(self) -> None:
        inputs = HeatInputs(
            air_c=f_to_c(90.0),
            relative_humidity=50.0,
            wind_ms=1.0,
            shortwave_wm2=400.0,
            cloud_cover=50.0,
        )
        result = evaluate(inputs, Workload.MODERATE)
        assert result.source_temperature_f == pytest.approx(103.0, abs=0.5)
        assert result.unadjusted_f == pytest.approx(90.0, abs=0.5)
        assert (result.work_min, result.rest_min) == (30, 30)


class TestHumidityCorrection:
    def test_all_six_published_steps(self) -> None:
        assert humidity_adjustment_f(10.0) == (-8.0, "10 %")
        assert humidity_adjustment_f(20.0) == (-4.0, "20 %")
        assert humidity_adjustment_f(30.0) == (0.0, "30 %")
        assert humidity_adjustment_f(40.0) == (3.0, "40 %")
        assert humidity_adjustment_f(50.0) == (6.0, "50 %")
        assert humidity_adjustment_f(60.0) == (9.0, "60 %")
        assert humidity_adjustment_f(95.0) == (9.0, "60 %")

    def test_between_two_steps_takes_the_lower(self) -> None:
        assert humidity_adjustment_f(25.0) == (-4.0, "20 %")
        assert humidity_adjustment_f(45.0) == (3.0, "40 %")
        assert humidity_adjustment_f(5.0) == (0.0, "30 % baseline")

    def test_dry_air_cools_the_adjusted_temperature(self) -> None:
        wet = evaluate(_shade(f_to_c(105.0), 60.0), Workload.LIGHT)
        dry = evaluate(_shade(f_to_c(105.0), 10.0), Workload.LIGHT)
        assert dry.source_temperature_f < wet.source_temperature_f
        assert dry.source_temperature_f == pytest.approx(wet.source_temperature_f - 17.0, abs=0.5)

    def test_table_is_relative_to_a_30pct_baseline(self) -> None:
        assert HUMIDITY_STEPS[2] == (30.0, 0.0)


class TestSunCorrection:
    def test_full_sun_adds_thirteen_fahrenheit(self) -> None:
        inputs = HeatInputs(
            air_c=40.0, relative_humidity=30.0, wind_ms=2.0, shortwave_wm2=950.0, cloud_cover=0.0
        )
        assert sun_adjustment_f(inputs) == (13.0, "full sun")

    def test_partly_cloudy_adds_seven(self) -> None:
        inputs = HeatInputs(
            air_c=40.0, relative_humidity=30.0, wind_ms=2.0, shortwave_wm2=400.0, cloud_cover=50.0
        )
        assert sun_adjustment_f(inputs) == (7.0, "partly cloudy")

    def test_shade_and_night_add_nothing(self) -> None:
        shade = HeatInputs(
            air_c=40.0, relative_humidity=30.0, wind_ms=2.0, shortwave_wm2=600.0, cloud_cover=95.0
        )
        night = HeatInputs(
            air_c=40.0, relative_humidity=30.0, wind_ms=2.0, shortwave_wm2=0.0, cloud_cover=100.0
        )
        assert sun_adjustment_f(shade) == (0.0, "no shadows")
        assert sun_adjustment_f(night) == (0.0, "night")

    def test_irradiance_alone_recovers_the_three_bands(self) -> None:
        strong = HeatInputs(40.0, 30.0, 2.0, 800.0)
        medium = HeatInputs(40.0, 30.0, 2.0, 400.0)
        weak = HeatInputs(40.0, 30.0, 2.0, 100.0)
        assert sun_adjustment_f(strong)[0] == 13.0
        assert sun_adjustment_f(medium)[0] == 7.0
        assert sun_adjustment_f(weak)[0] == 0.0

    def test_direct_sun_pushes_a_cool_day_into_a_restriction(self) -> None:
        # Light work at 100 F in shade is Normal, but full sun adds 13 F,
        # which clamps to the table's 112 F ceiling: Caution.
        air_c = f_to_c(100.0)
        shade = evaluate(_shade(air_c, 30.0), Workload.LIGHT)
        sun = evaluate(
            HeatInputs(
                air_c=air_c,
                relative_humidity=30.0,
                wind_ms=1.0,
                shortwave_wm2=950.0,
                cloud_cover=0.0,
            ),
            Workload.LIGHT,
        )
        assert shade.decision is Decision.NORMAL
        assert sun.decision is Decision.CAUTION
        assert sun.unadjusted_f == pytest.approx(100.0, abs=0.5)


class TestTableShape:
    def test_table_covers_90_to_112_fahrenheit(self) -> None:
        assert SCHEDULE_MIN_F == 90
        assert SCHEDULE_MAX_F == 112
        assert set(SCHEDULE_F) == set(range(90, 113))

    def test_every_cell_is_one_of_the_three_decisions(self) -> None:
        for row in SCHEDULE_F.values():
            for cell in row.values():
                assert cell[0] in (Decision.NORMAL, Decision.SCHEDULED, Decision.CAUTION)

    def test_heat_increases_monotonically_down_each_column(self) -> None:
        rank = {Decision.NORMAL: 0, Decision.SCHEDULED: 1, Decision.CAUTION: 2}
        for workload in SUPPORTED_WORKLOADS:
            scores = []
            for fahrenheit in range(90, 113):
                decision, work, _rest = SCHEDULE_F[fahrenheit][workload]
                # Within "scheduled", a shorter work period is worse.
                severity = (rank[decision], -work if decision is Decision.SCHEDULED else 0)
                scores.append(severity)
            assert scores == sorted(scores), workload

    def test_out_of_range_temperatures_are_clamped_not_extrapolated(self) -> None:
        scorching = evaluate(
            HeatInputs(
                air_c=50.0,
                relative_humidity=60.0,
                wind_ms=1.0,
                shortwave_wm2=950.0,
                cloud_cover=0.0,
            ),
            Workload.HEAVY,
        )
        assert scorching.source_temperature_f > SCHEDULE_MAX_F
        assert "clamped to table range" in scorching.adjustments_f
        assert scorching.decision is Decision.CAUTION

        freezing = evaluate(_shade(-20.0, 30.0), Workload.HEAVY)
        assert freezing.unadjusted_f < SCHEDULE_MIN_F
        assert "clamped to table range" in freezing.adjustments_f
        assert freezing.decision is Decision.NORMAL


class TestDutyCycle:
    def test_normal_means_full_time_work(self) -> None:
        result = evaluate(_shade(f_to_c(90.0), 30.0), Workload.MODERATE)
        assert result.duty_cycle == 1.0

    def test_scheduled_duty_cycle_is_the_work_fraction(self) -> None:
        result = evaluate(_shade(f_to_c(104.0), 30.0), Workload.HEAVY)
        assert result.duty_cycle == pytest.approx(20 / 60)

    def test_caution_means_no_work(self) -> None:
        result = evaluate(_shade(f_to_c(112.0), 30.0), Workload.HEAVY)
        assert result.duty_cycle == 0.0


class TestWorkloadGuard:
    def test_light_moderate_and_heavy_are_supported(self) -> None:
        for workload in (Workload.LIGHT, Workload.MODERATE, Workload.HEAVY):
            evaluate(_shade(f_to_c(95.0), 30.0), workload)

    @pytest.mark.parametrize("workload", [Workload.RESTING, Workload.VERY_HEAVY])
    def test_untabulated_workloads_are_refused_rather_than_guessed(
        self, workload: Workload
    ) -> None:
        with pytest.raises(HeatInputError, match="not tabulated"):
            evaluate(_shade(f_to_c(100.0), 30.0), workload)

    def test_error_names_the_supported_workloads(self) -> None:
        with pytest.raises(HeatInputError) as excinfo:
            evaluate(_shade(f_to_c(100.0), 30.0), Workload.VERY_HEAVY)
        for workload in SUPPORTED_WORKLOADS:
            assert workload.value in str(excinfo.value)


class TestWorkRest:
    def test_label_matches_the_decision(self) -> None:
        assert WorkRest(Decision.NORMAL, 0, 0, 90.0, 90.0, "").label == "Normal work"
        assert WorkRest(Decision.CAUTION, 0, 0, 115.0, 90.0, "").label.startswith("Caution")
        assert WorkRest(Decision.SCHEDULED, 20, 40, 104.0, 90.0, "").label == "20/40"

    def test_scheduled_cell_without_durations_is_invalid(self) -> None:
        cell = WorkRest(Decision.SCHEDULED, None, None, 104.0, 90.0, "")
        with pytest.raises(ValueError, match="scheduled cells"):
            _ = cell.duty_cycle
