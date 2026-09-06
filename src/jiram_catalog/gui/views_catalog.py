"""The Catalog tab: every frame that sees the planet, filtered and selected.

The map is a datashader raster of the boresight points, so the 47,600
rows redraw as fast as the filters change; below about 5,000 rows the
individual points are overlaid on top of it with their metadata in the
hover, because at that size the user is looking at frames rather than at
coverage.  A box on the map is a selection, and the selection is what the
other two tabs are pointed at.
"""

from __future__ import annotations

import io
from pathlib import Path

import holoviews as hv
import holoviews.operation.datashader as hd
import numpy as np
import pandas as pd
import panel as pn
import param

from . import extensions
from . import data as data_module
from .state import LAT_BAND_NAMES, CatalogState

#: State parameters the Catalog tab's filtered set depends on.
FILTER_PARAMETERS: tuple[str, ...] = (
    "orbit_range",
    "date_range",
    "band",
    "resolution_max_km",
    "emission_max",
    "on_planet_min",
    "dayside_only",
    "lat_band",
    "revisit_only",
)

#: Above this many rows the map is the raster alone; below it, points with hover.
HOVER_LIMIT = 5_000

#: Columns the selection table and its CSV carry.
TABLE_COLUMNS: tuple[str, ...] = (
    "product_id",
    "orbit",
    "half",
    "seq_id",
    "start_time",
    "bore_lat",
    "bore_lon_east",
    "bore_emission",
    "median_pixel_km",
    "on_planet_frac",
    "dayside_frac",
    "lat_band",
)

_HOVER_COLUMNS = ("product_id", "start_time", "median_pixel_km", "bore_emission")


def polar_xy(lat, lon_east, hemisphere: str) -> tuple[np.ndarray, np.ndarray]:
    """Azimuthal-equidistant display coordinates, degrees from the pole.

    ``rho = 90 - |lat|`` and the east longitude is the polar angle, so a
    point at the pole is at the origin and the equator is the rim.  Rows
    in the other hemisphere come back as NaN, which draws nothing.
    """
    latitude = np.asarray(lat, dtype=np.float64)
    longitude = np.asarray(lon_east, dtype=np.float64)
    if hemisphere.upper().startswith("N"):
        keep = latitude >= 0.0
    else:
        keep = latitude <= 0.0
    rho = np.where(keep, 90.0 - np.abs(latitude), np.nan)
    theta = np.radians(longitude)
    return rho * np.cos(theta), rho * np.sin(theta)


def display_frame(frame: pd.DataFrame, view: str) -> pd.DataFrame:
    """Add the ``map_x``/``map_y`` the current view draws."""
    out = frame.copy()
    if out.empty:
        out["map_x"] = np.empty(0, dtype=np.float64)
        out["map_y"] = np.empty(0, dtype=np.float64)
        return out
    if view == "cyl":
        out["map_x"] = out["bore_lon_east"].to_numpy(dtype=np.float64)
        out["map_y"] = out["bore_lat"].to_numpy(dtype=np.float64)
    else:
        x, y = polar_xy(out["bore_lat"], out["bore_lon_east"], view)
        out["map_x"], out["map_y"] = x, y
    return out


def view_limits(view: str) -> tuple[tuple[float, float], tuple[float, float]]:
    """Axis ranges of a view, so an empty filter still draws the whole map."""
    if view == "cyl":
        return (0.0, 360.0), (-90.0, 90.0)
    return (-92.0, 92.0), (-92.0, 92.0)


def products_within(frame, bounds) -> list[str] | None:
    """Product ids inside a box drawn on the map, in display coordinates.

    The test is done on ``map_x``/``map_y``, so it means the same thing
    in the cylindrical view and in either polar view.  ``None`` says the
    box was not a selection (no box, or nothing to select from) and the
    current selection should stand.
    """
    if bounds is None or frame is None or getattr(frame, "empty", True):
        return None
    x0, y0, x1, y1 = bounds
    x = frame["map_x"].to_numpy(dtype=np.float64)
    y = frame["map_y"].to_numpy(dtype=np.float64)
    inside = (
        (x >= min(x0, x1))
        & (x <= max(x0, x1))
        & (y >= min(y0, y1))
        & (y <= max(y0, y1))
    )
    return [str(value) for value in frame.loc[inside, "product_id"]]


