# The GUI v2 front end: architecture, state, and how to extend it

The earlier GUI was a server-rendered application in which the server held the state, computed
the pictures, and pushed rasters to the browser; the browser was, in effect, a
screen. That arrangement is what produced the five failures the owner found in
his first test: a colour map change that did not apply, panels that stopped
repainting, a movie that rendered but was never shown, hover that did nothing
because the points had been rasterised away, and a zoom that changed the aspect
ratio. Each of those is a synchronisation failure between two copies of the
same state, and none of them is a bug that can be fixed once. GUI v2 therefore
moves the state and the rendering into the browser and leaves the server with
the two jobs it is uniquely good at, namely reading NetCDF and Parquet off the
Lustre filesystem and running long jobs; the API contract that divides the two
is `docs/specs/2026-09-06_api_contract.md`, and it is normative for both sides.

In this note we describe the resulting architecture, the single state store,
how each of the three views talks to the API, and the recipe for adding a
fourth. The user-facing walkthrough of v1 remains `docs/gui_guide.md`; the
vocabulary (band halves, latitude bands, stacks, strips) is unchanged.

## The shape of the application

The front end is a Vite build of React 18 and TypeScript in strict mode,
living in `frontend/`, with the production bundle committed under
`src/jiram_catalog/webapp/dist/` so that `jiram-catalog gui` serves the
application and the API from the same origin. There is no router and no
component framework; the three views are tabs of one page and the styling is
plain CSS modules. The dependency list is deliberately short: `zustand` for
state, `@deck.gl/core`, `@deck.gl/layers` and `@deck.gl/react` for the two
kinds of picture the application draws, `apache-arrow` for the two tables it
loads, and `plotly.js-dist-min` for the curves.

Two of those choices carry most of the design. The first is deck.gl's
`OrthographicView`, whose zoom is a scalar rather than a pair, so the aspect
ratio of a map or an image is locked by construction and v1's distorting zoom
has no expression here. The second is Arrow: `/api/catalog/frames.arrow`
arrives as one IPC stream of about 47,600 band-half rows and is split, in
`src/lib/catalogTable.ts`, into contiguous typed arrays (`Float32Array` for
the geometry, a `Float64Array` of epoch milliseconds for the times, plain
string arrays for the four text columns). Nothing in the catalog path ever
becomes an array of JavaScript objects; a filter is one pass over typed
arrays producing a `Uint32Array` of surviving row indices, and deck.gl is
handed packed binary attributes rather than accessor callbacks. That is what
makes a filter change repaint in a frame, and it is also why hover works:
the points are on the client, so they can be picked.

## One store, and what is in it

All state lives in a single zustand store, `src/store/store.ts`. It carries
the config from `/api/config`, the catalog columns and the index that maps a
`product_id|half` key to a row, the filters, the current filtered index array
and the projected positions, the working selection, the saved selections, the
current stack with its metadata and frame image, the current strip with its
metadata and statistics, the job list, a per-key loading map, and a toast
queue. Views are subscribers; they never hold a second copy of anything a
sibling view can change. The filters and the working selection are mirrored
into `localStorage` through `src/store/persist.ts`, whose reads and writes are
wrapped in `try`/`catch` so that a private window or a browser with site data
disabled degrades to a session that simply does not remember.

Every remote call goes through `store.run(key, work)`, which sets a loading
flag, runs the work, converts a failure into an error toast carrying the
contract's `detail` string, and clears the flag in a `finally`. The
consequence is that "shows a loading state and never freezes on a failed
request" is a property of one function rather than a discipline each view has
to keep. The API itself is wrapped once, in `src/api/client.ts`; no component
calls `fetch`.

The working selection is a `Set` of `product_id|half` keys rather than a set
of row indices, which matters for two reasons. First, a selection saved to
`/api/selections` and reloaded later must survive a catalog that has grown;
keys are stable where indices are not. Second, the tray's summary (frames,
orbits, latitude range, band halves) is derived from the keys through the
index on every render, so it cannot drift from what is actually selected.
Of course this costs a map lookup per key, which is nothing at the scale of a
selection a human makes.

