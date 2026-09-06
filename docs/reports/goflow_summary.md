# goflow_rnd repository summary (for JIRAM cataloging-tool design)

Repo read (read-only): `/home/kaushiks/scratch/goflow_rnd`. Nothing created/modified/deleted there.

## 1. What this repo is

`goflow_rnd` is NOT the GOFlow model itself; it's a separate R&D repo that
pretrains/extends GOFlow with synthetic spectrally-controlled passive-scalar
transport (README.md:1-9; AGENTS.md:3-5). The real GOFlow model/training
code lives at `/expanse/lustre/projects/cla119/kaushiks/goflow/goflow`
commit `07cad15` (docs/provenance.md:7-9) and was NOT read (out of scope).
goflow_rnd re-implements a compact port of GOFlow's UNet
(`src/goflow_rnd/models/unet.py:1-9`) and reproduces GOFlow's exact
input/target conventions for controlled synthetic experiments.

## 2. Original GOFlow input contract (quoted secondhand from docs/provenance.md:11-16)

Citing `goflow/dataSST.py`/`goflow_core.py` @ 07cad15:
- input: 3 frames of `loggrad_T = log10|grad T|` (K/m), 12 h apart, land
  zero-encoded, clipped to `[-19, 0]`;
- target: `U,V` at the middle frame (`num_input_frames // 2`), or last frame
  if `causal=True`;
- model: UNet (`nbase=16`), optional spectral/gradient loss, Tukey-window +
  boundary masks; eval: R^2 on velocity/gradients, spectral loss.
This is the authoritative "what GOFlow consumes" answer — a JIRAM tool
producing 3-frame log-gradient(brightness-temperature) sequences at fixed
cadence, limb/off-disk-masked, in `[-19,0]` decades/m, matches it directly.

## 3. goflow_rnd's own executable contract (read directly from code)

**File format**: per-realization NetCDF4 `realization.nc` (xarray, zlib,
1-frame chunks; pipeline.py:301-336). Variables (pipeline.py:216-231,
300-311): `image`,`valid`(bool),`loggrad` `(frame,y_img,x_img)` float32;
`u_mid,v_mid` (frame,...) rendered w/ image's PSF, no noise/jitter/mask;
`u_mean,v_mean,disp_x,disp_y` `(interval,...)`; optional truth-grid
`q_truth,u_truth,v_truth`. Global attrs incl. `array_order`,
`units_velocity="m s-1"`, `dx_truth_m`, `dx_img_m`, `dt_img_s`
(pipeline.py:324-329). Sidecars: `manifest.json` (schema
`src/goflow_rnd/schemas/manifest-v1.json`), `config.resolved.yaml`,
`diagnostics.json`, `timing.json`. Dataset = `rNNNNN/` dirs + `spec.json` +
`dataset_manifest.json` (dataset.py:12-47,122-159).

**Shapes/axes**: C-order, spatial axes last `(...,ny,nx)`, row index
increases with y (north-up, never flipped; docs/conventions.md:9-17). `u`=x
(east), `v`=y (north). `CropDataset.__getitem__` (dataset.py:238-263)
produces model input `x`: `(3,crop,crop)` float32, 3 consecutive frames
`[i-1,i,i+1]`, `loggrad` or standardized `image`; target `y`: `(2,crop,crop)`
(u,v) at middle frame `i` (or interval_mean/displacement); `mask`:
`(1,crop,crop)`. `UNet(3,2,...)` — hard-coded 3-in/2-out channels, dense
per-pixel output (train.py:104). Default crop 256x256 (smoke uses 64x64).

**dtype**: generation/solve float64/complex128; stored & model arrays
float32 (docs/conventions.md:12).

**Normalization**: `loggrad` input = GOFlow's exact min-max
`(x-(-19))/(0-(-19))` on `log10(|grad I|+eps)` clipped `[-19,0]`
(render.py:24-26,145-153; dataset.py:169-170) — the `[-19,0]` bound assumes
SI K/m gradient of temperature; a different physical quantity/units (e.g.
JIRAM radiance or brightness temp in different units) would need this
re-derived, not reused verbatim. `image`-mode: per-crop standardize (mean/std
over always-valid pixels only; dataset.py:172-176). Velocity target default
units = pixels/frame (`d=u*dt/dx`; docs/conventions.md:18-19); `m_per_s` also
supported.

