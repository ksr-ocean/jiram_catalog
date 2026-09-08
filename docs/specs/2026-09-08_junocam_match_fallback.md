# Accepted empty-footprint matching repair

The expansion's read-only match audit found that ordinary JIRAM catalog rows
carry fp_lon=[] alongside valid c1_lon..c4_lon coordinates. The existing
longitude fallback treats the empty sequence as authoritative and returns no
box, causing false-negative cross-instrument matches. Fix this bounded defect
before reporting JIRAM candidate yield from the new JunoCam sample.

Executor owns only src/jiram_catalog/api/science.py. Gates, fixtures, specs,
other source and all scientific data remain read-only. The lead-owned fixed
gate is tests/test_gate_junocam_matches.py. No grid, physical model, policy,
time threshold, overlap formula or response schema changes are authorized.

Treat an empty longitude sequence as absent and use the existing corner
fallback. Preserve priority for valid explicit bounds and nonempty footprint
sequences, full-longitude handling, antimeridian behavior, band merging and
the unknown-bounds result when neither source is usable. Keep the change
small; do not redesign matching or widen this to unrelated malformed inputs.

Physical-band amendment from the same real-catalog audit: paired JIRAM
products use band='LM' while each geometry row's half='L' or half='M'
identifies the physical detector footprint. When a qualifying JIRAM row has
one of those physical halves, report that half in the existing bands list.
Do not infer an overlapping M half from a qualifying L half or vice versa.
Retain the legacy band fallback when no recognized half is supplied, retain
the existing observation-level deduplication and merge only qualifying bands.
The lead adds two fixed real-row-shape cases before this amendment is executed.

Run the fixed gate and existing science offline tests. The lead reproduces
the original failure, reruns the gate and offline suite, and verifies actual
mirror API examples after the executor delivers. Source-based false negatives
must remain distinguished from physical or observational limitations.

Judgment calls: an empty optional footprint does not invalidate separately
available measured corners; restore the existing fallback without altering
the approximate spherical-box method.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