It should be noted that the store also renders a hidden `<pre id="debug-state">`
element, `src/components/DebugState.tsx`, carrying
`{n_points, n_filtered, selection_n, view, stack_id, level, t, cmap, stats_visible,
instrument_filter, band, composite, n_footprints, strip_band, strip_composite,
norm, stretch_mode, strip_norm, strip_stretch_mode}` as JSON. Six of those
arrived with the instrument work of 2026-09-07 and four more with the
photometry work of the same day; `band`, `composite`, `norm` and `stretch_mode`
are the Poles viewer's, which is what the amendments' tests name, and the
strips viewer carries its own beside them rather than sharing fields whose
meaning would depend on which tab happened to be open. The two norms are the
wire spelling -- `lambert`, `minnaert:0.8`, `flat:64` -- so a test can compare
what the viewer believes with the query string it actually sent. v1 could not be tested from the outside because everything it did
happened inside a server-rendered canvas; the debug element gives the Playwright suite a
number to wait on instead of a screenshot to compare, and it is the reason the
end-to-end tests are assertions rather than smoke tests.

## Where the client-side and server-side filters meet

The catalog map is filtered in the browser and the three coverage charts are
filtered by the server, which is a deliberate duplication: the charts must
agree with the API, and the map must be instant. The two are kept honest by
sharing one parameter vocabulary. `src/lib/filters.ts` defines
`CatalogFilters`, `filterIndices` for the typed-array pass, and
`filtersToParams` for the query string, and the missing-value rule is written
once in both. Thresholds exclude rather than require, so a frame whose
emission angle the geometry engine never fixed stays on the map instead of
vanishing the moment a slider moves; the two exceptions, which drop rows whose
value is missing, are `on_planet_min` and the latitude bounds, the latter
because a frame whose boresight misses the planet has no latitude to be inside
a band. A latitude bound is applied on the client exactly when
`filtersToParams` sends it, viz. when it differs from the default, so the two
counts match. The table caption prints both, which turns any future divergence
into something the user sees rather than something they have to suspect. Two
smaller rules protect the same property: a summary response is kept only when
the filters have not moved on since it was asked for, so two quick filter
changes cannot leave the earlier answer on screen, and a failed summary drops
the number entirely rather than leaving the previous filters' count beside a
new client count, viz. the very disagreement the caption exists to expose.

The amendment of 2026-09-07 adds three filters to the same vocabulary, viz. an
instrument (JIRAM, JunoCam, or both), a band that a row must carry, and a
minimum quality tier, and each follows the rule the others already do: they are
equality or membership tests on a value a row either has or does not, and a row
with no tier at all is kept. The band is a membership test rather than an
equality one because a JunoCam row carries several bands at once, as the
`;`-joined `bands` column of `frames.arrow`; `src/lib/bands.ts` scans that
string for a whole token instead of splitting it, so a filter pass over 47,000
rows still allocates nothing. The quality filter defaults to `B` rather than to
`C` precisely so that the client and the server start out counting the same
rows: the contract hides tier `C` unless it is asked for, and a client default
of "show everything" would have disagreed with the server's summary on the
first paint.

## The three views

