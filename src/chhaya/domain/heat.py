"""Pure heat-stress physics. No I/O, no clock, no AWS.

Every formula here is transcribed from a published source and every source is
named in the docstring, because the whole point of Chhaya is that a supervisor
can be told *why* the site stopped work — with a number and a citation, not a
vibe.

Sources
-------
Stull, R., 2011: Wet-bulb temperature from relative humidity and air
    temperature. J. Appl. Meteor. Climatol., 50, 2267-2269.
    Valid RH 5-99%, T -20..+50 C, 1013.25 hPa. Mean absolute error < 0.3 C.
ISO 7243:2017 — Ergonomics of the thermal environment: assessment of heat
    stress using the WBGT index. Equations (1) and (2), section 5.
Whitaker, S., 1977: Correlating heat and mass transfer data for low Reynolds
    number flow around a sphere. AIChE J., 23, 427-429.
Sutherland, W., 1893: The viscosity of gases and molecular force.
    Philos. Mag., 36, 507-531.
Rothfusz, L.P., 1990: The heat index equation. NWS Technical Attachment SR/SSD
    90-23. (US customary units, converted here.)
Brunt, D., 1932: Notes on radiation in the atmosphere. QJRMS, 58, 389-400.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

SIGMA = 5.670374419e-8  # Stefan-Boltzmann constant, W m^-2 K^-4
GLOBE_DIAMETER_M = 0.150  # ISO 7243 standard black globe, 150 mm
GLOBE_ABSORPTIVITY = 0.95  # matte black globe, solar band
GLOBE_EMISSIVITY = 0.95  # matte black globe, longwave band
R_AIR = 287.058  # specific gas constant for dry air, J kg^-1 K^-1
P_STANDARD = 101325.0  # Pa — Stull's fit is sea-level specific
PRANDTL_AIR = 0.7  # Prandtl number of air, weakly temperature dependent
K_REF = 0.0241  # W m^-1 K^-1, air conductivity at 0 C
K_EXP = 0.85  # power-law exponent fit to standard air property tables


class HeatInputError(ValueError):
    """Raised when inputs fall outside a source's stated validity range."""


@dataclass(frozen=True, slots=True)
class HeatInputs:
    """One hour of weather at one site, in SI-ish working units.

    Attributes:
        air_c: Dry-bulb air temperature, degrees Celsius.
        relative_humidity: Relative humidity, percent (0-100).
        wind_ms: Wind speed at 10 m, metres per second.
        shortwave_wm2: Global shortwave radiation on a horizontal surface,
            watts per square metre.
        cloud_cover: Total cloud cover, percent (0-100), optional because not
            every forecast supplies it.
        measured_wbgt_c: A WBGT reading from a real meter at the site, when the
            site has one. Overrides the model entirely.
        measured_globe_c: A measured black-globe temperature, when available.
    """

    air_c: float
    relative_humidity: float
    wind_ms: float
    shortwave_wm2: float = 0.0
    cloud_cover: float | None = None
    measured_wbgt_c: float | None = None
    measured_globe_c: float | None = None

    def validate(self) -> None:
        if not -20.0 <= self.air_c <= 50.0:
            raise HeatInputError(
                f"air_c {self.air_c} outside Stull (2011) validity range -20..50 C"
            )
        if not 5.0 <= self.relative_humidity <= 99.0:
            raise HeatInputError(
                f"relative_humidity {self.relative_humidity} outside Stull (2011) "
                "validity range 5..99 %"
            )
        if self.wind_ms < 0.0:
            raise HeatInputError("wind_ms cannot be negative")
        if self.shortwave_wm2 < 0.0:
            raise HeatInputError("shortwave_wm2 cannot be negative")


def saturation_vapour_pressure_hpa(air_c: float) -> float:
    """Saturation vapour pressure over liquid water, hPa (Alduchov & Eskridge
    1996 Magnus form, the WMO-recommended coefficients)."""
    return 6.1094 * math.exp(17.625 * air_c / (air_c + 243.04))


def vapour_pressure_hpa(air_c: float, relative_humidity: float) -> float:
    return saturation_vapour_pressure_hpa(air_c) * relative_humidity / 100.0


def dew_point_c(air_c: float, relative_humidity: float) -> float:
    """Dew point, degrees Celsius (inverse Magnus, Alduchov & Eskridge)."""
    if relative_humidity <= 0.0:
        raise HeatInputError("relative_humidity must be positive to get a dew point")
    gamma = math.log(relative_humidity / 100.0) + 17.625 * air_c / (air_c + 243.04)
    return 243.04 * gamma / (17.625 - gamma)


