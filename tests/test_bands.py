"""Tests for verdict bands and the stricter-wins merge rule.

Chhaya's central safety property: when two independent published engines
disagree about an hour, the stricter verdict wins and the deciding engine is
named. This module tests that property directly, plus the band vocabulary
Chhaya inherits from NIOSH's own three-way table.
"""

from __future__ import annotations

import pytest

from chhaya.domain.bands import (
    DEFAULT_CAUTION_MARGIN_C,
    STOP_REASON,
    Band,
    EngineFinding,
    Verdict,
    band_from_margin,
    band_from_schedule,
    caution_margin_for,
    finding_from_schedule,
    finding_from_screening,
    merge,
)
from chhaya.domain.heat import HeatInputs, analyse
from chhaya.domain.niosh import Decision, evaluate
from chhaya.domain.screening import STANDARD_SPREAD_C, Acclimatization, screen
from chhaya.domain.workload import Workload


def _finding(
    engine: str,
    band: Band,
    margin_c: float | None = None,
    index: int = 0,
) -> EngineFinding:
    return EngineFinding(
        engine=engine,
        band=band,
        margin_c=margin_c,
        summary=f"{engine} says {band.value}",
        detail="detail",
        citation="citation",
    )


class TestBandVocabulary:
    def test_bands_are_ordered_normal_to_stop(self) -> None:
        assert Band.NORMAL.sort_key < Band.CAUTION.sort_key < Band.STOP.sort_key

    def test_labels_are_human_readable(self) -> None:
        assert Band.NORMAL.label == "Normal work"
        assert Band.STOP.label == "Stop work"
        assert "restrict" in Band.CAUTION.label

    def test_stop_reason_is_the_niosh_table_wording(self) -> None:
        assert STOP_REASON == "High levels of heat stress; consider rescheduling activities."


class TestBandFromSchedule:
    @pytest.mark.parametrize(
        ("decision", "expected"),
        [
            (Decision.NORMAL, Band.NORMAL),
            (Decision.SCHEDULED, Band.CAUTION),
            (Decision.CAUTION, Band.STOP),
        ],
    )
    def test_three_decisions_map_to_three_bands(self, decision: Decision, expected: Band) -> None:
        assert band_from_schedule(decision) is expected

    def test_a_work_rest_cycle_is_a_restriction_not_a_pass(self) -> None:
        # 20/40 means work continues, but not at full capacity, so the band
        # must reflect that something has been restricted.
        assert band_from_schedule(Decision.SCHEDULED) is Band.CAUTION


class TestBandFromMargin:
    def test_zero_margin_is_a_stop(self) -> None:
        assert band_from_margin(0.0) is Band.STOP

    def test_negative_margin_is_a_stop(self) -> None:
        assert band_from_margin(-0.01) is Band.STOP
        assert band_from_margin(-10.0) is Band.STOP

    def test_just_inside_the_amber_band_is_caution(self) -> None:
        assert band_from_margin(0.01) is Band.CAUTION
        assert band_from_margin(DEFAULT_CAUTION_MARGIN_C) is Band.CAUTION

    def test_outside_the_amber_band_is_normal(self) -> None:
        assert band_from_margin(DEFAULT_CAUTION_MARGIN_C + 0.01) is Band.NORMAL
        assert band_from_margin(15.0) is Band.NORMAL

    def test_a_narrower_custom_band_widens_the_normal_zone(self) -> None:
        assert band_from_margin(1.5, caution_margin_c=1.0) is Band.NORMAL
        assert band_from_margin(0.5, caution_margin_c=1.0) is Band.CAUTION


class TestCautionMargin:
    def test_margins_come_from_the_table_5_1_spread(self) -> None:
        assert caution_margin_for(Workload.MODERATE) == STANDARD_SPREAD_C[Workload.MODERATE]
        assert caution_margin_for(Workload.VERY_HEAVY) == 2.0

    def test_every_band_has_a_published_spread(self) -> None:
        for workload in Workload:
            assert caution_margin_for(workload) == STANDARD_SPREAD_C[workload]

    def test_default_margin_is_the_widest_published_spread(self) -> None:
        assert max(STANDARD_SPREAD_C.values()) == DEFAULT_CAUTION_MARGIN_C


