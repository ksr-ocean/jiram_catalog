# JunoCam expansion: archive and cost reconnaissance

Read the accepted parent `2026-09-08_junocam_expansion.md` and project rules.
Own only `docs/reports/junocam_expansion_inventory_2026-09-08.md`. Source, tests,
gates, fixtures, specs, indexes and all existing data are read-only. Small new
metadata samples may be stored only in the parent's expansion directory;
temporary computation files go in configured Lustre TMPDIR.

Measure the existing manifest/index schema, the distribution of preferred RDR
observations by pass/date, colour/methane/other acquisition types where known,
and full-resolution versus summed geometry where metadata permits. Inspect
small authoritative archive label/index/checksum/timing metadata as needed.
Do not download native images or rebuild any existing manifest/index.

Report candidate close-approach windows, observation counts and measured or
explicitly estimated byte costs for prime-mission passes (at least PJ3,5,6,8,
12,18,24,30,34; include other candidates if inexpensive). Measure overlap with
local JIRAM strip/pass/time coverage and available SPICE kernel date coverage.
Determine whether current manifest discovery is complete enough to select
these samples, whether versions/timing offsets are represented correctly,
and exactly how to restrict existing download commands to the chosen native
products without inadvertently downloading whole campaigns or overwriting
existing metadata. Identify shared-index/incremental-update hazards but do
not fix them. Separate retrieved facts from inferred date windows/costs.

Report must provide compact tables, reproducible read-only commands, file
paths, uncertainties and source URLs. Lead verifies key counts and chooses
the actual acquisition; the executor does not choose final science scope.

Judgment calls: reconnaissance only; retain raw version counts separately
from independent observations, and avoid assuming every campaign image is
close to perijove or scientifically useful.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