def wet_bulb_stull_c(air_c: float, relative_humidity: float) -> float:
    """Psychrometric (shade) wet-bulb temperature, degrees Celsius.

    Stull (2011) equation (1). Arctangents take radians. Valid for RH 5-99 %
    and T -20..50 C at 1013.25 hPa; errors -1.0 to +0.65 C, mean absolute
    error below 0.3 C.

    This is the *shielded* wet bulb. In direct sun the natural wet bulb that
    ISO 7243 asks for runs warmer, so treating the two as equal under-reads
    heat stress — which is why Chhaya never decides on WBGT alone.
    """
    rh = relative_humidity
    t = air_c
    return (
        t * math.atan(0.151977 * math.sqrt(rh + 8.313659))
        + math.atan(t + rh)
        - math.atan(rh - 1.676331)
        + 0.00391838 * math.pow(rh, 1.5) * math.atan(0.023101 * rh)
        - 4.686035
    )


def heat_index_c(air_c: float, relative_humidity: float) -> float:
    """Apparent temperature (NWS heat index), degrees Celsius.

    Rothfusz (1990), the US customary equation converted to SI and applied
    only when it is meaningful: below about 27 C the NWS does not report a
    heat index at all, so this returns the air temperature there rather than
    a number that can fall below the air temperature and understate the heat
    a worker actually feels.
    """
    t_f = air_c * 9.0 / 5.0 + 32.0
    if t_f < 80.0 and relative_humidity < 40.0:
        return air_c
    r = relative_humidity
    t_f2 = t_f * t_f
    r2 = r * r
    hi_f = (
        -42.379
        + 2.04901523 * t_f
        + 10.14333127 * r
        - 0.22475541 * t_f * r
        - 6.83783e-3 * t_f2
        - 5.481717e-2 * r2
        + 1.22874e-3 * t_f2 * r
        + 8.5282e-4 * t_f * r2
        - 1.99e-6 * t_f2 * r2
    )
    if r < 13.0 and t_f >= 80.0:
        hi_f -= ((13.0 - r) / 4.0) * math.sqrt((17.0 - abs(t_f - 95.0)) / 17.0)
    elif r > 85.0 and t_f < 80.0:
        hi_f += ((r - 85.0) / 10.0) * ((7.0 - t_f) / 7.0)
    # Below the NWS reporting threshold the regression was fitted for, its
    # output can fall *below* the air temperature (a mild afternoon comes out
    # 1 C cooler than it is), which for a heat-safety tool is a worse failure
    # than no number at all. NWS guidance is to report the air temperature
    # instead, so this does.
    if hi_f < 80.0:
        return air_c
    return (hi_f - 32.0) * 5.0 / 9.0


def _sutherland_viscosity_pa_s(temp_k: float) -> float:
    """Dynamic viscosity of air (Sutherland 1893), Pa s."""
    return 1.458e-6 * math.pow(temp_k, 1.5) / (temp_k + 110.4)


def _air_conductivity(temp_k: float) -> float:
    """Thermal conductivity of air, W m^-1 K^-1 (power law fitted to standard
    property tables; accurate to about 2 % from 0 to 100 C)."""
    return K_REF * math.pow(temp_k / 273.15, K_EXP)


def sky_emissivity_brunt(air_c: float, relative_humidity: float) -> float:
    """Clear-sky effective emissivity, Brunt (1932): 0.52 + 0.065*sqrt(e)."""
    e = vapour_pressure_hpa(air_c, relative_humidity)
    return 0.52 + 0.065 * math.sqrt(max(e, 0.0))


def _sphere_natural_convection(temp_k: float, wall_k: float) -> float:
    """Free-convection coefficient h for a 150 mm sphere, W m^-2 K^-1.

    Churchill (1983) sphere correlation. At forecast wind speeds below about
    1 m/s this is the same size as the forced term, so omitting it would let
    the modelled globe run 10 C hot on still, sunny afternoons — exactly the
    hours that matter.
    """
    beta = 1.0 / temp_k
    mu = _sutherland_viscosity_pa_s(temp_k)
    rho = P_STANDARD / (R_AIR * temp_k)
    nu = mu / rho
    alpha = nu / PRANDTL_AIR
    delta_t = max(wall_k - temp_k, 0.1)
    rayleigh = 9.81 * beta * delta_t * math.pow(GLOBE_DIAMETER_M, 3) / (nu * alpha)
    denominator = math.pow(1.0 + (0.469 / PRANDTL_AIR) ** (9.0 / 16.0), 4.0 / 9.0)
    nusselt = 2.0 + 0.589 * math.pow(rayleigh, 0.25) / denominator
    return nusselt * _air_conductivity(temp_k) / GLOBE_DIAMETER_M