The Catalog view (`src/views/CatalogView.tsx`, with the map in
`CatalogMap.tsx` and the filter row in `CatalogFilters.tsx`) loads
`frames.arrow` once, projects the boresights with `src/lib/projection.ts` into
either cylindrical coordinates or the azimuthal-equidistant polar coordinates
of v1 (`rho = 90 - |lat|`, east longitude as the polar angle, the other
hemisphere sent to `NaN` so it draws nothing), and hands deck.gl a
`ScatterplotLayer` over packed buffers. Hover is GPU picking through deck.gl,
backed by a nearest-point search over the same typed arrays within about
fourteen pixels; the tooltip reports which of the two answered in its
`data-source` attribute, and under Chromium's software WebGL on this cluster
node it is GPU picking that answers. Box and lasso selection are a polygon
test in the typed arrays: the screen polygon is unprojected once through the
orthographic view's scalar zoom and the test is one pass over the filtered
indices. Colour is by orbit, year, pixel size, emission or instrument, categorical for
the first two and the last and a viridis ramp for the two physical quantities,
with a legend. Below the map,
the summary charts come from `/api/catalog/summary` and the table pages 200
rows at a time with checkboxes and a client-side CSV blob.

A JunoCam row is not a point. One JunoCam image is a swath tens of degrees
across, and drawing it as its boresight would be a lie about what the
instrument saw, so `frames.arrow` carries up to 64 vertices of the on-planet
outline in `fp_lon`/`fp_lat` and the browser draws the outline itself, as a
`PolygonLayer` beneath the points, stroked and filled at low alpha. Two things
make that more than a `map` over the vertices, and both live in
`src/lib/footprints.ts`. The first is the seam: an outline that straddles 0/360
is one shape on the sphere and two on a cylindrical map, and drawing it as one
polygon paints a band right across the picture, so the ring is cut at the seam
with the crossing latitude interpolated, which is what makes the two pieces
meet their edges at the same place. The second is the pole: an outline that
encircles a pole never closes in cylindrical coordinates at all, so the ring's
winding is accumulated and, when it comes to a full turn, the polygon is closed
over the top or bottom edge of the map, the hemisphere being read off the mean
latitude of the vertices. In the two polar projections neither problem exists,
the seam being a radius and the pole the origin, so there the vertices are
simply projected with the equator as a clamp rather than a cut. The outlines
are pickable on the same terms as the points, by the GPU when the pan tool is
out and by a bounding-box rejection followed by the same point-in-polygon test
the box tool uses when the selection overlay is covering the canvas; the points
are drawn above the outlines so that a boresight inside a swath, of which there
are many, is still the more precise target. Of course a footprint is two orders
of magnitude more geometry than a point, so `computeFootprints` stops at four
thousand outlines, which is a guard on the repaint budget rather than a policy
about what the user may see.

The Poles view (`src/views/PolesView.tsx`) lists stacks from `/api/stacks`,
opens one through `/meta`, and plays its frames. The colour map is the
important part. The server sends a grayscale PNG with the value in the red
channel and validity in alpha; the browser maps it through a 256-entry LUT
(`src/lib/lut.ts`, with gray, viridis, magma, inferno and cividis generated
from nine anchors each) into a DOM canvas, and that canvas becomes a
`BitmapLayer` through `createImageBitmap`. Changing the colour map is
therefore one pass over pixels already in memory and cannot fail to arrive,
which is precisely how v1's colour map control failed. The stretch sliders do
require a refetch, so they are debounced by 350 ms, and frames are held in a
twenty-entry LRU (`src/lib/lru.ts`) with the next two prefetched during
playback. The graticule from `meta.graticule` and the emission overlay are
each a further layer on the same bounds, the bounds themselves coming from the
`X-Bounds` header of the served PNG rather than from the stack's full extent,
since the server strides the image down. A rendered movie is a native
`<video controls>` element pointed at `/api/stacks/{id}/movie`, which the
backend serves with byte ranges; "Render movie" and "Export triples" post
their jobs and poll `/api/jobs/{id}` every two seconds, reloading the video
element when the render finishes.

