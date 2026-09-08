# Accepted JunoCam multi-pass expansion

Status: accepted by the owner's request to expand the scientifically screened
JunoCam dataset beyond PJ4, explicitly using subagents. The existing exclusion
requirement persists: affected or unassessed instrument/radiometric data must
not become viewable. Earlier authorization to push completed work also persists.

## Goal and measured starting state

Deliver useful mapped JunoCam observations from multiple additional perijoves,
with actual image acquisition, label/quality/navigation processing, explicit
source-based clearances and GUI-visible products. Merely expanding metadata or
starting an unattended job does not complete this task. Default scientific
priority is JIRAM overlap plus multi-pass texture comparisons; the owner may
steer that priority while reconnaissance proceeds. Do not promise a regular
motion triplet before measuring cadence, geometry and common coverage.

At the start, the manifest has 82,668 product records in orbit buckets 0–80;
labels/indexes cover PJ4 and PJ58; local indexed pixels, quality and geometry
cover 122 PJ4 processing products. The GUI exposes 72 preferred eligible PJ4
observations. Policy records `legacy_measured_orbits: [4]` and no additional
clearances. Existing published/derived PJ4 evidence is preserved.

## Phase 1: independent reconnaissance

Companion specs assign three disjoint reports: archive inventory/cost, primary
instrument evidence, and pipeline/incremental-write/validation audit. Reports
must provide measured facts, source links and explicit unknowns. They do not
authorize source edits, broad quality clearances or native image downloads.
Small metadata downloads are allowed only inside
`<mirror>/junocam/expansion_2026-09-08/`; caches/logs/runtime files use configured
Lustre TMPDIR, never system `/tmp` or another user's scratch directory.

The lead selects the acquisition list and resource budget after those facts,
records exact products/pass/date limits and writes fixed acceptance gates before
dispatching execution. Prefer existing processing/CLI paths when sound. If a
pipeline defect prevents safe incremental expansion, specify and repair that
bounded defect before running the expansion. Application source, policy and
existing mirror products stay unchanged during reconnaissance.

## Completion requirements

- More than one newly added pass, with a target of at least three scientifically
  useful additional passes unless measured source/quality evidence rules them
  out. Any narrower result requires a documented factual reason, not convenience.
- Record candidate and accepted/rejected/unassessed IDs, native processing
  versions, archive URLs, timing, bytes/checksums where available, instrument
  evidence, quality metrics, geometry and map resolution.
- Preserve exclusion semantics; new clearances identify observations/products
  and cite an inspected evidence record. Clean appearance alone is insufficient.
- Build useful strip products and appropriate existing region grids from the
  accepted images; assess population grouping and available temporal coverage.
- Verify existing PJ4 behavior and new-pass catalog, images, physical bands,
  quality enforcement and derived product access against pre-written gates.
- Update coverage documentation, the guide's dated local-data snapshot and
  build/open-item records to describe the expanded sample and actual limits.
  Commit source/docs as needed and push normally; native data remain outside Git.

Judgment calls: expand a bounded scientifically selected sample first, with
the acquisition scope determined from measured costs and evidence; preserve
the current physical models and coordinate conventions; keep the decision to
download at scale and final scientific eligibility interpretation with the lead.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