def _sphere_convection(temp_k: float, wall_k: float, wind_ms: float) -> float:
    """Combined convective coefficient for a 150 mm sphere, W m^-2 K^-1.

    Forced term from Whitaker (1977) with Sutherland (1893) viscosity; free
    term from Churchill (1983); combined with the usual cube-sum rule so the
    stronger mechanism dominates as the wind picks up.
    """
    h_forced = _sphere_forced_convection(temp_k, wind_ms)
    h_free = _sphere_natural_convection(temp_k, wall_k)
    return math.pow(h_forced**3 + h_free**3, 1.0 / 3.0)


def _sphere_forced_convection(temp_k: float, wind_ms: float) -> float:
    """Forced-convection coefficient h for a 150 mm sphere, W m^-2 K^-1.

    Whitaker (1977) Nusselt correlation with air properties from Sutherland's
    law and the ideal gas law; Prandtl number held at 0.7.
    """
    mu = _sutherland_viscosity_pa_s(temp_k)
    rho = P_STANDARD / (R_AIR * temp_k)
    nu = mu / rho
    reynolds = max(wind_ms, 0.1) * GLOBE_DIAMETER_M / nu
    nusselt = 2.0 + (0.4 * math.sqrt(reynolds) + 0.06 * math.pow(reynolds, 2.0 / 3.0)) * math.pow(
        PRANDTL_AIR, 0.4
    )
    return nusselt * _air_conductivity(temp_k) / GLOBE_DIAMETER_M


def globe_temperature_c(
    air_c: float,
    relative_humidity: float,
    wind_ms: float,
    shortwave_wm2: float,
) -> float:
    """Steady-state black-globe temperature, degrees Celsius.

    Energy balance on a 150 mm matte black globe: solar absorbed plus sky
    longwave in equals emitted longwave plus convective out. Solved by
    bisection because the radiative term is quartic.

    This is a *model*, not a measurement. It assumes a matte black sphere in
    free air, no ground reflection, and a clear-sky longwave sink from Brunt
    (1932). ISO 7243 Annex B says direct measurement is preferred; treat this
    as a screening estimate with roughly 3 C of uncertainty, and let a site
    with a real meter override it through ``HeatInputs.measured_globe_c``.
    """
    air_k = air_c + 273.15
    emissivity_sky = sky_emissivity_brunt(air_c, relative_humidity)
    sky_k = air_k * math.pow(emissivity_sky, 0.25)
    absorbed = GLOBE_ABSORPTIVITY * shortwave_wm2 + GLOBE_EMISSIVITY * SIGMA * math.pow(sky_k, 4)

    def imbalance(guess_c: float) -> float:
        guess_k = guess_c + 273.15
        emitted = GLOBE_EMISSIVITY * SIGMA * math.pow(guess_k, 4)
        convected = _sphere_convection(air_k, guess_k, wind_ms) * (guess_c - air_c)
        return absorbed - emitted - convected

    low, high = air_c - 30.0, air_c + 80.0
    if imbalance(low) < 0.0:
        return low
    if imbalance(high) > 0.0:
        return high
    for _ in range(80):
        mid = (low + high) / 2.0
        if imbalance(mid) > 0.0:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


@dataclass(frozen=True, slots=True)
class WbgtResult:
    """WBGT in degrees Celsius plus the inputs that produced it.

    Attributes:
        value_c: The WBGT value to screen against.
        shaded_c: ISO 7243 equation (1) — no solar load.
        sun_c: ISO 7243 equation (2) — with solar load.
        natural_wet_bulb_c: Wet bulb used in the weighting.
        globe_c: Globe temperature used in the weighting.
        sun_load: True when equation (2) was selected.
        modeled: True when globe or wet bulb came from a model rather than a
            meter at the site.
    """

    value_c: float
    shaded_c: float
    sun_c: float
    natural_wet_bulb_c: float
    globe_c: float
    sun_load: bool
    modeled: bool


def wbgt_from_components(
    natural_wet_bulb_c: float,
    globe_c: float,
    air_c: float,
    sun_load: bool,
) -> float:
    """ISO 7243:2017 section 5, equations (1) and (2), as written.

    Kept separate from :func:`compute_wbgt` so the standard's own Annex D
    reference table can be replayed against this function directly.
    """
    if sun_load:
        return 0.7 * natural_wet_bulb_c + 0.2 * globe_c + 0.1 * air_c
    return 0.7 * natural_wet_bulb_c + 0.3 * globe_c


