# Accepted read-only JIRAM candidate-overlap audit

The accepted expansion prioritizes JIRAM overlap and requires measured coverage.
The science executor may read existing catalog/index data and the existing
science/matches API. Write only small scripts/JSON under
<mirror>/junocam/expansion_2026-09-08/evidence/; all repository files, gates,
fixtures, scientific data and policy are read-only. No native acquisition,
reprojection, exports or velocity/statistical interpretation is authorized.

For each of the194 newly cleared IDs, request existing cross-instrument
matches with max_dt_s300, min_overlap0.25 and limit100. Record the exact request,
returned IDs/bands/timing/approximate overlap and any capped result. Summarize
per pass how many new JunoCam observations have at least one candidate, and
how many have a returned M-band candidate. Counts of returned pairs are not
an exhaustive census when the result is capped. Choose one returned example
per pass for the lead to reproduce. Do not infer native JIRAM pixel availability
from a geometry-index match or label a candidate as registered/physically
equivalent. Save provenance and explain the spherical bounding-box approximation
and cross-instrument altitude/illumination limitations.

Lead acceptance: reproduce the selected examples through the current API,
check counts against the saved per-ID records, and retain final interpretation.
No source change or numerical acceptance gate is involved in this read-only
inventory audit.

Judgment calls: a bounded five-minute window and quarter-of-smaller-box
threshold give a reproducible candidate search, not a scientific coincidence
criterion; distinguish metadata candidates from verified pixel pairs.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
