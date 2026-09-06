# Command reference and worked examples

All commands below are `uv run jiram-catalog <subcommand> ...` from the
repository root. Every subcommand accepts `--mirror PATH` (else
`$JIRAM_MIRROR`, else the TOML config, else the built-in default -- see
`docs/configuration.md`) and `-v` for debug logging; most also accept
`--orbits SPEC` where `SPEC` is `all` or a comma list of integers and
ranges, e.g. `4,5,10-20`. Usage lines below are pasted from `--help`
(regenerate with the same command if a spec changes).

## Environment variables

| variable | purpose | default |
| --- | --- | --- |
| `JIRAM_MIRROR` | local mirror root (labels, images, SPICE kernels, every product this tool writes) | `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror` |
| `JIRAM_PAPER_DATA` | published perijove-4 ground truth (Ingersoll et al. 2022 maps, TRACKER4 vectors); read-only, used by the gates | `/expanse/lustre/projects/cla119/kaushiks/JIRAM` |
| `JIRAM_CONFIG` | path to a TOML file overriding the two roots above; unset means `~/.config/jiram_catalog.toml` | (unset) |
| `JIRAM_SKIP_GATES` | set to `1` to run the offline test suite without the mirror/paper data (`pytest`'s own convention, not a `jiram-catalog` option) | (unset) |

See `docs/configuration.md` for the full precedence rule and the TOML
file's schema; `uv run jiram-catalog config` prints what will actually
be used and why.

## Subcommand index

```
usage: jiram-catalog [-h]
                     {manifest,mirror,index,kernels,geo,stats,regions,region-stack,movie,export-goflow,strips,strip-stats,gui,config}
                     ...
```

| subcommand | what it does |
| --- | --- |
| `manifest` | list the archive's files into `manifest.parquet` |
| `mirror` | download labels and/or image data |
| `index` | parse labels into `frames.parquet` |
| `kernels` | download the SPICE kernels a set of orbits needs |
| `geo` | compute SPICE geometry into `frames_geo.parquet` |
| `stats` | print/write frame-index summary statistics |
| `regions` | list the region registry |
| `region-stack` | reproject frames onto a region, write a time stack |
| `movie` | render a stack as MP4/GIF |
| `export-goflow` | write constant-cadence realizations for the velocity model |
| `strips` | build the per-pass, per-sequence strip library |
| `strip-stats` | spectra, structure functions and bicoherence of strips |
| `gui` | serve the catalog browser |
| `config` | show the resolved mirror/paper-data paths and their source |

## Archive mirroring: `manifest`, `mirror`, `index`, `stats`

```
usage: jiram-catalog manifest [-h] [--mirror MIRROR] [--orbits SPEC] [-v]
                              [--refresh]
```
Fetches the archive listing for each selected orbit (cached; `--refresh`
re-fetches) and writes `manifest.parquet`.

```
usage: jiram-catalog mirror [-h] [--mirror MIRROR] [--orbits SPEC] [-v]
                            [--family {img,spe,log,all}] [--kinds KINDS]
                            [--jobs JOBS] [--verify]
```
Downloads labels and/or data (`--kinds labels,data`, the default) for
one product family (`--family img`, the default -- this catalog is
camera-frames-only, see `docs/decisions.md`). `--verify` checks MD5
against the label and re-downloads mismatches once.

```
usage: jiram-catalog index [-h] [--mirror MIRROR] [--orbits SPEC] [-v]
                           [--jobs JOBS]
```
Parses every mirrored `.LBL` into `frames.parquet` and assigns sequence
ids (see `docs/architecture.md`).

```
usage: jiram-catalog stats [-h] [--mirror MIRROR] [--orbits SPEC] [-v]
```
Prints (and writes to `<mirror>/index/stats.md`) frame counts, a
per-orbit table, and sequence-length/gap histograms.

### Worked example: index a new orbit

```
uv run jiram-catalog manifest --orbits 24
uv run jiram-catalog mirror --orbits 24 --kinds labels --jobs 3
uv run jiram-catalog mirror --orbits 24 --kinds data --jobs 3 --verify
uv run jiram-catalog index --orbits 24
uv run jiram-catalog stats --orbits 24
```
For orbit 4 in the current mirror, `stats` reports 1,111 frames (558 L,
553 M), 58 sequences, spanning 2017-02-01T19:48:54 to
2017-02-02T18:28:06, 245,735,424 bytes of `.IMG` data.

## SPICE geometry: `kernels`, `geo`

```
usage: jiram-catalog kernels [-h] [--mirror MIRROR] [--orbits SPEC] [-v]
                             [--jobs JOBS] [--dry-run]
```
Downloads the static kernel set plus, per selected orbit, the
reconstructed CK and SPK the archive labels name. `--dry-run` reports
present/missing without downloading. Kernels for a new orbit are
usually needed before `geo` can run on it.

```
usage: jiram-catalog geo [-h] [--mirror MIRROR] [--orbits SPEC] [-v]
                         [--jobs JOBS] [--limit LIMIT]
```
Computes per-frame SPICE geometry into `frames_geo.parquet` (one row per
band half). `--limit K` caps frames per orbit, for a quick check.
Prints (and writes `<mirror>/index/geo_report.md`) the agreement between
this geometry and whatever label geometry exists.

### Worked example

```
uv run jiram-catalog kernels --orbits 24
uv run jiram-catalog geo --orbits 24 --jobs 8
```

## Regions and stacks: `regions`, `region-stack`, `movie`, `export-goflow`

```
usage: jiram-catalog regions [-h] [--config CONFIG] [-v]
```
Lists the region registry (`configs/regions.yaml`): `north_pole_paper`
(the published perijove-4 grid, 15 km/px), `north_pole` and `south_pole`
(the same rule, either hemisphere, 3600x3600), `neb_15n` (a mid-latitude
example, `local_ortho`, 10 km/px). See `docs/architecture.md` for what
`polar_ortho` and `local_ortho` mean.

```
usage: jiram-catalog region-stack [-h] [--mirror MIRROR] [--orbits SPEC] [-v]
                                  --region REGION --band {L,M}
                                  [--level {frame,sequence}] [--out OUT]
                                  [--config CONFIG] [--no-crop]
                                  [--margin-px MARGIN_PX]
                                  [--max-emission MAX_EMISSION]
                                  [--min-on-planet MIN_ON_PLANET]
                                  [--jobs JOBS]
```
Selects every frame overlapping the region, reprojects each, and writes
a time stack (`--level frame`) or one-per-sequence composite
(`--level sequence`). Default output path:
`<mirror>/regions/<region>/<band>_orbits<SPEC>_<level>.nc`.

```
usage: jiram-catalog movie [-h] --out OUT [--fps FPS] [--pct LOW HIGH]
                           [--cmap CMAP] [-v]
                           stack
```

```
usage: jiram-catalog export-goflow [-h] --out OUT [--dt-tol DT_TOL]
                                   [--min-frames MIN_FRAMES] [--crop-to-valid]
                                   [-v]
                                   stack
```

### Worked example: build a polar stack and a movie

```
uv run jiram-catalog region-stack --region north_pole_paper --orbits 4 --band M --level frame
uv run jiram-catalog region-stack --region north_pole_paper --orbits 4 --band M --level sequence
uv run jiram-catalog movie <mirror>/regions/north_pole_paper/M_orbits4_sequence.nc \
    --out <mirror>/regions/north_pole_paper/M_orbits4_sequence.mp4
```
On the current mirror the frame-level stack for orbit 4 has 294 time
steps (2.25 GB); the sequence-level composite has 25 (one per spin
sequence), each with more valid pixels than any single contributing
frame.

### Worked example: export for the velocity model

```
uv run jiram-catalog export-goflow <mirror>/regions/north_pole_paper/M_orbits4_sequence.nc \
    --out <mirror>/regions/north_pole_paper/goflow_M_orbits4
```
This produced 10 realizations (3-4 frames each, `dt_img_s` 470.4-487.5 s)
on the current mirror; see `docs/data_products.md` for the file layout
and `docs/reports/goflow_summary.md` for why it looks like this.

## Strips and statistics: `strips`, `strip-stats`

```
usage: jiram-catalog strips [-h] [--mirror MIRROR] [--orbits SPEC] --band
                            {L,M} [--lat-band LO:HI] [--min-frames MIN_FRAMES]
                            [--jobs JOBS] [--limit LIMIT] [-v]
```
`--lat-band` filters unit rows by boresight latitude before chunking; a
negative lower bound needs `=`, e.g. `--lat-band=-45:45` (otherwise
argparse reads `-45:45` as an unknown option).

```
usage: jiram-catalog strip-stats [-h] [--mirror MIRROR] [--orbits SPEC]
                                 [--band {L,M}] [--resolution-class KM]
                                 [--max-lag-px MAX_LAG_PX] [--n-fft N_FFT]
                                 [--min-valid-frac MIN_VALID_FRAC]
                                 [--population] [--out OUT] [--limit LIMIT]
                                 [-v]
```
`--resolution-class KM` restricts to strips of one class (required for
`--population`, which additionally writes the population-mean product
for that class -- see `docs/data_products.md`).

### Worked example: build strips for a latitude band

```
uv run jiram-catalog strips --orbits 4,24 --band M --lat-band=-45:45 --jobs 8
```

### Worked example: compute statistics

```
uv run jiram-catalog strip-stats --orbits 4,24 --band M
uv run jiram-catalog strip-stats --orbits 4,24 --band M --resolution-class 150 --population
```

## The catalog browser: `gui`

```
usage: jiram-catalog gui [-h] [--mirror MIRROR] [--port PORT]
                         [--address ADDRESS] [--no-browser] [-v]
```
Serves the three-tab browser application: a FastAPI backend under
`src/jiram_catalog/api/` answering Arrow/JSON/PNG, and a React + deck.gl
front end (`frontend/`, committed as a built bundle) that holds all
state and does all drawing in the browser. See `docs/gui_usage.md` for
the SSH-tunnel workflow, what each tab shows, and what it writes, and
`docs/gui_v2_notes.md` for the front end's architecture.

```
uv run jiram-catalog gui --port 5006 --no-browser
```

## Configuration: `config`

```
usage: jiram-catalog config [-h]
```
Prints the resolved mirror and paper-data paths and which tier of the
precedence chain supplied each (`env`, `file`, or `default`). See
`docs/configuration.md`.

```
$ uv run jiram-catalog config
mirror: /expanse/lustre/projects/cla119/kaushiks/jiram_mirror (source: default)
paper_data: /expanse/lustre/projects/cla119/kaushiks/JIRAM (source: default)
```

## Running the test suite

```
JIRAM_SKIP_GATES=1 uv run pytest -q      # offline: no mirror, no paper data needed
uv run pytest -q                          # includes the read-only gates (needs the mirror and paper data)
```
The gates (`tests/test_gate_*.py`) compare the pipeline's output against
the published perijove-4 ground truth and are read-only to every
contributor and agent -- see `docs/agent_harness.md`.
