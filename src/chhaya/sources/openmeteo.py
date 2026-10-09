"""Live weather from Open-Meteo: free, no API key, 10k calls/day.

https://open-meteo.com/ — the response shape is stable and the service has no
signup, which is exactly what a hackathon demo room needs. The backend is
constructed with a site_id -> (latitude, longitude) map so it never has to
reach back into the policy layer, and it only fetches the four fields the
heat engines consume.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from chhaya.domain.heat import HeatInputs
from chhaya.sources.base import SourceError

__all__ = ["OpenMeteoSource", "SiteLocation"]

_BASE_URL = "https://api.open-meteo.com/v1/forecast"
_HOURLY = "temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation,cloud_cover"
_UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://open-meteo.com/",
}


class SiteLocation:
    """A site's coordinates. Open-Meteo only needs these."""

    def __init__(self, latitude: float, longitude: float) -> None:
        self.latitude = latitude
        self.longitude = longitude


class OpenMeteoSource:
    """A :class:`~chhaya.sources.base.HeatSource` backed by Open-Meteo.

    The client is injectable so tests can point it at a canned response
    without touching the network, and so the caller controls timeouts.
    """

    def __init__(
        self,
        sites: dict[str, SiteLocation],
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._sites = sites
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=_BASE_URL,
            headers=_UA,
            follow_redirects=True,
            timeout=timeout_seconds,
        )

    async def current(self, site_id: str, when: datetime) -> HeatInputs:
        location = self._sites.get(site_id)
        if location is None:
            raise SourceError(f"unknown site for Open-Meteo: {site_id}")
        try:
            response = await self._client.get(
                "",
                params={
                    "latitude": location.latitude,
                    "longitude": location.longitude,
                    "hourly": _HOURLY,
                    "wind_speed_unit": "ms",
                    "forecast_days": 2,
                },
            )
            response.raise_for_status()
            payload: dict[str, Any] = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceError(f"Open-Meteo request failed for {site_id}: {exc}") from exc
        return self._parse(payload, when, site_id)

    @staticmethod
    def _parse(payload: dict[str, Any], when: datetime, site_id: str) -> HeatInputs:
        hourly = payload.get("hourly")
        if not isinstance(hourly, dict):
            raise SourceError(f"Open-Meteo response for {site_id} has no hourly block")
        times: list[str] = hourly.get("time") or []
        wanted = when.replace(minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
        try:
            index = times.index(wanted)
        except ValueError:
            raise SourceError(f"Open-Meteo forecast for {site_id} has no hour {wanted}") from None

        def field(name: str) -> float:
            values = hourly.get(name) or []
            if index >= len(values) or values[index] is None:
                raise SourceError(f"Open-Meteo field {name} missing for {site_id} at {wanted}")
            return float(values[index])

        return HeatInputs(
            air_c=field("temperature_2m"),
            relative_humidity=field("relative_humidity_2m"),
            wind_ms=field("wind_speed_10m"),
            shortwave_wm2=field("shortwave_radiation"),
            cloud_cover=field("cloud_cover"),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
