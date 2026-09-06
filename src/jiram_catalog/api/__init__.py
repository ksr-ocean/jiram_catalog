"""The GUI v2 HTTP service: a thin JSON/Arrow/PNG skin over the modules.

Nothing here computes anything the command line cannot; the package
exists so that a browser can ask the same questions over HTTP.  The
loaders, caches and transforms are the GUI v1 ones
(:mod:`jiram_catalog.gui.data`), which is what keeps the two front ends
from disagreeing about what a filter means.
"""

from __future__ import annotations

__all__ = ["create_app"]


def create_app(mirror=None):
    """Import shim for :func:`jiram_catalog.api.app.create_app`."""
    from .app import create_app as _create_app

    return _create_app(mirror)