class _Displayed(param.Parameterized):
    """The filtered rows in display coordinates; one recompute per change."""

    frame = param.Parameter(default=None)
    view = param.String(default="cyl")


def filters(state: CatalogState, mirror: str | Path | None = None) -> pn.Column:
    """The Catalog tab's sidebar widgets."""
    extensions()
    widgets: list = [
        pn.pane.Markdown("### Catalog filters", margin=(0, 0, -10, 0)),
        pn.widgets.IntRangeSlider.from_param(state.param.orbit_range, name="orbit"),
        pn.widgets.DatetimeRangeInput.from_param(state.param.date_range, name="date"),
        pn.widgets.RadioButtonGroup.from_param(
            state.param.band, name="band half"
        ),
        pn.widgets.EditableFloatSlider.from_param(
            state.param.resolution_max_km, name="pixel <= (km)", step=5.0
        ),
        pn.widgets.EditableFloatSlider.from_param(
            state.param.emission_max, name="emission <= (deg)", step=1.0
        ),
        pn.widgets.EditableFloatSlider.from_param(
            state.param.on_planet_min, name="on-planet >=", step=0.05
        ),
        pn.widgets.Checkbox.from_param(state.param.dayside_only, name="dayside only"),
        pn.widgets.Select.from_param(state.param.lat_band, name="latitude band"),
    ]
    if data_module.has_trackability(mirror):
        widgets.append(
            pn.widgets.Checkbox.from_param(
                state.param.revisit_only, name="same-pass revisit only"
            )
        )
    widgets.append(
        pn.widgets.RadioButtonGroup.from_param(state.param.polar_view, name="view")
    )
    return pn.Column(*widgets, sizing_mode="stretch_width")


