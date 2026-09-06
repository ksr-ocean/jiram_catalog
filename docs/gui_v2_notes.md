# The GUI v2 front end: architecture, state, and how to extend it

GUI v1 was a Panel application in which the server held the state, computed
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
`{n_points, n_filtered, selection_n, view, stack_id, t, cmap}` as JSON. v1
could not be tested from the outside because everything it did happened inside
a Bokeh canvas; the debug element gives the Playwright suite a number to wait
on instead of a screenshot to compare, and it is the reason the end-to-end
tests are assertions rather than smoke tests.

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
into something the user sees rather than something they have to suspect.

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
indices. Colour is by orbit, year, pixel size or emission, categorical for the
first two and a viridis ramp for the last two, with a legend. Below the map,
the summary charts come from `/api/catalog/summary` and the table pages 200
rows at a time with checkboxes and a client-side CSV blob.

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

The Strips view (`src/views/StripsView.tsx`) is the same viewer over
`/api/strips/{id}/image.png`, with local-time contours as a second path layer,
beside a filtered table of `strips.arrow` and a small map of strip centres
coloured by year. Latitude and epoch filter as overlap tests, matching
`strips.load_strips`, while resolution, valid fraction and dayside are
thresholds on the strip's own scalar. The statistics panel draws three Plotly
figures from `/api/strips/{id}/stats`: the isotropic spectrum on log-log axes
annotated with the wavelength range it spans, the two one-dimensional spectra
along x and y, and the structure functions with S2 on a log axis and the
signed S3 on a linear secondary axis.

The selection tray, `src/components/SelectionTray.tsx`, is the organising
object and is always visible. v1 had "Send to Poles" and "Send to Strips"
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
including a save round trip against a mocked `fetch`. The end-to-end suite
runs headless Chromium against a live backend and asserts on the debug
element and the DOM: the catalog draws at least forty thousand points, a
filter reduces the count and the server agrees with it, hovering a dense area
after zooming to it produces a tooltip with a product id, a box drag increases
the selection count, opening the per-sequence stack sets `stack_id` and loads
its first frame, changing the colour map changes both `cmap` and a sampled
pixel of the colour-mapped canvas, the movie element reaches `readyState >= 1`,
and the strip statistics render as Plotly figures.

There is no GPU on the cluster node, so all of this runs on Chromium's
SwiftShader implementation of WebGL 2, which deck.gl accepts and which does
perform GPU picking correctly; the pixel assertion samples the DOM canvas that
holds the colour-mapped image rather than reading back the WebGL drawing
buffer, both because it is the honest object to sample (it is the image the
user is looking at, before compositing) and because it does not depend on
`preserveDrawingBuffer`. Population statistics across a filtered set of
strips, tracking-vector overlays, and figure export as SVG remain unbuilt and
are tracked in `docs/open_items.md`.
