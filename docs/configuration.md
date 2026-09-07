# Configuring paths (`jiram_catalog.config`)

Two locations used to be hardcoded to one user's directories on the
cluster. Both are now resolved by `src/jiram_catalog/config.py` at the
point of use, with the same precedence for each:

```
explicit argument (e.g. --mirror)  >  environment variable  >  TOML config file  >  built-in default
```

"At the point of use" matters: nothing is cached at import time, so
setting an environment variable (or, in a test, `monkeypatch.setenv(...)`)
takes effect on the next call, not just the next process.

## The two settings

| setting      | environment variable | built-in default                                              |
|--------------|-----------------------|-----------------------------------------------------------------|
| mirror       | `JIRAM_MIRROR`        | `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror` (the group's shared mirror) |
| paper data   | `JIRAM_PAPER_DATA`    | `/expanse/lustre/projects/cla119/kaushiks/JIRAM` (published PJ4 ground truth, read-only) |

The defaults point at the project group's shared copies on the cluster,
so a group member who does nothing gets the previous behaviour
unchanged. Anyone working from a different mirror, or a different
checkout of the paper data, sets the corresponding environment variable
(see `CONTRIBUTING.md`).

`jiram-catalog`'s own `--mirror` option, where a subcommand has one,
still takes precedence over `JIRAM_MIRROR` -- it is the "explicit
argument" at the top of the precedence list above.

## The optional TOML file

For a setting you want to fix once rather than export in every shell,
`jiram_catalog.config` also reads a small TOML file:

- its location is `$JIRAM_CONFIG` if that is set, otherwise
  `~/.config/jiram_catalog.toml`;
- a missing file is not an error -- it simply contributes no overrides;
- it is consulted after the environment variable and before the
  built-in default, for both settings independently.

Recognised keys, both optional:

```toml
# ~/.config/jiram_catalog.toml
mirror = "/expanse/lustre/projects/cla119/myname/jiram_mirror"
paper_data = "/expanse/lustre/projects/cla119/myname/JIRAM"
```

`tomllib` (Python's standard-library TOML reader, 3.11+) is imported
lazily, only when the file actually needs to be read, and only after the
environment variable for that setting was found absent. This keeps the
built-in defaults working even where `tomllib` is unavailable -- for
instance the two `scripts/` helpers that deliberately run under a
foreign interpreter outside the `uv` project.

## Inspecting what will be used

```
uv run python -m jiram_catalog.config_cmd
```

prints one line per setting: its resolved value and which tier of the
precedence chain supplied it (`env`, `file`, or `default`). The same
`describe()` output is registered as a `jiram-catalog config` subcommand
(`src/jiram_catalog/config_cmd.py`, `add_subparser`/`run`); once the CLI
coordinator wires it into `cli.py`, `uv run jiram-catalog config` will do
the same thing.

## GUI execution

`JIRAM_ARROW_THREADS` controls the GUI's Arrow CPU and I/O pools. The default
is four threads, capped by CPU affinity; it is independent of BLAS/OpenMP
settings. NetCDF-backed GUI routes and background jobs share a process-local
lock because the installed netCDF C library does not support concurrent
access safely. Long image operations can queue other image requests; config,
health, job status, selections and catalog access remain responsive.
