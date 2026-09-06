"""The built front end, served at ``/`` by :mod:`jiram_catalog.api.app`.

``dist/`` is written by ``npm run build`` in ``frontend/`` and is not
tracked except for its ``.gitkeep``; the backend answers 503 with a hint
when the bundle is missing, so a source checkout still serves the API.
"""

from __future__ import annotations

from pathlib import Path

#: Directory the Vite build writes into.
DIST = Path(__file__).resolve().parent / "dist"


def dist_dir() -> Path:
    """Absolute path of the built bundle directory (may not exist)."""
    return DIST


def index_html() -> Path:
    """The bundle entry point (may not exist)."""
    return DIST / "index.html"
