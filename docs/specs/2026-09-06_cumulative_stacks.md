# Spec: cumulative sweep stacks and the three viewing modes in the Poles view

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog.
Read first: `src/jiram_catalog/stacks.py` (`build_stack`,
`composite_sequences`, `write_stack`, `read_stack`, `stack_output_path`),
`src/jiram_catalog/cli.py` (the `region-stack --level` argument; the
lead has already added the choice `cumulative` there and passes it
through as `level`), `src/jiram_catalog/api/stacks.py` (listing, meta,
frames, build job), `frontend/src/views/` (Poles view), `frontend/e2e/`,
`docs/specs/2026-09-06_api_contract.md`.

## Why
The owner wants three ways to watch a polar region: what the imager
records spin by spin (frame level), one snapshot per sweep showing
structure evolution (sequence level, the velocity-model input), and
the sweep filling in frame by frame and resetting at the next sequence
(cumulative). The third is new.

## Part A: product (`stacks.py`)
- `accumulate_sequences(stack: xr.Dataset) -> xr.Dataset`: input a
  frame-level stack; output the same `time` axis and grid; for time
  index `t` belonging to sequence `s`, `image[t]` is the composite of
  the frames of `s` with index `<= t` using EXACTLY the rule of
  `composite_sequences` (mean of valid pixels, `valid` = any,
  `emission` = min); per-time coords copied from the frame stack plus
  `seq_index` (0-based position within the sequence) and `seq_n`;
  attrs as the input with `level = "cumulative"`. Implement as a
  running update per sequence (keep running sum and count arrays; one
  pass; never materialise more than the running arrays plus one
  frame). Property to test: for the last index of every sequence,
  `image`, `valid`, `emission` equal the corresponding row of
  `composite_sequences(stack)` (allclose, NaN-aware).
- `build_stack` path: when `level == "cumulative"` the CLI builds (or
  reuses, if the frame-level file already exists and is newer than the
  index) the frame-level stack, then writes the cumulative file with
  `stack_output_path(..., level="cumulative")` (file stem
  `<band>_orbits<SPEC>_cumulative.nc`). The same for the API build job
  (`level` value `cumulative`).

## Part B: API
- `GET /api/stacks` items gain `siblings: {frame?: id, sequence?: id,
  cumulative?: id}` (ids of stacks with the same region, band and
  orbit token) and `label` (`"Instrument frames"`, `"Region
  snapshots"`, `"Accumulating sweep"`); the listing is sorted region,
  band, then level order sequence, cumulative, frame.
- `meta.per_time[i]` gains `seq_index` and `seq_n` when present.
- `POST /api/stacks/build` accepts `level` in `frame|sequence|cumulative`.

## Part C: front end (Poles view)
- A mode selector (three labelled buttons) above the viewer; selecting
  a mode opens the sibling stack of that level if it exists, else shows
  a "Build this view" button that posts the build job with the
  current stack's region, band, orbits and the requested level, and
  switches to it when the job finishes.
- Stack list: badge with the label; description line under the list
  explaining the three modes in one sentence each.
- The time readout shows, for cumulative stacks, "sweep k, frame i of
  n" from `seq_index`/`seq_n`.
- Build dialog: `level` shown with the three labels.
- `#debug-state` gains `level`.
- Tests: vitest for the sibling resolution and label mapping; e2e:
  switching mode on the paper sequence stack to "Instrument frames"
  changes `stack_id` and `level`; if a cumulative sibling exists
  (`north_pole_paper/M_orbits4_cumulative`), selecting it works and the
  readout shows "sweep".

## Files in scope
`src/jiram_catalog/stacks.py`, `src/jiram_catalog/api/stacks.py`,
`src/jiram_catalog/api/data.py` (if listing helpers live there),
`tests/test_regions_offline.py` (add tests for `accumulate_sequences`;
do not change existing tests), `tests/test_api_offline.py` (add),
`frontend/src/**`, `frontend/tests/**`, `frontend/e2e/**`,
`src/jiram_catalog/webapp/dist/**` (rebuild), `docs/gui_v2_notes.md`
(the paragraphs that change), `docs/gui_guide.md` (Poles section: the
three modes; no new screenshots required). READ-ONLY: everything else
including `cli.py`, `pyproject.toml`, `uv.lock`, gates. Under the
mirror write only `regions/north_pole_paper/M_orbits4_cumulative.nc`
(build it with the CLI as part of validation) and `gui_cache/`.

## Validation that defines done
```
JIRAM_SKIP_GATES=1 uv run pytest -q
uv run jiram-catalog region-stack --region north_pole_paper --orbits 4 --band M --level cumulative
uv run pytest -q tests/test_gate_regions_pj4.py tests/test_gate_cumulative.py tests/test_gate_api.py
cd frontend && npm run typecheck && npm test && npm run build
uv run pytest -q tests/test_gate_frontend.py
```
READ-ONLY gate `tests/test_gate_cumulative.py`: the cumulative file
exists with the frame stack's `time` length (294); for every sequence
the last cumulative step equals the sequence composite from
`M_orbits4_sequence.nc` (allclose with NaN equality) and the first step
equals the first frame; `seq_index` runs 0..n-1 within each sequence;
the API lists the three siblings for the paper region with the right
labels.

## Report (at most 30 lines)
Commands and outcomes; build time and file size; judgment calls;
ambiguities with the choice made.