def build(state: CatalogState, mirror: str | Path | None = None) -> pn.Column:
    """Map, coverage panels and selection table over the frame catalog."""
    extensions()
    table = data_module.catalog_table(mirror)
    displayed = _Displayed()

    def refresh(*_events) -> None:
        filtered = data_module.apply_filters(table, state)
        displayed.param.update(
            frame=display_frame(filtered, state.polar_view), view=state.polar_view
        )

    refresh()
    state.param.watch(refresh, list(FILTER_PARAMETERS) + ["polar_view"])

    stream = hv.streams.Params(
        parameterized=displayed, parameters=["frame", "view"], rename={}
    )

    def points(frame, view):
        x_range, y_range = view_limits(view)
        labels = (
            ("east longitude (deg)", "latitude (deg)")
            if view == "cyl"
            else (f"{view} polar: 90 - |lat| (deg)", "")
        )
        return hv.Points(frame, kdims=["map_x", "map_y"]).opts(
            xlim=x_range, ylim=y_range, xlabel=labels[0], ylabel=labels[1]
        )

    def hover_points(frame, view):
        columns = [name for name in _HOVER_COLUMNS if name in getattr(frame, "columns", [])]
        small = frame if len(frame) < HOVER_LIMIT else frame.iloc[:0]
        return hv.Points(small, kdims=["map_x", "map_y"], vdims=columns).opts(
            color="#e45756",
            size=4,
            alpha=0.8,
            tools=["hover", "box_select"],
            xlim=view_limits(view)[0],
            ylim=view_limits(view)[1],
        )

    raster = hd.rasterize(hv.DynamicMap(points, streams=[stream])).opts(
        cmap="viridis",
        cnorm="eq_hist",
        colorbar=True,
        clabel="frames per pixel",
        tools=["box_select"],
        active_tools=["box_zoom"],
        responsive=True,
        height=380,
        title="boresight coverage",
    )
    interactive = hv.DynamicMap(hover_points, streams=[stream])
    bounds = hv.streams.BoundsXY(source=raster)

    def on_bounds(bounds=None) -> None:
        selected = products_within(displayed.frame, bounds)
        if selected is not None:
            state.selection = selected

    bounds.add_subscriber(on_bounds)
    map_pane = pn.pane.HoloViews(raster * interactive, sizing_mode="stretch_width")

    # ---------------------------------------------------------------- coverage
    def coverage(frame, view):
        del view
        if frame is None or frame.empty:
            empty = hv.Bars([], "x", "frames")
            return (empty + empty + hv.Curve([], "month", "frames")).cols(3)
        counts = (
            pd.Series(frame["lat_band"]).value_counts().reindex(LAT_BAND_NAMES).fillna(0)
        )
        bands = hv.Bars(
            [(name, float(value)) for name, value in counts.items()],
            hv.Dimension("band"),
            hv.Dimension("frames"),
        ).opts(xrotation=45, height=260, responsive=True, title="by latitude band")
        by_orbit = frame["orbit"].value_counts().sort_index()
        orbits = hv.Bars(
            [(int(k), float(v)) for k, v in by_orbit.items()],
            hv.Dimension("orbit"),
            hv.Dimension("frames"),
        ).opts(xrotation=90, height=260, responsive=True, title="by orbit")
        by_month = frame["month"].value_counts().sort_index()
        line = hv.Curve(
            (list(by_month.index), list(by_month.to_numpy(dtype=float))),
            hv.Dimension("month"),
            hv.Dimension("frames"),
        ).opts(height=260, responsive=True, title="passes in time", interpolation="steps-mid")
        return (bands + orbits + line).cols(3).opts(shared_axes=False)

    coverage_pane = pn.pane.HoloViews(
        hv.DynamicMap(coverage, streams=[stream]), sizing_mode="stretch_width"
    )

    # ----------------------------------------------------------------- table
    def selected_frame() -> pd.DataFrame:
        frame = displayed.frame
        if frame is None:
            return pd.DataFrame(columns=list(TABLE_COLUMNS))
        if state.selection:
            frame = frame.loc[frame["product_id"].isin(set(state.selection))]
        columns = [name for name in TABLE_COLUMNS if name in frame.columns]
        return frame[columns].reset_index(drop=True)

    tabulator = pn.widgets.Tabulator(
        selected_frame(),
        pagination="remote",
        page_size=15,
        disabled=True,
        show_index=False,
        sizing_mode="stretch_width",
        height=430,
    )
    caption = pn.pane.Markdown("")

    def refresh_table(*_events) -> None:
        frame = selected_frame()
        tabulator.value = frame
        total = 0 if displayed.frame is None else len(displayed.frame)
        what = "selected" if state.selection else "filtered"
        caption.object = (
            f"**{len(frame):,} {what}** of {total:,} filtered "
            f"({len(table):,} frames see the planet)"
        )

    refresh_table()
    displayed.param.watch(refresh_table, ["frame"])
    state.param.watch(refresh_table, ["selection"])

    def csv() -> io.StringIO:
        return io.StringIO(selected_frame().to_csv(index=False))

    download = pn.widgets.FileDownload(
        callback=csv, filename="jiram_selection.csv", color="primary", label="CSV"
    )
    to_poles = pn.widgets.Button(label="Send to Poles")
    to_strips = pn.widgets.Button(label="Send to Strips")
    clear = pn.widgets.Button(label="Clear selection")
    sent = pn.pane.Markdown("")

    def send_to_poles(_event=None) -> None:
        frame = selected_frame()
        orbits = sorted({int(value) for value in frame.get("orbit", [])})
        state.poles_orbits = orbits
        sent.object = (
            f"Poles tab: orbits {', '.join(str(value) for value in orbits) or 'none'}"
        )

    def send_to_strips(_event=None) -> None:
        frame = selected_frame()
        names = pd.Series(frame.get("lat_band", pd.Series(dtype=object))).dropna()
        band = str(names.mode().iloc[0]) if not names.empty else "all"
        state.strip_lat_band = band if band in LAT_BAND_NAMES else "all"
        if state.strip_band == "all" and "half" in frame.columns and not frame.empty:
            halves = set(frame["half"].astype(str))
            if len(halves) == 1:
                state.strip_band = halves.pop()
        sent.object = f"Strips tab: latitude band **{state.strip_lat_band}**"

    def clear_selection(_event=None) -> None:
        state.selection = []
        sent.object = ""

    to_poles.on_click(send_to_poles)
    to_strips.on_click(send_to_strips)
    clear.on_click(clear_selection)

    return pn.Column(
        pn.pane.Markdown(
            "Box-select on the map to choose frames; the points carry their "
            "metadata in the hover below "
            f"{HOVER_LIMIT:,} rows."
        ),
        map_pane,
        pn.pane.Markdown("### Coverage"),
        coverage_pane,
        pn.pane.Markdown("### Selection"),
        caption,
        pn.Row(download, to_poles, to_strips, clear, sent),
        tabulator,
        sizing_mode="stretch_width",
    )
