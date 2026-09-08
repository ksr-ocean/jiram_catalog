# Accepted exact-product acquisition and measurement

Parent: `2026-09-08_junocam_expansion.md`. Lead decision after three independent
audits: acquire exactly the 194 selected rows of
`docs/reports/junocam_expansion_selection_2026-09-08.csv`, a read-only acquisition
oracle for the executor. This is 7,854,710,784 native bytes across PJ5/6/8/12/18/
24/30/34 (12/32/20/19/23/18/27/43 products respectively), within the inventory's
Jupiter closest-image ±90-minute windows. IDs, versions, URLs and label/native
MD5 hashes are explicit. The other 143 rows document exclusions from this batch.
Finite timing includes both nonzero applied corrections and zero/no-shift-needed.

Executor owns only mirror writes described here and an operational script/logs
under `<mirror>/junocam/expansion_2026-09-08/`. No repository/source/spec/gate/
policy edits. Do not download whole days, old versions, maps, methane or EDRs.
Do not modify existing PJ4/PJ58 records/products, JIRAM, or ground truth.

1. Verify selection against durable inventory and candidate-label timing audits.
   Use `load_manifest_files` joined by exact product_id, verifying volume/path/
   URLs, and existing `_mirror_kind` to download only selected labels then data.
   Maximum three download workers. Native writes remain in the normal
   `<mirror>/junocam/<volume>/DATA/...` structure. Resume verified complete files.
2. Verify all 194 sizes and label/native MD5 hashes against the oracle; record
   actual sizes/hashes and results as `acquisition_checks.json`. Never mark a
   mismatch successful. No source clearing at this stage.
3. After complete verification, serialize `build_index(root, selected_orbits,
   jobs=4)` then `build_quality(root, selected_orbits, jobs=2)`. These operations
   preserve other passes. Save per-product measurements and summaries in the
   expansion directory. Do not run geometry, strips or stacks in this assignment.
4. Run `tests/test_gate_junocam_expansion.py::test_acquired_products` (lead owns
   this gate). Report actual elapsed/resource costs, counts, failures, quality
   metrics and exclusions. The lead verifies again and adjudicates eligibility.

Lead has backed up prior indexes under expansion `baseline/` and records all
prior NetCDF paths/sizes/mtimes. User authorized the expansion; this specification
is its bounded operational scope, not another approval request.

Judgment calls: full-resolution RGB first; defer PJ3 kernel-number mismatch and
unsupported methane offsets; use native RDR with documented response drift,
without inventing calibrated I/F or a brightness-stability guarantee.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
