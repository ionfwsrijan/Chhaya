"""The single interface every weather backend satisfies.

Chhaya never imports Open-Meteo, a JSON fixture, or a synthetic generator
directly — it asks a :class:`HeatSource` for the weather at a site and an
hour, and every backend must answer in the same shape. That is what lets the
API run live in the cloud, offline in a demo room, and deterministically in
the test suite without changing a line of the safety logic.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from chhaya.domain.heat import HeatInputs

__all__ = ["HeatSource", "SourceError"]


class SourceError(RuntimeError):
    """The weather backend could not answer, and Chhaya should escalate."""


@runtime_checkable
class HeatSource(Protocol):
    """One site's weather, for one hour, in HeatInputs shape."""

    async def current(self, site_id: str, when: datetime) -> HeatInputs:
        """Return the weather at *site_id* for the hour containing *when*.

        Backends must be pure reads: fetching weather never mutates state.
        Raising SourceError is always legal; returning garbage is not.
        """
        ...
