# Spec: pedagogical review of the codebase (document + slide deck)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog.
Audience: ONE reader, the project's owner: an ocean modeler, fluent in
numerical methods, spectral analysis and machine learning, who did not
write this code, is new to planetary archives and the SPICE toolkit,
and wants to understand the design and the important implementation
details well enough to modify anything. Register: pedagogical,
concrete, unhurried; explain terms once; show short code excerpts and
the reasoning behind them; keep the dead ends and debugging scars as
first-class sections, because the transferable lesson usually lives in
the bend, not the result. No executor product names anywhere.

## Deliverables
1. `PEDAGOGICAL_REVIEW.md` at the repository root (target 1,500-2,500
   lines; Markdown with fenced code, tables, and simple ASCII diagrams).
2. `docs/pedagogy/slides.tex` (beamer, `metropolis` theme if installed,
   else `default`; 60-90 slides; one idea per slide; code via
   `listings` or `minted`-free `verbatim`; figures from
   `docs/pedagogy/figures/`) and `docs/pedagogy/slides.pdf` compiled
   with `latexmk -pdf`. The deck follows the review's structure and
   cites section numbers of the review on each slide footer.
3. Figures you generate with matplotlib into `docs/pedagogy/figures/`
   (PNG, dpi 150, from real data): at least (a) one raw 128x432 frame
   next to its per-pixel latitude map, (b) the paper's n01a map beside
   our reprojection of the same frame with the difference, (c) one
   mid-latitude strip with its graticule, (d) the isotropic spectrum
   and a structure function of that strip, (e) the trackability heatmap
   (copy the existing one), (f) a sketch of the SPICE frame chain and of
   the pinhole model (matplotlib or TikZ). Reuse existing scripts and
   modules; do not modify them.

## Sources (read all; verify claims against code before writing)
`README.md`, `docs/README.md`, `docs/architecture.md`,
`docs/data_products.md`, `docs/usage.md`, `docs/decisions.md`,
`docs/open_items.md`, `docs/agent_harness.md`,
`docs/build_log_2026-09-04.md`, `docs/gui_design.md`, every file in
`docs/specs/` and `docs/reports/`, and the source under
`src/jiram_catalog/` (read the bodies; this review is about
implementation). Data: `JIRAM_MIRROR` default (see
`uv run jiram-catalog config`); the published maps under the
paper-data root are read-only.

## Required structure of the review (sections; keep this order)
1. What the tool is for, and the two regimes (time series at the
   poles, statistics at mid-latitudes), in one page.
2. The three archive facts that shaped everything, each with how it was
   discovered and what it forced: labels lose geometry after orbit 38;
   image files are little-endian despite the labels; sequences are
   numbered from 1 and frame 1 never arrives.
3. Architecture: layers as built; module map with one paragraph per
   module; the data flow from archive to product as an ASCII diagram;
   where every product lives on disk.
4. Deep dives, one subsection per module, each with: purpose; the key
   data structures; the algorithm (equations where they exist); the
   two or three implementation details a modifier must know; the
   validation gate and what it protects; scars. Cover: pds/mirror
   (robots, wget, completeness checks), labels (PVL parsing, band
   groups), index (sequence segmentation), geometry (SPICE frame chain,
   pinhole model, light time and stellar aberration including the
   observer-vector correction found during implementation, vectorised
   ellipsoid intercept, the 12 deg/s epoch sensitivity, the mirror-blind
   result), kernels, geo (augmentation, spawn workers, longitude arcs,
   pole handling, limb frames and the label-centroid caveat, the
   L-band residual), vicar, reproject (inverse camera model,
   orthographic paper grid: the fit story and the radius degeneracy),
   regions (polar and local orthographic grids, the visibility
   subtlety), stacks (selection, memory design, composites),
   movie/export_goflow (conventions, log-gradient, cadence runs),
   tracking (masked NCC as cost volumes, sub-pixel refinement, the
   reversed line axis of the published vectors, index base), strips
   (chunking, resolution classes), stats2d (conventions-v1, masked
   spectra and mask leakage, shell counting, bicoherence),
   trackability (definitions; the best-baseline redefinition), gui
   (state, lazy stacks, rasterisation, graticule seams), config and the
   subcommand-registration pattern.
5. Testing philosophy: offline tests versus gates; how each gate was
   constructed from ground truth; what each guards; the two gates that
   were wrong and how that was found.
6. How it was built: the spec-gate-execute-verify loop in practice,
   what each executor tier did, the stalls and the ambiguity stops,
   the byte-order and arithmetic errors caught by executors, the
   documentation fact-check; what this teaches about delegating
   numerical work.
7. How to extend it: a worked example (add a new region type or a new
   statistic), step by step through spec, gate, module, wiring, docs.
8. Glossary (SPICE terms, PDS terms, planetocentric, System III,
   emission angle, RDR, IK/FK/CK/SPK, orthographic, etc.).

## Rules
- Write only the three deliverables (and the figures). Change no code,
  spec, report, or other document. No commits.
- Every number quoted must come from a report, the build log, a gate,
  or a measurement you make; cite the source file inline in the review.
- Where the code and a document disagree, the code wins; say so.
- Use `uv run python` for figure generation; kernels for orbit 4 load
  with `KernelSet.for_orbits(mirror, [4])`.
- The deck must compile: run `latexmk -pdf -interaction=nonstopmode
  slides.tex` in `docs/pedagogy/` and fix errors; keep the `.tex`
  readable (no generated noise).

## Validation that defines done
```
test -s PEDAGOGICAL_REVIEW.md && wc -l PEDAGOGICAL_REVIEW.md
ls docs/pedagogy/figures/*.png | wc -l      # >= 6
test -s docs/pedagogy/slides.pdf
grep -rci codex PEDAGOGICAL_REVIEW.md docs/pedagogy/slides.tex   # zeros
JIRAM_SKIP_GATES=1 uv run pytest -q                                # unchanged
```

## Report (at most 30 lines)
Line and slide counts; figures produced; any claim you could not
verify; judgment calls.
