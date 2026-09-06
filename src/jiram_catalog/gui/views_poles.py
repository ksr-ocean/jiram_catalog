"""The Poles tab: a viewer for the region stacks the command line writes.

A frame stack is 2 GB, so nothing here loads one.  The dataset is opened
lazily, one time step is read when the player moves, and the display
stretch comes from a strided subsample of a few steps.  The image is
drawn on its own kilometre grid with the coordinate arrays the stack
carries -- ``hv.Image((x_km, y_km, array))`` puts row 0 at the smallest
``y``, which is the stack's own convention, so no array is ever flipped.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

import holoviews as hv
import holoviews.operation.datashader as hd
import numpy as np
import panel as pn
import param
import xarray as xr

from ..export_goflow import export_stack
from ..movie import write_movie
from ..pds import mirror_root
from . import extensions
from . import data as data_module
from .state import CatalogState

#: Colour maps offered for the image; the first is the movie's default.
COLOUR_MAPS: tuple[str, ...] = ("gray", "viridis", "magma", "inferno", "cividis", "bone")

#: Graticule spacing, degrees.
DLAT = 2.0
DLON = 30.0

#: Per-time coordinates the metadata pane shows, in order.
META_COORDS: tuple[str, ...] = ("product_id", "seq_id", "orbit", "n_frames")


class _Viewer(param.Parameterized):
    """Everything the image depends on, in one object the streams watch."""

    path = param.String(default="")
    index = param.Integer(default=0, bounds=(0, 0))
    percentiles = param.Range(default=(1.0, 99.0), bounds=(0.0, 100.0))
    cmap = param.Selector(default=COLOUR_MAPS[0], objects=list(COLOUR_MAPS))
    graticule = param.Boolean(default=True)
    emission = param.Boolean(default=False)
    clim = param.Tuple(default=(0.0, 1.0), length=2)


def _bounds(dataset: xr.Dataset) -> tuple[np.ndarray, np.ndarray]:
    """The stack's kilometre axes as plain arrays."""
    x = np.asarray(dataset["x_km"].values, dtype=np.float64)
    y = np.asarray(dataset["y_km"].values, dtype=np.float64)
    return x, y


def _plane(dataset: xr.Dataset, name: str, index: int) -> np.ndarray:
    """One time step of one variable, masked to the valid pixels."""
    variable = dataset[name]
    plane = variable.isel(time=index) if "time" in variable.dims else variable
    values = np.asarray(plane.values, dtype=np.float32)
    if "valid" in dataset:
        valid = dataset["valid"]
        mask = valid.isel(time=index) if "time" in valid.dims else valid
        values = np.where(np.asarray(mask.values, dtype=bool), values, np.nan)
    return values


def _empty_image(x: np.ndarray, y: np.ndarray, label: str) -> hv.Image:
    """A two-by-two NaN image on the same extent: an invisible layer."""
    xs = np.array([x[0], x[-1]], dtype=np.float64)
    ys = np.array([y[0], y[-1]], dtype=np.float64)
    return hv.Image((xs, ys, np.full((2, 2), np.nan)), ["x_km", "y_km"], label)


def _coordinate_value(dataset: xr.Dataset, name: str, index: int) -> Any:
    if name not in dataset.coords:
        return None
    coordinate = dataset.coords[name]
    if "time" in coordinate.dims:
        return coordinate.isel(time=index).values.item()
    return coordinate.values.item() if coordinate.size == 1 else None


def _in_background(
    work: Callable[[], str], status: pn.pane.Markdown, busy: pn.indicators.LoadingSpinner
) -> None:
    """Run a long action off the server's event loop, then report."""

    def target() -> None:
        try:
            message = work()
        except Exception as exc:  # a failed export must not kill the session
            message = f"**failed:** {' '.join(str(exc).split())}"

        def finish() -> None:
            status.object = message
            busy.value = False
            busy.visible = False

        if pn.state.curdoc is not None:
            pn.state.execute(finish)
        else:
            finish()

    busy.value = True
    busy.visible = True
    status.object = "working..."
    threading.Thread(target=target, daemon=True).start()