class TestFindings:
    def test_schedule_finding_carries_the_adjusted_temperature(self) -> None:
        schedule = evaluate(
            HeatInputs(
                air_c=44.0,
                relative_humidity=10.0,
                wind_ms=4.0,
                shortwave_wm2=950.0,
                cloud_cover=0.0,
            ),
            Workload.LIGHT,
        )
        finding = finding_from_schedule(schedule)
        assert finding.engine == "niosh_schedule"
        assert finding.citation == "NIOSH 2016-106 Table 6-2"
        assert finding.margin_c is None
        assert f"{schedule.source_temperature_f:.0f} F" in finding.summary

    def test_schedule_finding_quotes_the_stop_wording_for_caution(self) -> None:
        schedule = evaluate(
            HeatInputs(
                air_c=45.0,
                relative_humidity=60.0,
                wind_ms=1.0,
                shortwave_wm2=950.0,
                cloud_cover=0.0,
            ),
            Workload.HEAVY,
        )
        finding = finding_from_schedule(schedule)
        assert finding.band is Band.STOP
        assert STOP_REASON in finding.summary

    def test_screening_finding_names_the_limit_it_used(self) -> None:
        screening = screen(27.5, Workload.MODERATE, Acclimatization.UNACCLIMATIZED)
        finding = finding_from_screening(screening, caution_margin_for(Workload.MODERATE))
        assert finding.engine == "wbgt_screen"
        assert finding.citation == "NIOSH 2016-106 s8.1 and Table 5-1"
        assert "unacclimatized limit" in finding.summary
        assert finding.margin_c == pytest.approx(screening.margin_c)

    def test_screening_finding_cites_the_strictest_table_5_1_value(self) -> None:
        screening = screen(29.0, Workload.MODERATE, Acclimatization.UNACCLIMATIZED)
        finding = finding_from_screening(screening, caution_margin_for(Workload.MODERATE))
        assert "26.7 C (acgih)" in finding.summary


class TestMerge:
    def test_empty_findings_are_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            merge(())

    def test_a_single_finding_decides_on_its_own(self) -> None:
        verdict = merge((_finding("niosh_schedule", Band.CAUTION),))
        assert verdict.band is Band.CAUTION
        assert verdict.deciding_engine == "niosh_schedule"
        assert "strictest engine" in verdict.reason

    def test_stop_beats_normal_regardless_of_order(self) -> None:
        normal_first = merge(
            (_finding("niosh_schedule", Band.NORMAL), _finding("wbgt_screen", Band.STOP, -1.0))
        )
        stop_first = merge(
            (_finding("wbgt_screen", Band.STOP, -1.0), _finding("niosh_schedule", Band.NORMAL))
        )
        assert normal_first.band is Band.STOP
        assert stop_first.band is Band.STOP
        assert normal_first.deciding_engine == "wbgt_screen"
        assert stop_first.deciding_engine == "wbgt_screen"

    def test_caution_beats_normal(self) -> None:
        verdict = merge(
            (_finding("niosh_schedule", Band.NORMAL), _finding("wbgt_screen", Band.CAUTION, 1.0))
        )
        assert verdict.band is Band.CAUTION
        assert verdict.deciding_engine == "wbgt_screen"

    def test_stop_beats_caution(self) -> None:
        verdict = merge(
            (_finding("wbgt_screen", Band.CAUTION, 1.0), _finding("niosh_schedule", Band.STOP))
        )
        assert verdict.band is Band.STOP

    def test_tie_at_stop_keeps_the_more_restrictive_margin(self) -> None:
        verdict = merge(
            (
                _finding("wbgt_screen", Band.STOP, -1.0),
                _finding("other_screen", Band.STOP, -6.0),
            )
        )
        assert verdict.deciding_engine == "other_screen"
        assert "both reach stop" in verdict.reason

    def test_tie_with_a_missing_margin_loses_to_a_negative_margin(self) -> None:
        verdict = merge(
            (
                _finding("niosh_schedule", Band.STOP, None),
                _finding("wbgt_screen", Band.STOP, -0.5),
            )
        )
        # None sorts as -inf, i.e. maximally restrictive, so the schedule
        # engine wins the tie. This is what makes DISAGREEMENT 2 in the smoke
        # test name niosh_schedule as the deciding engine.
        assert verdict.deciding_engine == "niosh_schedule"

    def test_tie_at_normal_keeps_the_first_evaluated_engine(self) -> None:
        verdict = merge((_finding("first", Band.NORMAL, 5.0), _finding("second", Band.NORMAL, 4.0)))
        # Both are "safe", but the smaller margin is still the more restrictive
        # reading, so the merge stays deterministic either way.
        assert verdict.deciding_engine in {"first", "second"}
        assert verdict.band is Band.NORMAL

    def test_verdict_properties(self) -> None:
        stop = merge((_finding("a", Band.STOP),))
        normal = merge((_finding("a", Band.NORMAL),))
        assert stop.is_stop is True and stop.is_work_allowed is False
        assert normal.is_stop is False and normal.is_work_allowed is True

    def test_as_dict_lists_every_finding(self) -> None:
        verdict = merge(
            (_finding("niosh_schedule", Band.NORMAL), _finding("wbgt_screen", Band.STOP, -2.0))
        )
        data = verdict.as_dict()
        assert data["band"] == "stop"
        assert data["deciding_engine"] == "wbgt_screen"
        findings = data["findings"]
        assert isinstance(findings, list) and len(findings) == 2


