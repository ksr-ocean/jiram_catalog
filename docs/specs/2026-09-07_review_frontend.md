# Scientific workspace frontend

Accepted under `2026-09-07_review_implementation.md`. Implements all D01–D12
browser surfaces, focusing D06–D10. Backend contracts are in companion
`review_catalog` and `review_science` specs; read them before starting.

## Owned files

`frontend/src/**`, `frontend/tests/**`, `frontend/e2e/**` EXCEPT the lead's
read-only `frontend/e2e/review.spec.ts`; `frontend/README.md` if necessary.
Do not change package.json/lockfiles, Python, gates, fixtures, specs or built
dist. Lead runs final build and commits generated bundle. No image generation
needed: scientific imagery comes from the existing API.

## Interface requirements

Preserve internal tab IDs/test hooks/API compatibility. Visible names Explore,
Time series, Image library; add Coverage and Compare workflows. Calm, spacious
scientific desktop layout: collapsible selection tray and viewer metadata,
14–16px primary text, restrained borders, meaningful hierarchy. Image minimum
height >=300px even when statistics open; scroll the view rather than crush
it. At 1366x768 essential controls remain usable, no page horizontal overflow.
Dense controls go into labelled expandable advanced groups. Main image can
fit valid data, graticule density selectable and axes say planetocentric lat,
east longitude. Keep scientific raster bounds/origin intact.

Selection remains persistent; tray button shows count when collapsed. Rename
Show in Strips to state orbit-only effect. Human labels for products/regions,
retain exact IDs in inspector. Catalog presets: repeat cloud views (JIRAM
revisit only), polar morphology, single-pass texture, cross-instrument context.
Changing to JunoCam clears/hides L/M half and JIRAM-only revisit controls;
show trackability as unassessed, never a negative. Latitude search defaults
to footprint lat-range overlap with explicit boresight alternate, keep server
summary query in sync. Add region rectangle spatial selection by polygon
intersection rather than boresight only where supported by existing tools.

Map broad view defaults coverage-density representation with a count legend;
zoomed/selected/hovered observations reveal outlines, optional all-outlines
mode. Selected swaths remain distinct over density. Clear geographic labels
and legend identify what size/colour encode. Density can be computed in
client grid using existing layers, no new dependencies. Expose archive
metadata browsing through Coverage, not false pixels for unprocessed rows.

Inspector opens details for both instruments: source and label links, eligible
thumbnail, rationale, versions/observation identity, quality reasons,
processing/provenance. Prohibited/unknown instrument quality is metadata only,
no override switch. Coverage tables distinguish archive/labels/pixels/geometry/
quality/products and timestamps; can search by pass/identifier using archive
endpoint. References panel links to Mission Juno maps and calibrated PDS
collection with derived/reference caveats from API.

Export: fetch readiness when stack/band/norm changes. Explain no valid triple,
unique count, cadence/common coverage/units; disable export when not ready.
Send chosen physical band and norm to existing export endpoint. RGB needs an
explicit analysis band; do not quietly export RED for an RGB display. Preserve
movie controls but disable with an honest reason if not supported for selected
instrument/processing; coordinate lead for movie job support.

Compare: two chosen existing stack frames or library strips, synced camera
state/pan/zoom, split and blink mode, locked/independent stretches, common-mask
overlay (compute from loaded same-grid masks only), time separation, sample
resolution and backend registration/readiness from `/api/science/compare`.
Make incompatible-grid/unknown navigation explicit. Speed and optional
navigation-error input drive displacement/error display, not invented winds.
Expose existing vector overlays through vectors endpoint where status allows,
with scale legend and provenance. Cross-instrument candidate matching from
inspector using `/matches`, with actual approximation limits shown.

Statistics: user can select filtered library set for population; groups by
instrument/band/norm/resolution, independent observations/passes and uncertainty
shown with masks/seam diagnostics, fit-range controls and normalization
sensitivity comparison. Use `/population`; one unsupported group explains why.
Allow export of plotted figure SVG/PNG via Plotly.toImage (existing dependency),
numeric CSV/JSON and recipe including sources/settings/units. Never call
texture spectra kinetic-energy spectra. Named figure download controls remain
available even though Plotly modebar is hidden.

Accessibility: focus-visible styling, keyboard-operable rows, dialog focus
trap/restore, Escape close; real labels and errors/loading/empty states. Store
new preferences safely; don't erase prior view state on tab switches.

## Validation

Meaningful unit checks for filter agreement/identity/new state. E2E coverage of
new UI with existing live data, no source-product mutation required. Existing
old tests may expect visible names; update non-gate E2E semantics if renamed,
never weaken assertions. Typecheck/test locally, tell lead when ready to build.
Report every implemented D item and remaining integration concern. End with
judgment calls.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