**Sequence/cadence**: fixed 3-frame window (UNet in_channels=3); generator
makes longer per-realization sequences (24 hourly frames in
nominal_512/d0_rot_ideal, 6 in smoke) and the loader draws 3-frame windows
from them (dataset.py:214-221). `dt_img_s` is one constant per
realization/dataset (`FrameConfig`, config.py:157-163), config-defined, not
hard-coded to GOFlow's 12h (current configs use 3600s/1h). Midpoint-target
validity depends on `dt_img/tau_correlation` (docs/conventions.md:71-72; tex
eq. 140-156) — an explicitly open research question (Plan.md Phase 2).

**Grid**: regular, square pixels, SI units (docs/conventions.md:19-22).
Nominal case: 512x512 image px at 2km (from 1024x1024 truth at 1km;
configs/nominal_512.yaml; tex:180-184) — "nominal pixel sampling, not domain
size," since real sampling depends on viewing geometry (tex:182-184; the same
caveat applies to JIRAM's range/emission-angle-dependent footprint). No
spherical/map projection implemented (Plan.md Phase 8 entirely unchecked).

**Missing data/masks**: per-frame boolean `valid` from `cloud_mask` (render.py:114-121) — smooth correlated blobs, `missing_fraction`+`missing_corr_px` configurable; invalid pixels zeroed in input, excluded from normalization stats (dataset.py:174-176,245). "Land zero-encoded" for real GOFlow (docs/provenance.md:12) — land/limb masking must be supplied by the catalog tool, not inferred. Separate `edge_mask` (crops.py:63-68) trims a configurable border from loss/metrics (boundary guard, not validity).

**Geometry**: current code = flat, doubly periodic Cartesian plane only; no navigation model, no sphere. Guard-halo `h >= d_max+r_net+r_render` (crops.py:19-22; docs/conventions.md:74-76) hides synthetic periodic wraparound from the network — a synthetic-domain artifact, but the underlying principle (over-crop by receptive-field radius [~140px, models/unet.py:13] + expected navigation/PSF blur) generalizes to real JIRAM tiles. Spherical/Cassini geometry is design-only, not code — see §5.

**Supervision**: fully supervised on synthetic ground-truth `u,v` (from the
same spectral model driving advection) — explicitly a pretraining/curriculum
signal, "not validation" (AGENTS.md). Real domains (ocean, eventual JIRAM)
have no pixel-resolution ground truth; the intended real-data path is
synthetic pretrain -> self-supervised warp/forecast fine-tuning -> validation
against independent classical tracking, never visual plausibility alone.

## 4. INPUT DATA CONTRACT (spec block)

```
FORMAT:        NetCDF4 (xarray) for generated datasets; PyTorch NCHW at
               model-input time.
SEQUENCE:      3 consecutive frames per sample (fixed; UNet in_channels=3).
               dt_img_s constant per dataset, config-defined, not hard-coded
               (upstream GOFlow=12h, goflow_rnd expts=1h so far). No min/max
               cadence enforced; midpoint-target validity vs. dt_img/tau is
               an open research question.
ARRAY SHAPE:   x: (3,H,W) [channel,y,x] C-order, y north-up
               y: (2,H,W) ch0=u(east), ch1=v(north)
               mask: (1,H,W) valid & non-edge, float32
DTYPE:         float32 stored/model arrays (generation/solve float64)
INPUT VARIANT: "loggrad" (default,=GOFlow): log10(|grad I|) clipped [-19,0],
               min-max normalized to [0,1]. "image": raw scalar, per-crop
               standardized. (raw q, plain |grad q| exist in render.py but
               are not wired into CropDataset's `input` choice set today.)
GRID:          Regular square pixels, SI units. Nominal: 2km/px image from
               1km truth. No map projection/sphere in code yet.
MASK:          Per-frame boolean valid array; invalid px zeroed in input,
               excluded from normalization. Missing-data model = smooth
               correlated blobs, fraction+corr-length configurable.
GEOMETRY:      Flat Cartesian only (current code). Spherical/projected
               geometry, navigation, vector rotation are planned, not built.
SUPERVISION:   Fully supervised (synthetic ground truth) for pretraining;
               self-supervised warp/forecast losses intended for real-data
               fine-tuning (no ground truth expected for JIRAM).
TARGET:        Velocity at middle of 3-frame window by default (midpoint),
               units pixels/frame (d=u*dt/dx) or m/s; interval_mean and
               displacement targets also implemented.
```

## 5. What the planetary plan says specifically

