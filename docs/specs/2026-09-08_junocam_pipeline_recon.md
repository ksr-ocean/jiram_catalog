# JunoCam expansion: pipeline and verification reconnaissance

Read the accepted parent `2026-09-08_junocam_expansion.md` and project rules.
Own only `docs/reports/junocam_expansion_pipeline_2026-09-08.md`. All source,
configuration/policy, tests/gates/fixtures, specs and mirror data are read-only.
Use configured Lustre TMPDIR for any temporary inspection files.

Audit the actual manifest -> selective mirror -> index -> quality -> geometry
-> strip/stack -> API path for multi-pass expansion. Determine stage ordering
when new observations remain unassessed until explicit clearance. Find which
operations overwrite indexes/products, whether selected-orbit updates preserve
existing PJ4/PJ58 rows, how filenames/version identity and timing corrections
propagate, and how to batch without concurrent shared-index writes. Check
supported full-resolution products, SPICE/thread/process safety, memory costs,
resolution cutoffs and useful CLI controls. Inspect actual tests and propose
fixed acceptance checks without editing or weakening existing gates.

Give exact existing APIs/commands and smallest concrete repairs needed, if
any. Measure only cheap read-only operations; no native image download, product
build, GUI server or expensive statistics. Identify how new-pass images and
quality should be verified in the GUI and which stale counts/doc claims will
need updating. Lead writes execution specs and gates before any repairs.

Judgment calls: distinguish observed defects from hypothetical edge cases;
existing passing behavior and retained native/PJ4 products are regression
requirements, not disposable fixtures.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
