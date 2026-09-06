"""The Strips tab: the per-pass strip library, one strip, and its statistics.

The library is small enough to hold in a table and a map; the statistics
are not, so they are computed once per strip by ``stats2d.strip_statistics``
and cached under ``<mirror>/gui_cache``.  The viewer shows the strip on
its own kilometre grid with the graticule and the local-time contours the
projection implies, and reads latitude, longitude, local time and
emission back under the cursor.
"""

from __future__ import annotations

import io
from pathlib import Path

import holoviews as hv
import holoviews.operation.datashader as hd
import hvplot.pandas  # noqa: F401  (registers the .hvplot accessor)
import numpy as np
import pandas as pd
import panel as pn
import param
import xarray as xr

from . import extensions
from . import data as data_module
from .state import CatalogState
from .views_poles import COLOUR_MAPS, DLAT, DLON

#: State parameters the Strips tab's filtered set depends on.
FILTER_PARAMETERS: tuple[str, ...] = (
    "strip_band",
    "strip_lat_band",
    "strip_date_range",
    "strip_resolution_max_km",
    "strip_valid_min",
    "strip_dayside_min",
)

#: Columns of the library table, in order.
TABLE_COLUMNS: tuple[str, ...] = (
    "strip_id",
    "orbit",
    "band",
    "n_frames",
    "time_mid",
    "center_lat",
    "center_lon_east",
    "km_per_px",
    "valid_frac",
    "dayside_frac",
    "median_emission",
    "lat_min",
    "lat_max",
    "rows",
    "cols",
)

#: Local-time contours, hours.
DLOCAL_TIME = 2.0


class _Selected(param.Parameterized):
    """The filtered strip index, recomputed once per filter change."""

    frame = param.Parameter(default=None)


def filters(state: CatalogState, mirror: str | Path | None = None) -> pn.Column:
    """The Strips tab's sidebar widgets."""
    extensions()
    return pn.Column(
        pn.pane.Markdown("### Strip filters", margin=(0, 0, -10, 0)),
        pn.widgets.RadioButtonGroup.from_param(state.param.strip_band, name="band"),
        pn.widgets.Select.from_param(state.param.strip_lat_band, name="latitude band"),
        pn.widgets.DatetimeRangeInput.from_param(state.param.strip_date_range, name="epoch"),
        pn.widgets.EditableFloatSlider.from_param(
            state.param.strip_resolution_max_km, name="km/px <=", step=5.0
        ),
        pn.widgets.EditableFloatSlider.from_param(
            state.param.strip_valid_min, name="valid fraction >=", step=0.05
        ),
        pn.widgets.EditableFloatSlider.from_param(
            state.param.strip_dayside_min, name="dayside fraction >=", step=0.05
        ),
        sizing_mode="stretch_width",
    )


def _strip_stretch(dataset: xr.Dataset, key: str) -> tuple[float, float]:
    return data_module.stack_stretch(dataset, (1.0, 99.0), key=f"strip::{key}")


def _masked(dataset: xr.Dataset, name: str) -> np.ndarray:
    values = np.asarray(dataset[name].values, dtype=np.float32)
    if "valid" in dataset:
        values = np.where(np.asarray(dataset["valid"].values, dtype=bool), values, np.nan)
    return values