The view offers three modes rather than one -- the snapshot per spin sequence,
the sweep filling in frame by frame, the raw instrument frames -- and the
mode selector is deliberately not a control over the open stack. The three
modes are three files that differ only in the `level` in their name, so
switching mode is opening a sibling, and the backend says which siblings exist
in each listing item's `siblings` map alongside a human `label`
(`stacks.attach_siblings`, keyed on region directory, band and orbit token, so
a sibling is always a stack the viewer can actually open). The front end keeps
`src/lib/stackModes.ts` able to resolve the same map from the naming
convention on its own, which is what makes the selector testable without a
server and what keeps it from going blank against an older backend. When a
mode has no file, the selector offers to build it: the same
`/api/stacks/build` job, with the region, band and orbit token read back out
of the current stack's own name, and the job's returned `stack_id` opened when
it finishes. The cumulative level is `stacks.accumulate_sequences`, a running
sum, count and best-emission per sequence in one pass, so the last step of
every sweep is bit-for-bit the snapshot `composite_sequences` would produce
and the first is the bare frame; the extra `seq_index`/`seq_n` coordinates it
carries are what the time label turns into "sweep k, frame i of n".

Not every instrument has three modes. A JIRAM sweep is a region filling in
frame by frame, so a per-sequence snapshot and an accumulating sweep are both
meaningful views of the same frames; a JunoCam image is already the whole
swath, so `frame` is the only level its stacks come in and `availableLevels`
hides the other two rather than offering to build files that cannot exist. A
stack that carries a `band` dimension gains a band selector beside the colour
map and, when RED, GREEN and BLUE are all present, an RGB composite as one more
entry in the same menu, the composite being a mode rather than a fourth band.
It is the one picture in the application the browser does not colour-map: it
arrives from the contract's `frame/{t}/rgb.png` already carrying three bands,
`ImageView` draws its bytes as they are, and the colour-map control is disabled
while it is on screen, since a lookup table over three channels is not a colour
map but a mistake. Each channel takes its own stretch pair, defaulting to
`meta.stretch[norm][band]`, and a "link bands" toggle drives all three from the
one pair on the toolbar; unlinking it puts three pairs on screen, which is what
a deliberate colour balance needs. It starts *off* for a JunoCam stack and on
for a JIRAM one: three colour strips of one camera differ in throughput by tens
of per cent and in illumination by where each of them crossed the terminator, so
they do not share a stretch without tinting the picture, while a single JIRAM
band has nothing to link to. The stretches are remembered per band, so moving
between bands and back does not undo an adjustment.

### Illumination, which is not a property of Jupiter
The photometry amendment of 2026-09-07 puts an **Illumination** selector beside
the colour map in both viewers -- None, Lambert, Minnaert with a `k` slider from
0.3 to 1.2, Flatten with a sigma slider from 8 to 256 pixels -- and a
Linear/Asinh toggle for how the display range is laid over the eight bits. The
selector is not a cosmetic control. JunoCam measures reflected sunlight, so
what dominates a swath is the cosine of the solar incidence: the first orbit-4
polar stack had band medians of 6 to 181 DN against 99th percentiles of 1,100
to 2,800, which is a stretch that saturates the whole dayside to show a
terminator nobody asked about. The choice travels to the server as
`norm=` on `frame/{t}.png`, `frame/{t}/rgb.png` and a strip's `image.png`, and
on to `stats?norm=` as well, because a spectrum of a limb-darkened swath and a
spectrum of the corrected one are two different measurements and the panel must
never show one under the other's label.

The selector is initialised from `meta.norm_default` -- `lambert` for JunoCam,
`none` for JIRAM, which measures its own thermal emission and for which a
cosine correction means nothing -- and it offers only the models
`meta.norm_options` says the product can answer for, since Lambert and Minnaert
need a per-pixel incidence angle that a stack built before the amendment does
not carry. Changing it re-reads every stretch slider from
`meta.stretch[norm][band]` rather than carrying the old numbers over: dividing
by `cos(i)` at 80 degrees is a factor of six, and keeping the raw limits under
the corrected picture is how the first Lambert view of the orbit-4 stack came
out white.

