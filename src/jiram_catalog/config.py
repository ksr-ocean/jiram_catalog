"""Central resolution of the paths that used to be hardcoded per-user.

Two roots are configurable: the local mirror of the archive
(``JIRAM_MIRROR``) and the published perijove-4 ground-truth data used by
the gates (``JIRAM_PAPER_DATA``). For each, the precedence is:

    explicit argument > environment variable > TOML config file > default

The TOML file is read from ``$JIRAM_CONFIG`` if set, else
``~/.config/jiram_catalog.toml``; a missing file resolves to no overrides.
Its recognised top-level keys are ``mirror`` and ``paper_data``.

Every resolver function does its lookup when called, not at import time,
so callers (including tests, via ``monkeypatch.setenv``) can change the
environment and see it reflected in the next call.
"""

from __future__ import annotations

import os
from pathlib import Path

MIRROR_ENV = "JIRAM_MIRROR"
PAPER_ENV = "JIRAM_PAPER_DATA"
CONFIG_ENV = "JIRAM_CONFIG"

#: the group's shared copy
DEFAULT_MIRROR = Path("/expanse/lustre/projects/cla119/kaushiks/jiram_mirror")
#: published PJ4 ground truth, read-only
DEFAULT_PAPER_DATA = Path("/expanse/lustre/projects/cla119/kaushiks/JIRAM")

DEFAULT_CONFIG_FILE = Path("~/.config/jiram_catalog.toml")


def load_config_file() -> dict:
    """Load the optional TOML config file, or ``{}`` if there isn't one.

    ``tomllib`` is only in the standard library from Python 3.11; on an
    older interpreter (e.g. a script run under a foreign ``module load``
    environment) this degrades to "no file support" rather than failing
    to import, since the mirror/paper-data *defaults* must keep working
    there regardless.
    """
    configured = os.environ.get(CONFIG_ENV)
    path = Path(configured).expanduser() if configured else DEFAULT_CONFIG_FILE.expanduser()
    try:
        import tomllib
    except ModuleNotFoundError:
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (FileNotFoundError, OSError):
        return {}


def _resolve(explicit: str | Path | None, env_name: str, file_key: str, default: Path) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    from_env = os.environ.get(env_name)
    if from_env:
        return Path(from_env).expanduser()
    from_file = load_config_file().get(file_key)
    if from_file:
        return Path(from_file).expanduser()
    return default


def _source(env_name: str, file_key: str) -> str:
    if os.environ.get(env_name):
        return "env"
    if load_config_file().get(file_key):
        return "file"
    return "default"


def mirror_root(explicit: str | Path | None = None) -> Path:
    """Resolve the mirror root: explicit > ``$JIRAM_MIRROR`` > file > default."""
    return _resolve(explicit, MIRROR_ENV, "mirror", DEFAULT_MIRROR)


def paper_data_root(explicit: str | Path | None = None) -> Path:
    """Resolve the paper-data root, same precedence as :func:`mirror_root`."""
    return _resolve(explicit, PAPER_ENV, "paper_data", DEFAULT_PAPER_DATA)


def describe() -> str:
    """One line per setting: its resolved value and where it came from.

    The source reported here (``env``/``file``/``default``) is always for
    the no-explicit-argument resolution, since that is what ``jiram-catalog
    config`` and ``python -m jiram_catalog.config_cmd`` report; a caller
    that passes ``--mirror`` overrides it themselves and that is visible in
    the printed value, not the source label.
    """
    lines = []
    for label, resolver, env_name, file_key in (
        ("mirror", mirror_root, MIRROR_ENV, "mirror"),
        ("paper_data", paper_data_root, PAPER_ENV, "paper_data"),
    ):
        lines.append(f"{label}: {resolver()} (source: {_source(env_name, file_key)})")
    return "\n".join(lines)
