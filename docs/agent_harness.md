# Agent harness — how this project is built and extended

This repository is developed by a lead agent that delegates bounded
work to executor agents. The harness is the set of rules that make
that safe. Colleagues and future agents should follow the same loop.

## The loop, per milestone
1. **Facts first.** Unknowns are measured before anything is
   specified: a reconnaissance agent reports numbers (sizes, label
   fields, conventions), never designs. Reports live in `docs/reports/`.
2. **Spec.** The lead writes `docs/specs/<date>_<name>.md`: goal, files
   in scope, read-only list, measured facts, every decision made
   explicitly, exact API names, tests to write, the validation block
   that defines done, and the report format. Specs end with the
   verbatim clause: "If any part of this spec is ambiguous or
   underdetermined, do NOT choose an interpretation. Stop, list the
   ambiguities and the options, and make no further changes. Also list
   every judgment call you made, however minor, at the end of your
   final message." (Long-running executors may instead implement
   everything else and list the ambiguity with their choice.)
3. **Gate before dispatch.** The lead writes `tests/test_gate_<name>.py`
   before the executor starts. Gates compare against ground truth
   (the published PJ4 maps and vectors, SPICE oracles, synthetic
   fields with known answers). Gates, fixtures and specs are read-only
   to executors; passing a gate by editing it is task failure.
4. **Dispatch.** One executor per spec, fresh context, the spec file
   as the whole brief. Concurrent executors own disjoint files; shared
   files (`cli.py`, `pyproject.toml`, `uv.lock`) are edited only by the
   lead, who adds dependencies up front and wires subcommands after.
5. **Verify.** The lead runs the scope check (`git diff --stat` on the
   read-only list must be empty; changed files must match the spec's
   list), runs the gates and the full suite, reads the judgment calls,
   and adjudicates ambiguities. The executor's report is a claim.
6. **Record.** Outcome, executor, wall time, and gate result go in
   `docs/build_log_<date>.md`; decisions go in `docs/decisions.md`;
   anything left open goes in `docs/open_items.md`.

## Routing
- Reconnaissance, literature, and reader-facing prose: Sonnet-class.
- Mechanical code from an airtight spec: the cheapest executor that
  has passed a gate here.
- Bounded numerical or geometric work, anything with a SPICE or
  projection oracle, and fresh-context review: Opus-class.
- Never delegated: latitude and projection conventions per region,
  acceptance of geometry against ground truth, the decision to mirror
  image data at scale, scientific interpretation of results.

## Conventions every executor must know
- Planetocentric latitude, east-positive longitude in [0, 360).
- Geometry epoch is the label `START_TIME` exactly; Juno spins at
  12 deg/s, so never add exposure offsets.
- Archive `.IMG` files are little-endian float32 despite their labels.
- In 256-line dual-band products the top 128 lines are L, the bottom
  128 are M.
- Arrays are `(..., y, x)` with row index increasing with +y; plot with
  `origin="lower"`; SI units; FFT `norm="forward"`, angular wavenumbers
  (the user's conventions document, version `conventions-v1`).
- Environment: `uv` project, Python 3.12; `naif.jpl.nasa.gov` needs the
  `certifi` CA bundle; no system ffmpeg (use `imageio-ffmpeg`); the
  session scratch directory is wiped between sessions, so durable
  notes go in `docs/`.
- Executors do not edit the committed front-end build
  (`src/jiram_catalog/webapp/dist/`) directly: it is generated from
  `frontend/` by `npm run build`, and a hand edit there is invisible
  the next time someone rebuilds. Change `frontend/src/` and rebuild
  instead.

## Experience so far (2026-09-04/05)
- Two-stage reconnaissance (archive, geometry) cost about 230k Sonnet
  tokens and settled the two facts the whole design rests on.
- The crawler and index were delivered by a mechanical executor against
  a pre-written gate in one pass.
- The geometry engine went to the same executor three times: it
  correctly stopped twice on real arithmetic errors in the spec, then
  stalled for two hours; an Opus executor delivered it in 16 minutes
  and found one more flaw in the algorithm. Every later milestone went
  to Opus: 15 to 45 minutes each, one to three spec ambiguities each,
  all resolved sensibly and reported.
- Lead time is dominated by writing specs and gates, about one hour
  per milestone, and by reading reports capped at 50 lines.

## Adding a milestone
Copy the structure of the most recent spec; measure unknowns first;
write the gate; add dependencies yourself; dispatch; verify; record.