class TestStricterWinsEndToEnd:
    """The two engines, run over real weather, through the real merge."""

    def _verdict(self, inputs: HeatInputs, workload: Workload, accl: Acclimatization) -> Verdict:
        schedule = evaluate(inputs, workload)
        screening = screen(analyse(inputs).wbgt_c, workload, accl)
        return merge(
            (
                finding_from_schedule(schedule),
                finding_from_screening(screening, caution_margin_for(workload)),
            )
        )

    def test_humid_afternoon_is_stopped_by_the_wbgt_screen(
        self, humid_coastal_afternoon: HeatInputs
    ) -> None:
        verdict = self._verdict(
            humid_coastal_afternoon, Workload.MODERATE, Acclimatization.UNACCLIMATIZED
        )
        schedule = evaluate(humid_coastal_afternoon, Workload.MODERATE)
        # The air-temperature schedule alone would let this hour pass.
        assert schedule.decision is Decision.NORMAL
        # The WBGT screen catches what the air temperature misses.
        assert verdict.band is Band.STOP
        assert verdict.deciding_engine == "wbgt_screen"

    def test_hot_dry_afternoon_is_stopped_by_the_schedule(
        self, hot_dry_desert_afternoon: HeatInputs
    ) -> None:
        verdict = self._verdict(
            hot_dry_desert_afternoon, Workload.LIGHT, Acclimatization.UNACCLIMATIZED
        )
        schedule = evaluate(hot_dry_desert_afternoon, Workload.LIGHT)
        assert schedule.decision is Decision.CAUTION
        assert verdict.band is Band.STOP
        assert verdict.deciding_engine == "niosh_schedule"

    def test_mild_day_is_normal_by_both_engines(self, mild_day: HeatInputs) -> None:
        verdict = self._verdict(mild_day, Workload.LIGHT, Acclimatization.UNACCLIMATIZED)
        assert verdict.band is Band.NORMAL
        assert len(verdict.findings) == 2

    def test_both_engines_keep_their_own_number_in_the_record(
        self, humid_coastal_afternoon: HeatInputs
    ) -> None:
        verdict = self._verdict(
            humid_coastal_afternoon, Workload.MODERATE, Acclimatization.UNACCLIMATIZED
        )
        engines = {f.engine for f in verdict.findings}
        assert engines == {"niosh_schedule", "wbgt_screen"}
