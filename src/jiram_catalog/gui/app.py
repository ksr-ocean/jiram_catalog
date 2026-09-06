"""The application: three tabs, one state, one template.

``build`` is the whole app as a plain ``pn.Tabs`` -- what the tests and
the gate exercise, and what a notebook would display.  ``page`` dresses
it in the template a browser gets, with the active tab's filters in the
sidebar; ``serve`` runs it.
"""

from __future__ import annotations

import io
from pathlib import Path

import panel as pn

from ..pds import mirror_root
from . import extensions
from . import data as data_module
from . import views_catalog, views_poles, views_strips
from .state import CatalogState

TITLE = "JIRAM catalog"

#: Tab order; the same order ``build`` returns and ``page``'s sidebar follows.
TAB_NAMES: tuple[str, ...] = ("Catalog", "Poles", "Strips")


def header(mirror: str | Path | None = None) -> pn.pane.Markdown:
    """Mirror path and row counts, for the top of the page."""
    root = mirror_root(mirror)
    try:
        frames = len(data_module.catalog_table(root))
    except Exception:  # a mirror without a geometry table still gets a page
        frames = 0
    try:
        strips = len(data_module.strips_table(root))
    except Exception:
        strips = 0
    stacks = len(data_module.stack_paths(root))
    return pn.pane.Markdown(
        f"mirror `{root}` &nbsp;|&nbsp; **{frames:,}** frames on the planet "
        f"&nbsp;|&nbsp; **{strips:,}** strips &nbsp;|&nbsp; **{stacks}** region stacks",
        sizing_mode="stretch_width",
    )


def build(mirror: str | Path | None = None, state: CatalogState | None = None) -> pn.Tabs:
    """The three tabs over one shared :class:`CatalogState`."""
    extensions()
    root = mirror_root(mirror)
    shared = state if state is not None else CatalogState()
    return pn.Tabs(
        (TAB_NAMES[0], views_catalog.build(shared, root)),
        (TAB_NAMES[1], views_poles.build(shared, root)),
        (TAB_NAMES[2], views_strips.build(shared, root)),
        dynamic=False,
        sizing_mode="stretch_width",
    )


def session_controls(state: CatalogState) -> pn.Column:
    """Save the filters and selection as JSON, and load them back."""
    upload = pn.widgets.FileInput(accept=".json", label="load session")
    note = pn.pane.Markdown("")

    def on_upload(event) -> None:
        if not event.new:
            return
        try:
            state.from_json(event.new.decode("utf-8"))
            note.object = "session loaded"
        except (ValueError, UnicodeDecodeError) as exc:
            note.object = f"**not a session:** {exc}"

    upload.param.watch(on_upload, "value")
    download = pn.widgets.FileDownload(
        callback=lambda: io.StringIO(state.to_json()),
        filename="jiram_session.json",
        label="Save session",
    )
    return pn.Column(
        pn.pane.Markdown("### Session"), download, upload, note, sizing_mode="stretch_width"
    )


def page(mirror: str | Path | None = None) -> pn.template.FastListTemplate:
    """One browser session: the tabs, with the active tab's filters aside."""
    extensions()
    root = mirror_root(mirror)
    state = CatalogState()
    tabs = build(root, state)
    sidebars = [
        views_catalog.filters(state, root),
        views_poles.filters(state, root),
        views_strips.filters(state, root),
    ]

    def sidebar(active: int):
        return sidebars[int(active) % len(sidebars)]

    template = pn.template.FastListTemplate(
        title=TITLE,
        sidebar=[pn.panel(pn.bind(sidebar, tabs.param.active)), session_controls(state)],
        main=[header(root), tabs],
        sidebar_width=330,
        theme="dark",
    )
    return template


def serve(
    mirror: str | Path | None = None,
    port: int = 5006,
    address: str = "127.0.0.1",
    show: bool = False,
    **kwargs,
):
    """Serve the app with Bokeh; blocks until interrupted.

    ``websocket_origin`` is opened up because the browser reaches a
    tunnelled server as ``localhost:<port>`` while the server knows
    itself as ``127.0.0.1:<port>`` or as the compute node's name, and
    Bokeh would refuse the socket over that mismatch.
    """
    extensions()
    root = mirror_root(mirror)
    kwargs.setdefault("websocket_origin", "*")
    return pn.serve(
        lambda: page(root),
        port=int(port),
        address=address,
        show=bool(show),
        title=TITLE,
        **kwargs,
    )
