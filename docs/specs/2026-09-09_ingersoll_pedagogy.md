# Ingersoll et al. (2022): pedagogical methods note

## Authorization and scope

The owner requested a pedagogical deconstruction of the paper's cloud-motion
method, initially as a readable `.tex` document, then explicitly changed the
main delivery to a pedagogical slide deck for faster learning. Retain the
longer tutorial as companion notes. This is documentation work;
it does not authorize implementation of the proposed GUI/science features.

Files in scope:
- `docs/pedagogy/ingersoll_2022_tracking.tex`
- `docs/pedagogy/ingersoll_2022_tracking.pdf` (compiled reading copy)
- `docs/pedagogy/ingersoll_2022_tracking_slides.tex` (main editable deck)
- `docs/pedagogy/ingersoll_2022_tracking_slides.pdf` (main reading copy)
- `docs/README.md` (link to the note)

Application code, gates, fixtures, existing specs, native/derived scientific
data, and the published reference directory are read-only. Temporary research
and TeX build files belong in the configured Lustre `TMPDIR`.

## Content and evidence

Identify the Nature Astronomy article by DOI 10.1038/s41550-022-01774-0.
Read the full methods and seek the supplementary methods. Distinguish facts
reported in the publication, observations from local products/code, original
teaching examples, and proposed extensions. Explain image geometry/timing,
correlation tracking and its relationship to optical flow, texture masks,
velocity units, sampling versus resolution, contour vorticity/divergence,
independent-pair noise estimation and its assumptions, and scientific limits.
Use independently derived worked examples rather than reproducing paper text
or figures. Include source links and precise method/table locators.

Inspect local tracking implementation and prior validation, including tail
errors and limitations. Do not call the classical reimplementation identical
to the authors' software or their vectors perfect wind truth. Record the
Tracker3/Tracker4 naming discrepancy and distinguish 45 km spacing from altitude.
Summarize possible repository features as proposals only.

## Read-only review subtask

A reviewer may inspect this spec, existing tracking code/report, selected
published-product headers, and the new note when available. Return factual
corrections, numerical/convention checks and ambiguity findings; edit nothing.
No application test gate is appropriate for a prose-only change. The lead
checks citations, math examples, scope, TeX compilation and rendered pages.

## Completion

Standalone tutorial and Beamer TeX compile without missing references or
overfull text; readable PDFs are delivered. Slides use one principal idea per
frame, compact diagrams and worked examples, with sources/provenance and
judgment calls retained. Original scientific examples have checked arithmetic and
clearly stated assumptions. The docs index links both files. The final note
ends with judgment calls and unresolved alternatives. No scientific data are
modified and no wind-feature implementation is claimed.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
