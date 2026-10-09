"""A weather backend that replays a recorded fixture: the "history simulated,
today live" half of the honesty story.

The file format is a list of hours, each with the raw Open-Meteo-style fields,
so a recording captured from the live service replays without translation.
Recorded mode is what the demo room uses when the network is down, what the
video uses for a repeatable peak hour, and what regression tests pin.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from chhaya.domain.heat import HeatInputs
from chhaya.sources.base import SourceError

__all__ = ["RecordedSource"]


class RecordedSource:
    """A :class:`~chhaya.sources.base.HeatSource` over a JSON fixture."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SourceError(f"cannot read recording {self._path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise SourceError(f"recording {self._path} must be an object of site_id -> hours")
        self._hours: dict[str, dict[str, HeatInputs]] = {}
        for site_id, rows in payload.items():
            if not isinstance(rows, list):
                raise SourceError(f"recording for {site_id} must be a list of hours")
            table: dict[str, HeatInputs] = {}
            for row in rows:
                try:
                    stamp = datetime.fromisoformat(str(row["time"]))
                    inputs = HeatInputs(
                        air_c=float(row["temperature_2m"]),
                        relative_humidity=float(row["relative_humidity_2m"]),
                        wind_ms=float(row["wind_speed_10m"]),
                        shortwave_wm2=float(row["shortwave_radiation"]),
                        cloud_cover=float(row["cloud_cover"]),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise SourceError(f"bad hour in recording for {site_id}: {row!r}") from exc
                inputs.validate()
                table[stamp.replace(minute=0, second=0, microsecond=0).isoformat()] = inputs
            self._hours[site_id] = table

    async def current(self, site_id: str, when: datetime) -> HeatInputs:
        table = self._hours.get(site_id)
        if table is None:
            raise SourceError(f"site {site_id} is not in the recording")
        key = when.replace(minute=0, second=0, microsecond=0).isoformat()
        try:
            return table[key]
        except KeyError:
            raise SourceError(f"recording for {site_id} has no hour {key}") from None

    @property
    def recorded_hours(self) -> dict[str, list[str]]:
        """Which hours each site's recording actually covers."""
        return {site_id: sorted(table) for site_id, table in self._hours.items()}