The Strips view (`src/views/StripsView.tsx`) is the same viewer over
`/api/strips/{id}/image.png`, with local-time contours as a second path layer,
beside a filtered table of `strips.arrow` and a small map of strip centres
coloured by year. Latitude and epoch filter as overlap tests, matching
`strips.load_strips`, while resolution, valid fraction and dayside are
thresholds on the strip's own scalar. The statistics panel draws three Plotly
figures from `/api/strips/{id}/stats` -- the isotropic spectrum on log-log axes
annotated with the wavelength range it spans, the two one-dimensional spectra
along x and y, and the structure functions with S2 on a log axis and the
signed S3 on a linear secondary axis -- and it starts hidden behind the
toolbar's "Show statistics" toggle, whose state lives in the store, is mirrored
into `localStorage`, and gives the image viewer the panel's height whenever the
figures are away. A JunoCam strip holds several bands in one file, so the table
gains an instrument filter and a band filter whose options come from the strips
actually loaded, and the viewer gains the same band selector the Poles viewer
has; the band on screen is also the band the statistics are computed for, which
the contract exposes as `stats?band=` and the panel's headings name, so a
spectrum is never quietly the wrong filter's. The strip composite, however,
takes a different route from the stack's. The amendment gives a strip
`image.png?band=` and no composite endpoint of its own, so the three band
images are fetched and combined in the browser (`src/lib/rgb.ts`), with the
alpha channel the union of the three validity masks rather than their
intersection, since an edge pixel that one filter saw and another did not is
real data and intersecting would eat a band off every edge where the three
swaths do not quite overlap. Both routes end at the same object, viz. one RGBA
buffer with a band per channel, which is why `ImageView` needs one `composite`
flag and not two code paths.

The selection tray, `src/components/SelectionTray.tsx`, is the organising
object and is always visible. It counts the working selection by instrument as
well as by orbit, latitude and band, and its build dialog now chooses the
instrument first, because the contract's build endpoint takes a single `band`
for a JIRAM stack and a list of `bands` for a JunoCam one and the two do not
offer the same levels; choosing the instrument therefore rewrites the rest of
the form rather than leaving controls on screen that the job would ignore. v1 had "Send to Poles" and "Send to Strips"
buttons whose effect was invisible, so the user could not tell what had been
sent or that anything had; the tray replaces the metaphor with a permanent
column showing what is in the selection, a name field, save and load against
`/api/selections`, and the two actions that consume a selection, viz. building
a stack from it (which saves the selection first and passes its `selection_id`
to `/api/stacks/build`) and filtering the Strips view to its orbits.

## Adding a view

The recipe is short because the store does the work. Add the response types to
`src/api/types.ts` and the calls to the `api` object in `src/api/client.ts`;
add the state and its actions to the store, routing every request through
`run` so the loading and error behaviour comes for free; write the view under
`src/views/` as a subscriber, reusing `ImageView` for anything raster and
`Plot` for anything with curves; add a tab entry in `src/components/App.tsx`,
where all three views stay mounted and only the active one is displayed,
because unmounting is what made v1's panels stop repainting; and, if the view
introduces state a test needs to see, add the field to `DebugState.tsx` and
assert on it. Pure functions go in `src/lib/` with a vitest file beside them
in `frontend/tests/`; anything that needs a browser belongs in
`frontend/e2e/`, which runs against `E2E_BASE_URL`.

## What the tests cover, and what they cannot

