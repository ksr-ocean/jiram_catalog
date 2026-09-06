"""Panel web application for the JIRAM catalog.

Three tabs -- Catalog, Poles, Strips -- over the products the command
line already writes (see ``docs/gui_design.md``).  Nothing here computes
science; the GUI is a view and a selector.  Import is deliberately light:
Panel and HoloViews are pulled in by :func:`extensions`, which every
``build`` calls before it makes a widget.
"""

from __future__ import annotations

__all__ = ["extensions"]

_LOADED = False


def extensions() -> None:
    """Load the Bokeh backend and the Tabulator widget model once."""
    global _LOADED
    if _LOADED:
        return
    import holoviews as hv
    import panel as pn

    pn.extension("tabulator", sizing_mode="stretch_width", loading_indicator=True)
    hv.extension("bokeh")
    _LOADED = True
