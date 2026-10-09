"""Tests for the three weather backends.

Open-Meteo is tested with a MockTransport so the suite never touches the
network; Recorded and Fake are pure. Every backend must produce HeatInputs
that validate, so each test runs ``inputs.validate()`` as a floor.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from chhaya.domain.heat import HeatInputs
from chhaya.sources import (
    FakeSource,
    HeatSource,
    OpenMeteoSource,
    RecordedSource,
    SiteLocation,
    SourceError,
)

WHEN = datetime(2026, 5, 18, 14, 0, 0)
SITE = "hyderabad-miyapur-site-b"


def _canned_payload() -> dict[str, object]:
    times = [f"2026-05-18T{h:02d}:00" for h in range(24)]
    return {
        "hourly": {
            "time": times,
            "temperature_2m": [30.0 if h != 14 else 38.5 for h in range(24)],
            "relative_humidity_2m": [55.0] * 24,
            "wind_speed_10m": [2.0] * 24,
            "shortwave_radiation": [800.0 if 6 <= h <= 18 else 0.0 for h in range(24)],
            "cloud_cover": [20.0] * 24,
        }
    }


class TestFakeSource:
    @pytest.mark.asyncio
    async def test_is_deterministic(self) -> None:
        source = FakeSource.hot_afternoon()
        first = await source.current(SITE, WHEN)
        second = await source.current(SITE, WHEN)
        assert first == second
        first.validate()

    @pytest.mark.asyncio
    async def test_peak_is_mid_afternoon(self) -> None:
        source = FakeSource.hot_afternoon()
        afternoon = await source.current(SITE, datetime(2026, 5, 18, 15, 0))
        morning = await source.current(SITE, datetime(2026, 5, 18, 6, 0))
        assert afternoon.air_c > morning.air_c
        assert afternoon.shortwave_wm2 > morning.shortwave_wm2

    @pytest.mark.asyncio
    async def test_night_has_no_shortwave(self) -> None:
        source = FakeSource.hot_afternoon()
        night = await source.current(SITE, datetime(2026, 5, 18, 2, 0))
        assert night.shortwave_wm2 == 0.0

    @pytest.mark.asyncio
    async def test_unknown_site_raises(self) -> None:
        source = FakeSource.hot_afternoon()
        with pytest.raises(SourceError, match="no fake scenario"):
            await source.current("somewhere-else", WHEN)

    def test_pin_overrides_a_site(self) -> None:
        source = FakeSource.hot_afternoon()
        pinned = HeatInputs(25.0, 50.0, 1.0, 100.0, cloud_cover=50.0)
        source.pin(SITE, lambda _when: pinned)

        async def run() -> None:
            assert await source.current(SITE, WHEN) == pinned

        import asyncio

        asyncio.run(run())

    def test_sites_lists_everything(self) -> None:
        source = FakeSource.hot_afternoon()
        source.pin("other", lambda _w: HeatInputs(20.0, 40.0, 1.0))
        assert source.sites() == [SITE, "other"]

    def test_every_source_is_a_heat_source(self) -> None:
        assert isinstance(FakeSource.hot_afternoon(), HeatSource)


class TestRecordedSource:
    @pytest.mark.asyncio
    async def test_replays_a_canned_hour(self, tmp_path: Path) -> None:
        path = tmp_path / "recording.json"
        path.write_text(
            json.dumps(
                {
                    SITE: [
                        {
                            "time": "2026-05-18T14:00",
                            "temperature_2m": 38.5,
                            "relative_humidity_2m": 55.0,
                            "wind_speed_10m": 2.0,
                            "shortwave_radiation": 800.0,
                            "cloud_cover": 20.0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        source = RecordedSource(path)
        inputs = await source.current(SITE, WHEN)
        inputs.validate()
        assert inputs.air_c == 38.5
        assert inputs.relative_humidity == 55.0

    @pytest.mark.asyncio
    async def test_missing_hour_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({SITE: []}), encoding="utf-8")
        source = RecordedSource(path)
        with pytest.raises(SourceError, match="no hour"):
            await source.current(SITE, WHEN)

    @pytest.mark.asyncio
    async def test_unknown_site_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "rec.json"
        path.write_text(json.dumps({SITE: []}), encoding="utf-8")
        source = RecordedSource(path)
        with pytest.raises(SourceError, match="not in the recording"):
            await source.current("elsewhere", WHEN)

    def test_malformed_file_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.json"
        bad.write_text("not json", encoding="utf-8")
        with pytest.raises(SourceError, match="cannot read"):
            RecordedSource(bad)

    def test_bad_row_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "bad-row.json"
        path.write_text(json.dumps({SITE: [{"time": "2026-05-18T14:00"}]}), encoding="utf-8")
        with pytest.raises(SourceError, match="bad hour"):
            RecordedSource(path)

    def test_recorded_hours_is_sorted(self, tmp_path: Path) -> None:
        path = tmp_path / "hours.json"
        rows = [
            {
                "time": f"2026-05-18T{h:02d}:00",
                "temperature_2m": 30.0,
                "relative_humidity_2m": 50.0,
                "wind_speed_10m": 1.0,
                "shortwave_radiation": 100.0,
                "cloud_cover": 10.0,
            }
            for h in (14, 6, 10)
        ]
        path.write_text(json.dumps({SITE: rows}), encoding="utf-8")
        source = RecordedSource(path)
        assert source.recorded_hours[SITE] == [
            "2026-05-18T06:00:00",
            "2026-05-18T10:00:00",
            "2026-05-18T14:00:00",
        ]

    def test_every_source_is_a_heat_source(self, tmp_path: Path) -> None:
        path = tmp_path / "rec.json"
        path.write_text(json.dumps({SITE: []}), encoding="utf-8")
        assert isinstance(RecordedSource(path), HeatSource)


class TestOpenMeteoSource:
    def _source(self, handler: object) -> OpenMeteoSource:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
            base_url="https://api.open-meteo.com/v1/forecast",
        )
        return OpenMeteoSource({SITE: SiteLocation(17.44, 78.38)}, client=client)

    @pytest.mark.asyncio
    async def test_parses_a_realistic_payload(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["latitude"] == "17.44"
            assert "temperature_2m" in request.url.params["hourly"]
            assert request.url.params["wind_speed_unit"] == "ms"
            return httpx.Response(200, json=_canned_payload())

        source = self._source(handler)
        inputs = await source.current(SITE, WHEN)
        inputs.validate()
        assert inputs.air_c == 38.5
        assert inputs.wind_ms == 2.0
        assert inputs.cloud_cover == 20.0

    @pytest.mark.asyncio
    async def test_unknown_site_raises(self) -> None:
        source = self._source(lambda _r: httpx.Response(200, json=_canned_payload()))
        with pytest.raises(SourceError, match="unknown site"):
            await source.current("nope", WHEN)

    @pytest.mark.asyncio
    async def test_http_error_raises(self) -> None:
        source = self._source(lambda _r: httpx.Response(500, text="boom"))
        with pytest.raises(SourceError, match="request failed"):
            await source.current(SITE, WHEN)

    @pytest.mark.asyncio
    async def test_missing_hour_raises(self) -> None:
        source = self._source(lambda _r: httpx.Response(200, json={"hourly": {"time": []}}))
        with pytest.raises(SourceError, match="no hour"):
            await source.current(SITE, WHEN)

    @pytest.mark.asyncio
    async def test_missing_field_raises(self) -> None:
        payload = _canned_payload()
        assert isinstance(payload["hourly"], dict)
        del payload["hourly"]["temperature_2m"]  # type: ignore[index]
        source = self._source(lambda _r: httpx.Response(200, json=payload))
        with pytest.raises(SourceError, match="temperature_2m"):
            await source.current(SITE, WHEN)

    @pytest.mark.asyncio
    async def test_no_hourly_block_raises(self) -> None:
        source = self._source(lambda _r: httpx.Response(200, json={}))
        with pytest.raises(SourceError, match="hourly"):
            await source.current(SITE, WHEN)

    @pytest.mark.asyncio
    async def test_none_value_in_field_raises(self) -> None:
        payload = _canned_payload()
        assert isinstance(payload["hourly"], dict)
        payload["hourly"]["temperature_2m"][14] = None  # type: ignore[index]
        source = self._source(lambda _r: httpx.Response(200, json=payload))
        with pytest.raises(SourceError, match="temperature_2m"):
            await source.current(SITE, WHEN)
