# GUI design note — JIRAM catalog browser (draft 2026-09-05)

A single web application served from a cluster node (Panel + Bokeh +
HoloViews, with datashader for large point sets), opened in the user's
browser through an SSH tunnel. No desktop toolkit, no X forwarding.
Everything the GUI shows is a product the command line already makes;
the GUI is a view and a selector, and heavy work runs as background
jobs with progress bars.

## Three tabs, two regimes

### 1. Catalog (global overview and selection)
- Map of every frame that sees the planet (about 47,000 band-halves),
  as boresight points on a latitude-longitude map, with a polar-view
  toggle for each hemisphere. Points drawn with datashader, so filtering
  is instant; hover shows product id, time, pixel size, emission.
- Filters (sidebar): orbit range, date range, band (L/M), resolution
  class, maximum emission, minimum on-planet fraction, dayside only,
  latitude band, "has a same-pass revisit" (from the trackability
  table).
- Coverage panels: bar chart of frames per latitude band and per
  orbit, updating with the filters; a time line of passes.
- Selection: box or lasso on the map, or click rows in the table. The
  selection table lists frames with their metadata and can be exported
  (product list, CSV) or sent to the Poles or Strips tab.

### 2. Poles (time-series regime)
- Region chooser: north or south polar cap, size and km per pixel, or
  the paper's grid; orbit(s), band, level (individual frames or
  per-sequence composites).
- Open or build a stack (build runs in the background; the file is the
  same NetCDF the command line writes).
- Viewer: image with time slider and play button, percentile stretch,
  colour map, graticule overlay (latitude circles, longitude spokes),
  emission-angle overlay, sequence selector, frame metadata panel.
- Actions: render movie, export cadence-constant triples in the
  velocity-model layout, overlay tracking vectors when a vector file
  exists (published or ours).

### 3. Strips (statistics regime)
- Library table with filters: latitude band, epoch, resolution class,
  valid fraction, dayside fraction, band; a map of strip centres and
  outlines coloured by epoch or resolution.
- Strip viewer: image with graticule and local-time contours, mask
  shown as transparency, cursor readout of latitude, longitude, local
  time, emission.
- Statistics panel for the current strip: isotropic spectrum, along
  and across-track spectra, structure functions; for the current
  filtered set: population mean spectrum with standard error, and the
  bicoherence map, computed on demand and cached.
- Export: the filtered strip list, the population statistics as
  NetCDF, and a figure bundle.

## Behaviour shared by both regimes
- All state (filters, selection, current object) in one parameterised
  state object so views stay consistent and a session can be saved as a
  small JSON and reloaded.
- Long tasks (stack build, population statistics) in a worker thread
  with a progress indicator; results cached on disk under the mirror.
- Every plot has a "save figure" button (PNG, SVG) and every table a
  "download CSV".

## First version (what gets built first)
Catalog tab complete; Poles tab as a viewer for stacks that already
exist (building from the GUI comes later); Strips tab with the table,
map, viewer, and the per-strip statistics panel. Population statistics
and in-GUI builds follow once the first version has been used.

## Deployment
`jiram-catalog gui --port 5006` on a compute or login node; the user
tunnels with `ssh -L 5006:<node>:5006 <login>` and opens
`http://localhost:5006`. Panel serves one session per browser tab; the
catalog tables are loaded once per process and shared read-only.
