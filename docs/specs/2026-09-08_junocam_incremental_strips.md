# Accepted safe incremental JunoCam strip builds

Accepted as a necessary bounded repair under the multi-pass expansion request.
Executor owns only `src/jiram_catalog/junocam/strips.py`; lead owns this spec and
`tests/test_gate_junocam_incremental.py`. All other files, gates, fixtures,
physical algorithms and published/mirror data are read-only to this executor.

Measured defect: `update_index` currently replaces all JunoCam rows in a pass
and deletes omitted indexed files and every unindexed NetCDF in that directory.
A partial build or another band selection therefore destroys earlier products.
`_strip_task` also validates its index row after replacing the product, and does
not close its dataset on error. Individual `write_strip` uses atomic replacement.

Implement these exact semantics without changing signatures or projections:

- `update_index(mirror, rows, orbits)` upserts by `(instrument, strip_id)`.
  Incoming rows must be JunoCam, have unique strip IDs and belong to `orbits`;
  reject invalid input before any index write. Preserve every omitted row and
  every file. Remove both deletion loops. Empty input preserves an existing
  index byte for byte; without an index, write the normal empty schema.
- `_strip_task` validates `index_row` before `write_strip`. Close the created
  dataset in `finally`, including metadata or write failures. Do not report a
  successful row until the atomic file write succeeds.
- Existing product paths encode the product ID but not its band variant. Before
  writing over an existing path, read its physical `band` coordinate and reject
  a different set of bands with an explicit error containing `band`. Identical
  sets, including a different order, may replace the product. A malformed or
  unreadable existing product must fail safely. Do not invent variant IDs.
- Keep command-level index writes serialized operationally. Global locking,
  geometry changes and transaction machinery are outside this repair.

Validation: lead's gate must first fail against the old implementation, then
pass. It checks partial and empty upserts, unrelated JIRAM and same-pass data,
unindexed-file preservation, invalid-input rejection, prewrite metadata failure,
band collision protection and dataset closure. Run existing offline strip tests;
lead runs the offline suite. Report scope and actual outcomes.

Judgment calls: retain product-ID naming and fail on band collisions, rather
than introduce product variants; make incremental builds additive and require a
separate explicitly scoped operation for future pruning.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