def _has_sun(inputs: HeatInputs) -> bool:
    return inputs.shortwave_wm2 >= 50.0


def compute_wbgt(inputs: HeatInputs) -> WbgtResult:
    """WBGT by ISO 7243:2017, equation (1) or (2).

    Equation (1), no solar load:  WBGT = 0.7*tnw + 0.3*tg
    Equation (2), with solar load: WBGT = 0.7*tnw + 0.2*tg + 0.1*ta

    ISO reduces the globe weight under direct sun because a black globe
    over-reads shortwave compared with human skin. Chhaya also reports the
    unweighted ``sun_c`` so a caller can see how much of the number is
    radiant load rather than humidity.
    """
    inputs.validate()
    wet_bulb = wet_bulb_stull_c(inputs.air_c, inputs.relative_humidity)

    if inputs.measured_wbgt_c is not None:
        globe = inputs.measured_globe_c if inputs.measured_globe_c is not None else inputs.air_c
        return WbgtResult(
            value_c=inputs.measured_wbgt_c,
            shaded_c=inputs.measured_wbgt_c,
            sun_c=inputs.measured_wbgt_c,
            natural_wet_bulb_c=wet_bulb,
            globe_c=globe,
            sun_load=_has_sun(inputs),
            modeled=False,
        )

    globe = (
        inputs.measured_globe_c
        if inputs.measured_globe_c is not None
        else globe_temperature_c(
            inputs.air_c,
            inputs.relative_humidity,
            inputs.wind_ms,
            inputs.shortwave_wm2,
        )
    )
    sun_load = _has_sun(inputs)
    # The shade reading is equation (1) with the globe at air temperature:
    # JNIOSH's field check found Tg = Ta agreed with a real globe to within
    # 0.5 C when there is no shortwave. It is what a worker under a tarp
    # actually experiences, and the gap between it and sun_load is the whole
    # argument for stopping work.
    return WbgtResult(
        value_c=wbgt_from_components(wet_bulb, globe, inputs.air_c, sun_load),
        shaded_c=wbgt_from_components(wet_bulb, inputs.air_c, inputs.air_c, False),
        sun_c=wbgt_from_components(wet_bulb, globe, inputs.air_c, True),
        natural_wet_bulb_c=wet_bulb,
        globe_c=globe,
        sun_load=sun_load,
        modeled=inputs.measured_globe_c is None,
    )


@dataclass(frozen=True, slots=True)
class HeatStress:
    """Everything Chhaya can say about one hour of weather at one site."""

    inputs: HeatInputs
    wbgt: WbgtResult
    wet_bulb_c: float
    dew_point_c: float
    heat_index_c: float

    @property
    def air_c(self) -> float:
        return self.inputs.air_c

    @property
    def wbgt_c(self) -> float:
        return self.wbgt.value_c

    def as_dict(self) -> dict[str, float | bool]:
        """Flat, JSON-safe view for the API, the console and the audit trail."""
        return {
            "air_c": round(self.air_c, 2),
            "relative_humidity": round(self.inputs.relative_humidity, 1),
            "wind_ms": round(self.inputs.wind_ms, 2),
            "shortwave_wm2": round(self.inputs.shortwave_wm2, 1),
            "wet_bulb_c": round(self.wet_bulb_c, 2),
            "dew_point_c": round(self.dew_point_c, 2),
            "heat_index_c": round(self.heat_index_c, 2),
            "wbgt_c": round(self.wbgt_c, 2),
            "wbgt_shaded_c": round(self.wbgt.shaded_c, 2),
            "wbgt_sun_c": round(self.wbgt.sun_c, 2),
            "globe_c": round(self.wbgt.globe_c, 2),
            "sun_load": self.wbgt.sun_load,
            "wbgt_modeled": self.wbgt.modeled,
        }


def analyse(inputs: HeatInputs) -> HeatStress:
    """Run every index over one hour of weather."""
    inputs.validate()
    return HeatStress(
        inputs=inputs,
        wbgt=compute_wbgt(inputs),
        wet_bulb_c=wet_bulb_stull_c(inputs.air_c, inputs.relative_humidity),
        dew_point_c=dew_point_c(inputs.air_c, inputs.relative_humidity),
        heat_index_c=heat_index_c(inputs.air_c, inputs.relative_humidity),
    )
