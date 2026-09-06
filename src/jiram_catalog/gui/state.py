"""The one parameterised object every view reads and writes.

Filters, the current selection and the current objects live here so that
the three tabs cannot disagree with each other, and so that a session is
a small JSON document (:meth:`CatalogState.to_json`) that can be mailed,
committed, or reloaded (:meth:`CatalogState.from_json`).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import param

#: Latitude band edges and names, degrees; the same seven bands the
#: trackability table uses (half-open ``[lo, hi)`` except the last).
LAT_BAND_EDGES: tuple[float, ...] = (-90.0, -60.0, -30.0, -10.0, 10.0, 30.0, 60.0, 90.0)
LAT_BAND_NAMES: tuple[str, ...] = (
    "S polar",
    "S mid",
    "S low",
    "equator",
    "N low",
    "N mid",
    "N polar",
)

#: The archive's span, wide enough that the default date range filters nothing.
EPOCH_MIN = datetime(2016, 1, 1)
EPOCH_MAX = datetime(2027, 1, 1)

#: Coarsest pixel in the geometry table is ~1900 km; the default excludes nothing.
MAX_PIXEL_KM = 2000.0


class CatalogState(param.Parameterized):
    """Filters, selection and current objects, shared by all three tabs."""

    # ---------------------------------------------------------------- catalog
    orbit_range = param.Range(
        default=(1, 99), bounds=(1, 99), doc="inclusive range of orbit directories"
    )
    date_range = param.DateRange(
        default=(EPOCH_MIN, EPOCH_MAX), bounds=(EPOCH_MIN, EPOCH_MAX)
    )
    band = param.Selector(default="all", objects=["all", "L", "M"])
    resolution_max_km = param.Number(default=MAX_PIXEL_KM, bounds=(0.0, MAX_PIXEL_KM))
    emission_max = param.Number(default=90.0, bounds=(0.0, 90.0))
    on_planet_min = param.Number(default=0.0, bounds=(0.0, 1.0))
    dayside_only = param.Boolean(default=False)
    lat_band = param.Selector(default="all", objects=["all", *LAT_BAND_NAMES])
    revisit_only = param.Boolean(
        default=False, doc="keep frames with a same-pass revisit (trackability table)"
    )
    polar_view = param.Selector(default="cyl", objects=["cyl", "N", "S"])

    # ------------------------------------------------------- selection/current
    selection = param.List(default=[], item_type=str, doc="selected product ids")
    current_stack = param.String(default="", doc="path of the stack in the Poles tab")
    current_strip = param.String(default="", doc="strip_id shown in the Strips tab")
    poles_orbits = param.List(
        default=[], item_type=int, doc="orbits sent to the Poles tab from a selection"
    )

    # ----------------------------------------------------------------- strips
    strip_band = param.Selector(default="all", objects=["all", "L", "M"])
    strip_lat_band = param.Selector(default="all", objects=["all", *LAT_BAND_NAMES])
    strip_date_range = param.DateRange(
        default=(EPOCH_MIN, EPOCH_MAX), bounds=(EPOCH_MIN, EPOCH_MAX)
    )
    strip_resolution_max_km = param.Number(
        default=MAX_PIXEL_KM, bounds=(0.0, MAX_PIXEL_KM)
    )
    strip_valid_min = param.Number(default=0.0, bounds=(0.0, 1.0))
    strip_dayside_min = param.Number(default=0.0, bounds=(0.0, 1.0))

    #: Parameters a saved session carries, in a stable order.
    SESSION_PARAMETERS: tuple[str, ...] = (
        "orbit_range",
        "date_range",
        "band",
        "resolution_max_km",
        "emission_max",
        "on_planet_min",
        "dayside_only",
        "lat_band",
        "revisit_only",
        "polar_view",
        "selection",
        "current_stack",
        "current_strip",
        "poles_orbits",
        "strip_band",
        "strip_lat_band",
        "strip_date_range",
        "strip_resolution_max_km",
        "strip_valid_min",
        "strip_dayside_min",
    )

    # ------------------------------------------------------------------ (de)serialisation
    def to_json(self, *, indent: int | None = 2) -> str:
        """The session as JSON: dates as ISO strings, ranges as pairs."""
        payload: dict[str, Any] = {}
        for name in self.SESSION_PARAMETERS:
            payload[name] = _encode(getattr(self, name))
        return json.dumps(payload, indent=indent, sort_keys=False)

    def from_json(self, text: str | bytes) -> "CatalogState":
        """Apply a session written by :meth:`to_json`; returns ``self``.

        Unknown keys are ignored, so a session saved by an older version
        still loads, and a value the parameter refuses (a band that is
        not an option, say) leaves that parameter alone rather than
        failing the whole load.
        """
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("a session must be a JSON object")
        updates: dict[str, Any] = {}
        for name in self.SESSION_PARAMETERS:
            if name not in payload:
                continue
            parameter = self.param[name]
            try:
                updates[name] = _decode(parameter, payload[name])
            except (TypeError, ValueError):
                continue
        for name, value in updates.items():
            try:
                setattr(self, name, value)
            except ValueError:
                continue
        return self


def _encode(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (tuple, list)):
        return [_encode(item) for item in value]
    return value


def _decode(parameter: param.Parameter, value: Any) -> Any:
    if isinstance(parameter, param.DateRange):
        first, second = value
        return (_as_datetime(first), _as_datetime(second))
    if isinstance(parameter, param.Range):
        first, second = value
        return (type(parameter.default[0])(first), type(parameter.default[1])(second))
    if isinstance(parameter, param.List):
        item = parameter.item_type or str
        return [item(entry) for entry in value]
    if isinstance(parameter, param.Boolean):
        return bool(value)
    if isinstance(parameter, param.Number):
        return float(value)
    if isinstance(parameter, param.String):
        return str(value)
    return value


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