def build(state: CatalogState, mirror: str | Path | None = None) -> pn.Column:
    """Library table and map, the current strip's viewer, and its statistics."""
    extensions()
    library = data_module.strips_table(mirror)
    selected = _Selected()

    def refresh(*_events) -> None:
        selected.frame = data_module.apply_strip_filters(library, state)

    refresh()
    state.param.watch(refresh, list(FILTER_PARAMETERS))

    if not state.current_strip and not selected.frame.empty:
        state.current_strip = str(selected.frame["strip_id"].iloc[0])

    columns = [name for name in TABLE_COLUMNS if name in library.columns]
    tabulator = pn.widgets.Tabulator(
        selected.frame[columns],
        pagination="remote",
        page_size=12,
        disabled=True,
        show_index=False,
        selectable=1,
        sizing_mode="stretch_width",
        height=380,
    )
    caption = pn.pane.Markdown("")

    def on_frame(*_events) -> None:
        frame = selected.frame
        tabulator.value = frame[columns]
        caption.object = f"**{len(frame):,}** of {len(library):,} strips"

    on_frame()
    selected.param.watch(on_frame, "frame")

    def choose(event) -> None:
        frame = tabulator.value
        row = getattr(event, "row", None)
        if row is None or frame is None or not (0 <= int(row) < len(frame)):
            return
        state.current_strip = str(frame["strip_id"].iloc[int(row)])

    def choose_selection(event) -> None:
        rows = list(event.new or [])
        frame = tabulator.value
        if rows and frame is not None and 0 <= rows[0] < len(frame):
            state.current_strip = str(frame["strip_id"].iloc[rows[0]])

    tabulator.on_click(choose)
    tabulator.param.watch(choose_selection, "selection")

    # -------------------------------------------------------------------- map
    def centres(frame):
        if frame is None or frame.empty:
            return hv.Points([], ["center_lon_east", "center_lat"]).opts(
                xlim=(0, 360), ylim=(-90, 90)
            )
        hover = [
            name
            for name in ("strip_id", "orbit", "band", "km_per_px", "lat_min", "lat_max", "year")
            if name in frame.columns
        ]
        return frame.hvplot.points(
            "center_lon_east",
            "center_lat",
            c="year",
            cmap="viridis",
            hover_cols=hover,
            size=60,
            colorbar=True,
            xlim=(0, 360),
            ylim=(-90, 90),
            xlabel="east longitude (deg)",
            ylabel="latitude (deg)",
            title="strip centres, coloured by year",
            responsive=True,
            height=330,
        )

    map_pane = pn.pane.HoloViews(
        hv.DynamicMap(centres, streams=[hv.streams.Params(selected, ["frame"])]),
        sizing_mode="stretch_width",
    )

    # ----------------------------------------------------------------- viewer
    def strip_dataset() -> xr.Dataset | None:
        if not state.current_strip:
            return None
        try:
            return data_module.open_strip(mirror, state.current_strip)
        except (OSError, ValueError, KeyError):
            return None

    def viewer(current_strip, cmap):
        dataset = strip_dataset()
        if dataset is None:
            return hv.Image(([0.0, 1.0], [0.0, 1.0], np.full((2, 2), np.nan)), ["x_km", "y_km"])
        x = np.asarray(dataset["x_km"].values, dtype=np.float64)
        y = np.asarray(dataset["y_km"].values, dtype=np.float64)
        low, high = _strip_stretch(dataset, current_strip)
        image = hd.regrid(
            hv.Image((x, y, _masked(dataset, "image")), ["x_km", "y_km"], "radiance"),
            dynamic=False,
        ).opts(
            cmap=cmap,
            clim=(low, high),
            colorbar=True,
            responsive=True,
            height=480,
            bgcolor="#101014",
            xlabel="x (km)",
            ylabel="y (km)",
            title=str(current_strip),
            clipping_colors={"NaN": "rgba(0, 0, 0, 0)"},
        )
        graticule = hv.Path(
            data_module.graticule_for(dataset, current_strip, dlat=DLAT, dlon=DLON)
        ).opts(color="#f2f2f2", alpha=0.45, line_width=0.7)
        clock = hv.Path([])
        if "local_time_h" in dataset.coords:
            clock = hv.Path(
                data_module.contour_paths(
                    dataset["local_time_h"].values,
                    x,
                    y,
                    np.arange(0.0, 24.0, DLOCAL_TIME),
                    period=24.0,
                )
            ).opts(color="#ffd166", alpha=0.7, line_width=1.0, line_dash="dashed")
        return image * graticule * clock

    colours = pn.widgets.Select(label="colour map", options=list(COLOUR_MAPS), value="gray")
    image_dmap = hv.DynamicMap(
        pn.bind(viewer, current_strip=state.param.current_strip, cmap=colours)
    )
    pointer = hv.streams.PointerXY(x=np.nan, y=np.nan, source=image_dmap)

    def readout(x, y) -> str:
        dataset = strip_dataset()
        if dataset is None or x is None or y is None or not np.isfinite([x, y]).all():
            return "_move the cursor over the strip_"
        xs = np.asarray(dataset["x_km"].values, dtype=np.float64)
        ys = np.asarray(dataset["y_km"].values, dtype=np.float64)
        column = int(np.clip(np.searchsorted(xs, x), 0, xs.size - 1))
        row = int(np.clip(np.searchsorted(ys, y), 0, ys.size - 1))
        pieces = [f"x `{x:,.0f}` km", f"y `{y:,.0f}` km"]
        for name, unit, fmt in (
            ("lat", "deg", "{:.3f}"),
            ("lon_east", "deg E", "{:.3f}"),
            ("local_time_h", "h", "{:.2f}"),
            ("emission", "deg", "{:.2f}"),
            ("image", "", "{:.4g}"),
        ):
            if name in dataset:
                value = float(np.asarray(dataset[name][row, column].values))
                pieces.append(f"{name} `{fmt.format(value)}`{(' ' + unit) if unit else ''}")
        return " &nbsp;|&nbsp; ".join(pieces)

    cursor = pn.pane.Markdown(
        pn.bind(readout, x=pointer.param.x, y=pointer.param.y), height=30
    )

    # ------------------------------------------------------------- statistics
    def statistics(current_strip):
        if not current_strip:
            return pn.pane.Markdown("_no strip selected_")
        try:
            stats = data_module.strip_stats(mirror, current_strip)
        except Exception as exc:  # a strip the statistics cannot digest
            return pn.pane.Markdown(f"**statistics failed:** {' '.join(str(exc).split())}")

        shells = pd.DataFrame(
            {"k": np.asarray(stats["k"]), "E": np.asarray(stats["E"])}
        )
        shells = shells.loc[(shells["k"] > 0) & np.isfinite(shells["E"]) & (shells["E"] > 0)]
        shells["wavelength_km"] = 2.0 * np.pi / shells["k"] / 1000.0
        isotropic = shells.hvplot.line(
            x="k",
            y="E",
            hover_cols=["wavelength_km"],
            logx=True,
            logy=True,
            xlabel="k (rad m-1)   [2 pi / k in km in the hover]",
            ylabel="E(k)",
            title="isotropic spectrum",
            responsive=True,
            height=300,
        )

        lines = []
        for name, axis in (("P_x", "kx"), ("P_y", "ky")):
            frame = pd.DataFrame(
                {"k": np.asarray(stats[axis]), "P": np.asarray(stats[name])}
            )
            frame = frame.loc[(frame["k"] > 0) & np.isfinite(frame["P"]) & (frame["P"] > 0)]
            if frame.empty:
                continue
            lines.append(
                frame.hvplot.line(
                    x="k",
                    y="P",
                    logx=True,
                    logy=True,
                    label=f"along {name[-1]}",
                    xlabel="k (rad m-1)",
                    ylabel="P(k)",
                    title="one-dimensional spectra",
                    responsive=True,
                    height=300,
                )
            )
        oned = hv.Overlay(lines).opts(legend_position="top_right") if lines else hv.Curve([])

        radius = np.asarray(stats["r"]) / 1000.0
        second = pd.DataFrame({"r_km": radius, "S2": np.asarray(stats["S2"])})
        second = second.loc[np.isfinite(second["S2"]) & (second["S2"] > 0)]
        third = pd.DataFrame({"r_km": radius, "S3": np.asarray(stats["S3"])})
        third = third.loc[np.isfinite(third["S3"])]
        structure = (
            second.hvplot.line(
                x="r_km",
                y="S2",
                logx=True,
                logy=True,
                xlabel="r (km)",
                ylabel="S2",
                title="second order",
                responsive=True,
                height=300,
            )
            + third.hvplot.line(
                x="r_km",
                y="S3",
                logx=True,
                xlabel="r (km)",
                ylabel="S3 (signed)",
                title="third order",
                responsive=True,
                height=300,
            )
        ).cols(2).opts(shared_axes=False)

        summary = ", ".join(
            f"{name} `{stats.attrs[name]}`"
            for name in ("km_per_px", "band", "orbit", "valid_frac", "rows", "cols")
            if name in stats.attrs
        )
        return pn.Column(
            pn.pane.Markdown(f"**{current_strip}** -- {summary}"),
            pn.Row(pn.pane.HoloViews(isotropic), pn.pane.HoloViews(oned)),
            pn.pane.HoloViews(structure),
            sizing_mode="stretch_width",
        )

    statistics_pane = pn.panel(pn.bind(statistics, current_strip=state.param.current_strip))

    # ---------------------------------------------------------------- export
    def csv() -> io.StringIO:
        frame = selected.frame
        return io.StringIO(
            (frame if frame is not None else pd.DataFrame()).to_csv(index=False)
        )

    download = pn.widgets.FileDownload(
        callback=csv,
        filename="jiram_strips.csv",
        color="primary",
        label="Export filtered list (CSV)",
    )

    return pn.Column(
        pn.Row(caption, download),
        pn.Row(
            pn.Column(tabulator, sizing_mode="stretch_width"),
            pn.Column(map_pane, sizing_mode="stretch_width"),
            sizing_mode="stretch_width",
        ),
        pn.pane.Markdown("### Strip viewer"),
        pn.Row(colours, cursor),
        pn.pane.HoloViews(image_dmap, sizing_mode="stretch_width"),
        pn.pane.Markdown("### Statistics"),
        statistics_pane,
        sizing_mode="stretch_width",
    )
