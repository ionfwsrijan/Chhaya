"""Shared fixtures for Chhaya's test suite."""

from __future__ import annotations

import pytest

from chhaya.domain.heat import HeatInputs


@pytest.fixture
def mild_day() -> HeatInputs:
    """A comfortable morning: warm, dry-ish, light breeze, some sun."""
    return HeatInputs(
        air_c=26.0,
        relative_humidity=45.0,
        wind_ms=1.5,
        shortwave_wm2=300.0,
        cloud_cover=60.0,
    )


@pytest.fixture
def humid_coastal_afternoon() -> HeatInputs:
    """The case air-temperature rules under-read: not hot, but very humid.

    Dry bulb 30 C looks mild on a screen that only shows air temperature, but
    at 70 % relative humidity with almost no wind the WBGT is well over the
    NIOSH Recommended Alert Limit for unacclimatized moderate work. This is
    the scenario Chhaya exists to catch.
    """
    return HeatInputs(
        air_c=30.0,
        relative_humidity=70.0,
        wind_ms=0.5,
        shortwave_wm2=100.0,
    )


@pytest.fixture
def hot_dry_desert_afternoon() -> HeatInputs:
    """Blistering and bone-dry: the case the air-temperature schedule catches."""
    return HeatInputs(
        air_c=44.0,
        relative_humidity=10.0,
        wind_ms=4.0,
        shortwave_wm2=950.0,
        cloud_cover=0.0,
    )


@pytest.fixture
def still_humid_night() -> HeatInputs:
    """Night on a construction site: no sun, but the heat has not left."""
    return HeatInputs(
        air_c=31.0,
        relative_humidity=80.0,
        wind_ms=0.3,
        shortwave_wm2=0.0,
        cloud_cover=100.0,
    )
