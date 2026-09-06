# jiram_catalog

A catalog, geometry engine, and region-extraction toolkit for the full
Juno JIRAM infrared imager archive (PDS4 bundle `juno_jiram_bundle`,
Atmospheres node), built to feed cloud-tracking and optical-flow style
velocity retrieval at Jupiter.

## What it does

JIRAM's calibrated archive is ~85,000 camera frames, each with archive
label geometry that is missing for the majority of them (absent
entirely for orbits from 2021 onward, and for most dual-band products
at any date -- see `docs/decisions.md`). This tool:

1. **Mirrors** the archive's labels and image data locally, and parses
   every label into one indexed table (`frames.parquet`), with spin
   sequences reconstructed from the archive's own bookkeeping.
2. **Computes geometry from scratch** for every frame with a vectorised
   SPICE engine (per-pixel latitude, longitude, emission, incidence,
   phase; boresight and footprint summaries in `frames_geo.parquet`),
   independent of the label geometry, which becomes only a cross-check.
3. **Reprojects** frames onto named map regions by exact inverse camera
   modelling -- polar caps on the published perijove-4 grid (or any
   pole, any orientation) for repeat-view time series, and per-pass
   tangent-plane patches for the rest of the planet, where JIRAM
   revisits nothing and the natural product is a library of independent
   *strips* rather than a time stack.
4. **Exports** region time stacks as movies and as constant-cadence
   triples in the layout a downstream optical-flow velocity model reads,
   and computes masked spectra, structure functions, and bicoherence for
   the strip library.
5. **Serves a browser-based catalog** (Panel/Bokeh) with three tabs --
   Catalog (every frame, filterable and selectable), Poles (stack
   viewer, movies, exports), Strips (the strip library, its statistics)
   -- over an SSH tunnel from a cluster node.

Every claim above is checked against a published result: the geometry
engine, the reprojection, and the map grid it uses were all validated
against the 48 perijove-4 north-polar maps and TRACKER4 wind vectors of
Ingersoll et al. (2022) before anything downstream was built. See
`docs/architecture.md` for how the pieces fit together and
`docs/decisions.md` for what was found and settled along the way
(byte order, band ordering, the map projection, and more).

## What exists today

- Full-archive label mirror and index (85,108 camera frames); image
  data mirrored for a growing subset of orbits (complete and verified
  for orbits 4 and 24; see `docs/open_items.md` for the rest).
- SPICE geometry for every mirrored frame (`frames_geo.parquet`,
  113,565 rows), with three orbits (38, 55, 70) still missing geometry
  because of SPICE kernel coverage gaps -- an open item, not silently
  dropped.
- The region registry (`north_pole_paper`, `north_pole`, `south_pole`,
  `neb_15n`), region time stacks, movies, and velocity-model export.
- The strip library (289 strips across orbits 4 and 24 as of this
  writing) and its statistics.
- The trackability report: which orbits and latitude bands have
  repeat-view geometry that supports velocity retrieval at all, and at
  what wind speed.
- The three-tab GUI, first version (Catalog complete; Poles views
  existing stacks; Strips has the table, map, viewer, and per-strip
  statistics -- population statistics and in-app builds are deferred,
  see `docs/open_items.md`).

## Quickstart

```
uv sync
uv run jiram-catalog config                 # confirm which mirror/paper-data paths you'll use
uv run jiram-catalog manifest --orbits 4
uv run jiram-catalog mirror --orbits 4 --kinds labels,data --jobs 3 --verify
uv run jiram-catalog index --orbits 4
uv run jiram-catalog kernels --orbits 4
uv run jiram-catalog geo --orbits 4
uv run jiram-catalog regions
uv run jiram-catalog region-stack --region north_pole_paper --orbits 4 --band M --level sequence
```

`uv run jiram-catalog --help` lists every subcommand; `docs/usage.md`
has the full reference and more worked examples (a movie, a
velocity-model export, strips, statistics, the GUI).

## Configuration

Two paths matter: the local **mirror** (everything this tool downloads
or writes) and the published **paper data** (read-only ground truth for
the gates). Both resolve as `explicit argument > environment variable >
TOML config file > built-in default`, and both default to this group's
shared copies on the cluster, so doing nothing reproduces the existing
setup:

| setting | environment variable | default |
| --- | --- | --- |
| mirror | `JIRAM_MIRROR` | `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror` |
| paper data | `JIRAM_PAPER_DATA` | `/expanse/lustre/projects/cla119/kaushiks/JIRAM` |

Run `uv run jiram-catalog config` to see what will actually be used and
where it came from. Full details, including the optional TOML file, are
in `docs/configuration.md`.

## Where to read next

- `docs/README.md` -- index of every document in this repository.
- `docs/architecture.md` -- the system as built, module by module.
- `docs/data_products.md` -- the schema of every file this tool writes.
- `docs/usage.md` -- the command reference and worked examples.
- `docs/decisions.md` / `docs/open_items.md` -- what was settled, and
  what is still open.
- `AGENTS.md` -- start here before changing any code; this repository
  is built by a spec -> gate -> execute -> verify loop
  (`docs/agent_harness.md`), and gates, fixtures, and specs are
  read-only to executors.