- **No JIRAM/Jupiter mention anywhere in the repo** (full-repo case-insensitive
  grep for "jiram","jupiter","juno": zero hits). The one concretely named
  planetary target throughout tex/Plan.md/AGENTS.md is **Saturn/Cassini**
  (ISS, VIMS, thermal; north-polar hexagon) — AGENTS.md has a dedicated
  "Saturn-specific guardrails" section, no Jupiter equivalent. Any JIRAM
  tie-in is new work for the lead agent, using this Cassini plan only as a
  template (nav→JIRAM nav; VIMS filters→JIRAM's 5 IR channels; hexagon
  phase speed→Jovian jets/vortices e.g. GRS/polar cyclones) — analogy only.
- Regions (Cassini template): north-polar hexagon + Saturn's polar regions
  broadly (tex:554-563,575-581); wants cloud-feature motion explicitly
  separated from hexagon-boundary phase-speed motion.
- Preprocessing/nav (tex:583-596): inventory sequences w/ filter, cadence,
  resolution, geometry, pressure level; reproject to Saturn geometry + System
  III longitude; quantify nav/registration uncertainty via landmarks/limb
  fits before interpreting motion; run classical tracking alongside GOFlow on
  the same mapped sequences; compare to published winds; publish
  uncertainty/observability masks.
- Cadence: not fixed by the plan — "select image pairs/sequences using
  published feature-tracking cadence and quality criteria" (Plan.md Phase 10).
- Named obstacles (tex:554-581,775-804): ISS reflected sunlight/haze and
  depth-dependent VIMS filters are not passive temperature tracers; feature
  formation/dissipation, illumination change, nav error, foreshortening, and
  vertical sampling can violate brightness conservation; the hexagon is a
  wave **phase-speed** pattern, not necessarily material cloud motion —
  results must be labeled pattern-motion/transport/material-wind per
  evidence; published individual-vector uncertainty is "several to 10 m/s in
  favorable sequences, worse in poor geometry/strong shear" (tex:565-573).
  Same obstacle class applies to JIRAM (IR imager, spinning spacecraft,
  elliptical polar orbit) but is not written down anywhere in the repo.

**Existing planetary/JIRAM code**: none. `src/` has no planetary geometry,
projection, navigation, or Jupiter/Saturn-specific code; it is all
design-only (tex "Extension to the sphere"/"Cassini..." sections; AGENTS.md
"Spherical velocity fields"/"Saturn-specific guardrails"; Plan.md Phases
8-10, all unchecked). Only sphere-related dependency installed: `pyshtools`
(pyproject.toml:16; Plan.md Phase 0 note), unused by any module in `src/`.

## 6. Tooling

`uv` package manager (README.md:31-37; pyproject.toml `[tool.uv]`), env in
`$HOME/venvs/goflow_rnd` (NFS, not Lustre scratch — README.md:38-39),
`uv sync --frozen`. Python `>=3.12,<3.13`, pinned CPython 3.12.14
(docs/provenance.md:34-36). Deps (pyproject.toml:9-20): numpy>=2.2, scipy,
xarray, netCDF4, zarr, h5py, matplotlib, pyyaml, jsonschema, tqdm,
pyshtools>=4.13, **torch>=2.8** (CUDA `pytorch-cu126`, sm_70/sm_80 for
V100/A100). No JAX anywhere (grepped, zero hits). Test command `pytest -q`
(README.md:36; `testpaths=["tests"]`), 12 test modules, ~87+ deterministic
unit tests (Plan.md log); dev deps pytest, pytest-xdist, ruff. CLI
`goflow-rnd` (cli.py): `run`, `reproduce`, `gen-dataset`, `finalize-dataset`,
`train`, `eval-classical`, `check-env`. Cluster: SDSC Expanse RHEL 8.9; Slurm
scripts in `scripts/slurm/`, outputs on shared FS `runs/slurm/`.

## 7. data/, runs/, artifacts/ (names + counts only)

`data/`: `d0_rot_ideal/` (64 realization dirs r00000-r00063, 5 files each —
config.resolved.yaml, diagnostics.json, manifest.json, realization.nc,
timing.json — + dataset-level spec.json/dataset_manifest.json) and
`tiny_dev/` (6 realization dirs, same pattern). `runs/`: `nominal_512/`,
`nominal_512/reproduce/`, `smoke/`, `smoke_repro/`,
`train/tiny_dev_gpu_smoke/`, `slurm/` (~90 `.out`/`.log` files), plus loose
`pytest_*.log`/`latexmk.log`; ~100 files total. `artifacts/`: empty.

## 8. Scope notes

Did not open PDFs (tex read instead) or the upstream GOFlow/goflow_large_geom
repos (§2 is secondhand via docs/provenance.md). The 366-line external numerics
review under `docs/reviews/` was skimmed only, not relevant to the contract.