The unit suite covers the projections including the antimeridian and the
hemisphere masks, the latitude-band binning against the trackability edges,
every catalog filter once including both missing-value rules, the query-string
mapping, the polygon tests, the LUT endpoints, and the selection store
including a save round trip against a mocked `fetch`. The instrument work adds
three groups to it: the seam splitting, viz. an outline that misses the seam
left as one polygon, an outline that straddles it cut into two that meet the
edges at the interpolated latitude, and an outline that encircles a pole closed
over the top of the map with its two corner vertices, together with the
degenerate cases (fewer than three usable vertices, a NaN, a repeated closing
vertex) and the polar projections, where the equator is a clamp; the instrument,
band and quality filters against a fixture that now carries two JunoCam rows,
one of them multi-band and tier `B`, including the whole-token band test that
keeps `RED` from matching `INFRARED`; and the composite state, viz. which
stretch pair each channel receives linked and unlinked, what a band switch does
to the stretch on the toolbar, and the channel-wise composition with its union
alpha. The end-to-end suite
runs headless Chromium against a live backend and asserts on the debug
element and the DOM: the catalog draws at least forty thousand points, a
filter reduces the count and the server agrees with it, hovering a dense area
after zooming to it produces a tooltip with a product id, a box drag increases
the selection count, opening the per-sequence stack sets `stack_id` and loads
its first frame, switching mode to the instrument frames changes both
`stack_id` and `level`, the accumulating sweep reads out
`sweep k, frame i of n` and follows the slider, changing the colour map
changes both `cmap` and a sampled pixel of the colour-mapped canvas, the movie
element reaches `readyState >= 1`, and the strip statistics render as Plotly
figures. `e2e/instruments.spec.ts` adds the JunoCam half: the instrument filter
reduces `n_filtered` and raises `n_footprints` above zero while the server's
count still agrees with the client's, the band menu offers RED and not M once
JunoCam is chosen, hovering a footprint produces a tooltip naming the product
and the instrument, the legend names both instruments when colour is by
instrument, a JunoCam stack shows its band selector and only the `frame` mode
and, switched to RGB, sets `composite` true, marks the canvas
`data-composite="true"`, disables the colour maps and changes the sampled
pixel, and a JunoCam strip computes and plots its statistics for GREEN. Each of
those skips itself when `/api/config` reports `counts.junocam_images == 0`,
which is not a courtesy but the point: the gate runs this file against whatever
the mirror holds, and a test that fails for want of data says nothing about the
code.

There is no GPU on the cluster node, so all of this runs on Chromium's
SwiftShader implementation of WebGL 2, which deck.gl accepts and which does
perform GPU picking correctly; the pixel assertion samples the DOM canvas that
holds the colour-mapped image rather than reading back the WebGL drawing
buffer, both because it is the honest object to sample (it is the image the
user is looking at, before compositing) and because it does not depend on
`preserveDrawingBuffer`. Population statistics across a filtered set of
strips, tracking-vector overlays, and figure export as SVG remain unbuilt and
are tracked in `docs/open_items.md`.

## Design intent

The original design note (2026-09-05, superseded by this document)
sketched the GUI before either implementation existed, and most of it
survived the rewrite exactly because it was never about which framework
rendered it: three tabs over two regimes (Catalog as the global overview and
selection surface, Poles for the time-stack regime, Strips for the
per-pass library), one shared state object so the tabs cannot disagree
with each other, long operations run as background jobs with progress
rather than blocking a request, and every write confined to
`<mirror>/gui_cache/` plus files the user explicitly exports. v2 keeps
all of that; what changed is where the state and the drawing live, for
the reasons above. `zustand`'s single store is that one shared state
object, realised in the browser instead of on the server; the job pool
in `src/jiram_catalog/api/jobs.py` is the "worker thread with a progress
indicator" the note asked for, polled instead of pushed; the selection
tray is the note's cross-tab selection made into a permanent, visible
object instead of a "send to" side effect.

Three pieces of the original intent did not make it into this version
and remain open rather than abandoned (`docs/open_items.md`):
population statistics and a bicoherence map for a filtered set of
strips, a tracking-vector overlay on the Poles viewer, and a "save
figure" export to SVG (CSV and the stats JSON download cover the raster
and numeric cases, but not a redrawn vector figure). None of the three
was cut for a v2-specific reason -- they were deferred from the very
first version for the same reason, running out of first-version scope
before running out of design -- and the note that designed them is
folded into this paragraph now that the document itself is gone.