def stack_options(mirror: str | Path | None, orbits: list[int] | None = None) -> dict[str, str]:
    """``{label: path}`` of the region stacks, optionally narrowed to orbits.

    A stack's file name carries the orbits it was built from
    (``M_orbits4_sequence.nc``), which is how a selection sent from the
    Catalog tab narrows this list; when nothing matches, the full list
    stands rather than an empty chooser.
    """
    paths = data_module.stack_paths(mirror)
    options = {f"{path.parent.name}/{path.name}": str(path) for path in paths}
    if orbits:
        wanted = {f"orbits{orbit}" for orbit in orbits}
        narrowed = {
            label: value
            for label, value in options.items()
            if any(token in label for token in wanted)
        }
        if narrowed:
            return narrowed
    return options


def _default_stack(options: dict[str, str]) -> str:
    """Prefer a per-sequence stack: it is the small one, and it plays."""
    for label, value in options.items():
        if "sequence" in label:
            return value
    return next(iter(options.values()), "")


def filters(state: CatalogState, mirror: str | Path | None = None) -> pn.Column:
    """The Poles tab's sidebar: which stack, and how it is drawn."""
    extensions()
    return pn.Column(
        pn.pane.Markdown("### Poles"),
        pn.pane.Markdown(
            "The stack chooser, player and overlays are in the tab itself; "
            "stacks are the NetCDF files `jiram-catalog region-stack` writes "
            f"under `{mirror_root(mirror) / 'regions'}`."
        ),
        sizing_mode="stretch_width",
    )


