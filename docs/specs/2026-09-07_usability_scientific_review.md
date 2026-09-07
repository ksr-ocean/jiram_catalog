# Usability and scientific-value review

Status: review scope authorized by the owner's 2026-09-07 request; proposed
implementation changes require later acceptance and their own specs.

## Goal

Orient to the repository, evaluate organization, interface usability and
aesthetics, and record prioritized design recommendations. Investigate in
particular whether the JunoCam component is usable relative to its archive,
data characteristics, and the owner's cloud-tracking/optical-flow projects.

## Scope and read-only boundary

Deliver this spec, `docs/reports/usability_scientific_review_2026-09-07.md`,
review screenshots under `docs/reports/figures/usability_2026-09-07/`, and
links from `docs/README.md` and `docs/open_items.md`.
Application code, dependencies, committed front-end bundles, gates,
fixtures, other specs, published ground truth, and downstream projects are
read-only. Inspect existing mirror products without rebuilding them.
Browser GET requests may populate the existing API cache under the mirror's
`gui_cache/`; do not submit builds, exports, or saved-selection mutations.
Temporary review scripts and browser profiles use the configured Lustre
runtime temporary directory. Stop the review's local server afterwards.

## Method and decisions

Read the required project guidance first. Inspect code and current products,
exercise existing browser views on a compute node when available, and record
actual viewport sizes. Search primary archive/instrument sources for the
JunoCam assessment. Distinguish live observations, code findings, external
evidence, design opinions, and unverified scientific claims. Preserve settled
geometry and processing decisions; recommendations do not reopen acceptance
without new evidence. Prioritize frame selection, velocity retrieval, and
population statistics because these are the documented project goals.

## Validation and report

No code or test changes are required for this assessment. Check changed-file
scope, local Markdown links, captured browser evidence, and citations for
external claims. Do not describe an inspection as a new geometry validation
or a passing scientific gate. The report must include evidence and limits,
JunoCam readiness by use case, strengths, actionable recommendations with
priorities and completion criteria, and judgment calls/ambiguities with the
options considered. End the final response with the judgment calls.

If any part of this spec is ambiguous or underdetermined, do NOT choose an
interpretation. Stop, list the ambiguities and the options, and make no
further changes. Also list every judgment call you made, however minor, at
the end of your final message.
