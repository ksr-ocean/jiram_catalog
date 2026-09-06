# Spec: configurable paths and shared-repository hygiene

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog
(`uv` project; package `jiram_catalog`). Read first: `src/jiram_catalog/pds.py`
(`DEFAULT_MIRROR` and how other modules import or repeat it),
`grep -rn "kaushiks\|DEFAULT_MIRROR\|JIRAM_MIRROR" src tests scripts configs`,
`CONTRIBUTING.md`, `AGENTS.md`.

## Goal
No absolute path that belongs to one user remains in code or tests.
Every location is resolved through one small configuration module with
environment-variable overrides and documented defaults.

## Files in scope
- `src/jiram_catalog/config.py`  (create)
- every module under `src/jiram_catalog/` that defines or hardcodes a
  mirror or data path (modify only those lines and their imports)
- every file under `tests/` and `scripts/` that hardcodes a path
  (modify only those lines; gates keep their assertions byte-identical
  otherwise)
- `docs/configuration.md` (create: the precedence rules, the two environment variables, the optional TOML file with an example)
READ-ONLY: `README.md` (a documentation pass owns it), the rest of `docs/`, `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md`,
`pyproject.toml`, `uv.lock`, `configs/regions.yaml`. Do not change any
behaviour, tolerance, or assertion; this is a path-plumbing change.

## Design (normative)
```python
# config.py
MIRROR_ENV = "JIRAM_MIRROR"; PAPER_ENV = "JIRAM_PAPER_DATA"; CONFIG_ENV = "JIRAM_CONFIG"
DEFAULT_MIRROR = "/expanse/lustre/projects/cla119/kaushiks/jiram_mirror"   # the group's shared copy
DEFAULT_PAPER_DATA = "/expanse/lustre/projects/cla119/kaushiks/JIRAM"      # published PJ4 ground truth, read-only
def mirror_root(explicit: str | Path | None = None) -> Path   # explicit > env > config file > default
def paper_data_root(explicit=None) -> Path                       # same precedence
def load_config_file() -> dict   # TOML at $JIRAM_CONFIG, else ~/.config/jiram_catalog.toml, else {}; keys: mirror, paper_data
def describe() -> str            # one line per setting with its source (explicit/env/file/default), used by `jiram-catalog config`
```
`tomllib` from the standard library. Modules resolve paths by calling
these functions at use time (not at import time), so tests can set the
environment. The CLI's `--mirror` option keeps its precedence over the
environment. Add a `config` subcommand in a new module
`src/jiram_catalog/config_cmd.py` exposing `add_subparser`/`run` that
prints `describe()`; the coordinator wires it.

Gates: replace hardcoded strings with `config.mirror_root()` /
`config.paper_data_root()`; the skip logic (`JIRAM_SKIP_GATES`) is
unchanged. Scripts likewise.

## Validation that defines done
```
grep -rn "kaushiks" src tests scripts | grep -v "config.py"     # must print nothing (the gui package and gui_cmd.py are in scope too)
JIRAM_SKIP_GATES=1 uv run pytest -q
uv run pytest -q                                                 # gates unchanged in content, must still pass
JIRAM_MIRROR=/nonexistent uv run python -m jiram_catalog.config_cmd    # must show the env source and not crash
```

## Report (at most 25 lines)
Files touched (list), the grep result, both pytest tails, judgment
calls, ambiguities with the choice made.
