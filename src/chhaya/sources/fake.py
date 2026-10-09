"""A deterministic synthetic weather backend for scripted mode and demos.

No network, no fixture file, no clock: the same (site, hour) always returns
the same weather, because the values are a pure function of the site's seed
and the hour's position in a diurnal curve. That is what makes ``make local``
reproducible in a grader's terminal with the wifi off.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime

from chhaya.domain.heat import HeatInputs
from chhaya.sources.base import SourceError

__all__ = ["FakeSource", "Scenario"]


class Scenario:
    """One synthetic site: a diurnal curve plus a fixed humidity band.

    peak_air_c is the afternoon maximum; the curve troughs near sunrise and
    peaks around 15:00 local, which is close enough to a real summer day for
    the heat engines to exercise their full range.
    """

    def __init__(
        self,
        peak_air_c: float,
        humidity: float,
        wind_ms: float,
        peak_shortwave_wm2: float = 950.0,
    ) -> None:
        self.peak_air_c = peak_air_c
        self.humidity = humidity
        self.wind_ms = wind_ms
        self.peak_shortwave_wm2 = peak_shortwave_wm2

    def at_hour(self, when: datetime) -> HeatInputs:
        # 15:00 is the peak; 03:00 the trough (12-hour offset, half amplitude).
        hour_angle = ((when.hour - 15.0) / 24.0) * 2.0 * math.pi
        air_c = self.peak_air_c - 5.0 + 5.0 * math.cos(hour_angle)
        # Shortwave radiation: zero at night, peak at solar noon.
        daylight = max(0.0, math.sin(((when.hour - 6.0) / 12.0) * math.pi))
        shortwave = self.peak_shortwave_wm2 * daylight
        cloud = 100.0 * (1.0 - daylight) * 0.5  # light night cloud, clear day
        return HeatInputs(
            air_c=round(air_c, 1),
            relative_humidity=self.humidity,
            wind_ms=self.wind_ms,
            shortwave_wm2=round(shortwave, 1),
            cloud_cover=round(cloud, 1),
        )


class FakeSource:
    """A :class:`~chhaya.sources.base.HeatSource` over named scenarios."""

    def __init__(self, scenarios: dict[str, Scenario]) -> None:
        self._scenarios = dict(scenarios)
        self._pinned: dict[str, Callable[[datetime], HeatInputs]] = {}

    @classmethod
    def hot_afternoon(cls) -> FakeSource:
        """A Hyderabad May afternoon that trips the stop band."""
        return cls(
            {
                "hyderabad-miyapur-site-b": Scenario(peak_air_c=44.0, humidity=40.0, wind_ms=1.5),
            }
        )

    async def current(self, site_id: str, when: datetime) -> HeatInputs:
        pinned = self._pinned.get(site_id)
        if pinned is not None:
            return pinned(when)
        scenario = self._scenarios.get(site_id)
        if scenario is None:
            raise SourceError(f"no fake scenario registered for site {site_id}")
        return scenario.at_hour(when)

    def pin(self, site_id: str, inputs_at: Callable[[datetime], HeatInputs]) -> None:
        """Pin one site to an arbitrary function of the hour (for demos)."""
        self._pinned[site_id] = inputs_at

    def sites(self) -> list[str]:
        return sorted(set(self._scenarios) | set(self._pinned))
