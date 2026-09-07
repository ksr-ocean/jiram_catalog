# Accepted usability and scientific-workflow implementation

Status: accepted scope. On 2026-09-07 the owner accepted the review findings
and explicitly requested implementation of all D01–D12 recommendations,
with exclusion of JunoCam data affected by instrument/radiometric failures
and an assessment of the PDS calibrated collection. This is the execution
specification of that accepted scope, not another approval request.

## Boundaries and ownership

Keep the established physical models, latitude/projection conventions,
ground-truth gates and fixtures intact. Do not modify published data or the
downstream optical-flow project. Preserve existing mirrored source products.
New research samples: `<mirror>/junocam/calibration_review/`; new interactive
products/recipes: `<mirror>/gui_cache/research/`; updated derived views filter
old products in memory without destroying archived versions. No bulk image
mirroring is required. Runtime temporary files stay in configured Lustre TMPDIR.

The catalog executor owns the catalog/policy spec; the science executor owns
the science spec; the frontend executor owns the frontend spec. The lead owns
app/router and export-job integration, CLI/dependencies if needed, gates,
research sampling/interpretation, documentation, final verification, and
rebuilding/committing generated assets. No executor changes another's files.

## Acceptance and validation

D01–D12 map to the three companion specs and the calibrated-data report.
All must be accounted for in the build record. Capabilities requiring data
must have honest empty/unassessed states, not fabricated results. Original
versions remain available as provenance, while excluded instrument-failure
data is not viewable through ordinary image/analysis endpoints. A user may
inspect exclusion metadata, not reveal prohibited pixels via a quality toggle.

Lead writes `tests/test_gate_review_catalog.py`,
`tests/test_gate_review_science.py`, and `frontend/e2e/review.spec.ts` before
execution; executors treat these and all existing gates/fixtures as read-only.
Run focused gates, `JIRAM_SKIP_GATES=1 uv run pytest -q`, frontend unit/type/build
checks and a live browser suite. Validate at 1366x768 and 1440x900, preserve
screenshots and report limitations. Documentation records current capability,
source/processing distinctions, and validation scope. New module APIs and UI
contracts are set in the companion specs. A final local commit includes the
rebuilt bundle and changes from this accepted work, without publishing.

Judgment calls: the earlier review's explicit proposals are now accepted;
implementation detail is delegated within this scope. Exclusion is enforced
at data access as well as in controls. Learned products may be inspected but
are not silently substituted for original observations or labeled wind truth.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