def build(state: CatalogState, mirror: str | Path | None = None) -> pn.Column:
    """Stack chooser, image viewer with overlays, and the export actions."""
    extensions()
    options = stack_options(mirror, state.poles_orbits)
    viewer = _Viewer()
    initial = state.current_stack or _default_stack(options)
    state.current_stack = initial
    viewer.path = initial

    chooser = pn.widgets.Select(
        label="stack", options=options, value=initial if initial in options.values() else None
    )
    typed = pn.widgets.TextInput(
        label="or a path", placeholder="a NetCDF stack anywhere on disk"
    )
    player = pn.widgets.Player(
        label="time", start=0, end=1, value=0, interval=400, loop_policy="loop"
    )
    stretch = pn.widgets.EditableRangeSlider(
        label="stretch percentiles", start=0.0, end=100.0, value=(1.0, 99.0), step=0.5
    )
    colours = pn.widgets.Select.from_param(viewer.param.cmap, name="colour map")
    graticule_toggle = pn.widgets.Checkbox.from_param(
        viewer.param.graticule, name=f"graticule ({DLAT:g} deg, {DLON:g} deg)"
    )
    emission_toggle = pn.widgets.Checkbox.from_param(
        viewer.param.emission, name="emission overlay"
    )
    metadata = pn.pane.Markdown("")
    status = pn.pane.Markdown("")
    busy = pn.indicators.LoadingSpinner(value=False, visible=False, size=20)

    # ------------------------------------------------------------ current stack
    def dataset() -> xr.Dataset | None:
        if not viewer.path:
            return None
        try:
            return data_module.open_stack(viewer.path)
        except (OSError, ValueError) as exc:
            status.object = f"**cannot open** {viewer.path}: {exc}"
            return None

    def on_stack(*_events) -> None:
        stack = dataset()
        if stack is None:
            return
        steps = int(stack.sizes.get("time", 1))
        player.end = max(steps - 1, 1)
        if player.value > steps - 1:
            player.value = 0
        viewer.param.index.bounds = (0, max(steps - 1, 0))
        viewer.index = min(viewer.index, steps - 1)
        on_stretch()
        on_index()

    def on_stretch(*_events) -> None:
        stack = dataset()
        if stack is None:
            return
        viewer.percentiles = tuple(float(value) for value in stretch.value)
        viewer.clim = data_module.stack_stretch(
            stack, viewer.percentiles, key=viewer.path
        )

    def on_index(*_events) -> None:
        stack = dataset()
        if stack is None:
            metadata.object = "_no stack_"
            return
        index = int(viewer.index)
        rows = [
            f"**{Path(viewer.path).name}** &nbsp; level `{stack.attrs.get('level', '?')}`"
            f" &nbsp; band `{stack.attrs.get('band', '?')}`"
            f" &nbsp; region `{stack.attrs.get('region', '?')}`"
            f" &nbsp; {stack.attrs.get('km_per_px', float('nan')):g} km/px",
            f"step **{index + 1}** of {int(stack.sizes.get('time', 1))}"
            f" &nbsp; time `{np.datetime_as_string(np.asarray(stack['time'].isel(time=index).values), unit='s')}`",
        ]
        details = []
        for name in META_COORDS:
            value = _coordinate_value(stack, name, index)
            if value is not None:
                details.append(f"{name} `{value}`")
        if details:
            rows.append(" &nbsp; ".join(details))
        rows.append(f"stretch `{viewer.clim[0]:.4g}` to `{viewer.clim[1]:.4g}`")
        metadata.object = "\n\n".join(rows)

    def on_choice(event) -> None:
        if event.new:
            state.current_stack = str(event.new)

    def on_typed(event) -> None:
        text = str(event.new).strip()
        if text:
            state.current_stack = text

    def on_state_stack(*_events) -> None:
        if state.current_stack and state.current_stack != viewer.path:
            viewer.path = state.current_stack
            if state.current_stack in options.values():
                chooser.value = state.current_stack
            on_stack()

    def on_orbits(*_events) -> None:
        options.clear()
        options.update(stack_options(mirror, state.poles_orbits))
        chooser.options = options
        status.object = (
            "stack list narrowed to orbits "
            f"{', '.join(str(value) for value in state.poles_orbits)}"
            if state.poles_orbits
            else ""
        )

    chooser.param.watch(on_choice, "value")
    typed.param.watch(on_typed, "value")
    state.param.watch(on_state_stack, "current_stack")
    state.param.watch(on_orbits, "poles_orbits")
    player.param.watch(lambda event: setattr(viewer, "index", int(event.new)), "value")
    stretch.param.watch(on_stretch, "value")
    viewer.param.watch(on_index, ["index", "clim"])
    on_stack()

    # ------------------------------------------------------------------ layers
    def image(path, index, **_ignored):
        stack = dataset()
        if stack is None:
            return hv.Image(([0.0, 1.0], [0.0, 1.0], np.full((2, 2), np.nan)), ["x_km", "y_km"])
        x, y = _bounds(stack)
        values = _plane(stack, "image", int(index))
        return hv.Image((x, y, values), ["x_km", "y_km"], "radiance")

    def emission(path, index, emission, **_ignored):
        stack = dataset()
        if stack is None:
            return hv.Image(([0.0, 1.0], [0.0, 1.0], np.full((2, 2), np.nan)), ["x_km", "y_km"])
        x, y = _bounds(stack)
        if not emission or "emission" not in stack:
            return _empty_image(x, y, "emission")
        return hv.Image((x, y, _plane(stack, "emission", int(index))), ["x_km", "y_km"], "emission")

    def graticule(path, graticule, **_ignored):
        stack = dataset()
        if stack is None or not graticule or "lat" not in stack.coords:
            return hv.Path([])
        x, y = _bounds(stack)
        paths = data_module.graticule_paths(
            stack["lat"].values, stack["lon_east"].values, x, y, dlat=DLAT, dlon=DLON
        )
        return hv.Path(paths)

    image_stream = hv.streams.Params(viewer, ["path", "index"])
    emission_stream = hv.streams.Params(viewer, ["path", "index", "emission"])
    graticule_stream = hv.streams.Params(viewer, ["path", "graticule"])

    base = hd.regrid(hv.DynamicMap(image, streams=[image_stream])).apply.opts(
        cmap=viewer.param.cmap,
        clim=viewer.param.clim,
        colorbar=True,
        responsive=True,
        height=560,
        bgcolor="#101014",
        xlabel="x (km)",
        ylabel="y (km)",
    )
    over = hd.regrid(hv.DynamicMap(emission, streams=[emission_stream])).opts(
        cmap="plasma", alpha=0.4, colorbar=False
    )
    lines = hv.DynamicMap(graticule, streams=[graticule_stream]).opts(
        color="#f2f2f2", alpha=0.45, line_width=0.7
    )
    view = pn.pane.HoloViews(base * over * lines, sizing_mode="stretch_width")

    # ----------------------------------------------------------------- actions
    default_dir = data_module.export_dir(mirror)
    stem = Path(initial).stem or "stack"
    movie_path = pn.widgets.TextInput(
        label="movie file", value=str(default_dir / f"{stem}.mp4")
    )
    movie_fps = pn.widgets.FloatInput(label="fps", value=4.0, start=0.5, end=30.0, width=80)
    movie_button = pn.widgets.Button(label="Render movie", color="primary")
    goflow_dir = pn.widgets.TextInput(
        label="goflow dataset", value=str(default_dir / f"goflow_{stem}")
    )
    goflow_button = pn.widgets.Button(label="Export goflow triples")
    png_button = pn.widgets.Button(label="Save PNG")

    def render_movie(_event=None) -> None:
        stack, path = dataset(), movie_path.value
        if stack is None:
            return

        def work() -> str:
            summary = write_movie(
                stack,
                path,
                fps=float(movie_fps.value),
                percentiles=viewer.percentiles,
                cmap=viewer.cmap,
            )
            return (
                f"wrote **{summary['path']}** -- {summary['frames']} frames, "
                f"{summary['width']}x{summary['height']}, "
                f"{summary['bytes'] / 1e6:.1f} MB"
            )

        _in_background(work, status, busy)

    def export_goflow(_event=None) -> None:
        stack, out = dataset(), goflow_dir.value
        if stack is None:
            return

        def work() -> str:
            manifest = export_stack(stack, out, source=viewer.path)
            return (
                f"wrote **{out}** -- {manifest['n_realizations']} realization(s)"
            )

        _in_background(work, status, busy)

    def save_png(_event=None) -> None:
        stack = dataset()
        if stack is None:
            return

        def work() -> str:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            index = int(viewer.index)
            x, y = _bounds(stack)
            values = _plane(stack, "image", index)
            target = default_dir / f"{Path(viewer.path).stem}_t{index:03d}.png"
            figure = plt.figure(figsize=(9.0, 9.0 * values.shape[0] / values.shape[1]))
            axes = figure.add_axes((0.0, 0.0, 1.0, 1.0))
            axes.set_axis_off()
            axes.imshow(
                np.ma.masked_invalid(values),
                origin="lower",
                extent=(x[0], x[-1], y[0], y[-1]),
                cmap=viewer.cmap,
                vmin=viewer.clim[0],
                vmax=viewer.clim[1],
                interpolation="nearest",
            )
            figure.savefig(target, dpi=150, facecolor="#101014")
            plt.close(figure)
            return f"wrote **{target}**"

        _in_background(work, status, busy)

    movie_button.on_click(render_movie)
    goflow_button.on_click(export_goflow)
    png_button.on_click(save_png)

    return pn.Column(
        pn.Row(chooser, typed, sizing_mode="stretch_width"),
        pn.Row(player, stretch, colours, graticule_toggle, emission_toggle),
        metadata,
        view,
        pn.pane.Markdown("### Actions"),
        pn.Row(movie_path, movie_fps, movie_button),
        pn.Row(goflow_dir, goflow_button, png_button),
        pn.Row(busy, status),
        sizing_mode="stretch_width",
    )
