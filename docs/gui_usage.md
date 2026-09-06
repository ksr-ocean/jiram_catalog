# Using the catalog browser (`jiram-catalog gui`)

The GUI is two halves of one process: a small FastAPI backend
(`src/jiram_catalog/api/`) that answers Arrow, JSON and PNG under `/api`
and reads nothing the command line has not already written, and a React
+ deck.gl single-page front end (`frontend/`, built to
`src/jiram_catalog/webapp/dist/`) that holds all of the state and does
all of the drawing in the browser; the backend serves the built front
end at `/`, so there is one port to tunnel and no cross-origin story.
There is no display on the cluster and none is needed: the server
answers HTTP, the browser filters, projects, colour-maps and draws, so a
filter or a colour-map change is a local repaint rather than a request
that can silently fail to arrive (`docs/gui_v2_notes.md` has the full
architecture). The GUI writes nothing to the mirror except its own
cache and the files a user explicitly asks it to build.

## Serve and tunnel

Run the server on a compute node from an interactive allocation
(Expanse discourages running work on the login nodes; an interactive
node with tens of cores and 128 GB is the normal home for this tool).
On the node:

```
cd <repository>
uv run jiram-catalog gui --port 5006 --address 0.0.0.0 --no-browser
```

`--address 0.0.0.0` is needed on a compute node because the login node
must be able to reach the server over the cluster network when it
forwards your port; the default loopback binding only works when the
browser tunnel terminates on the same machine. Then, from your laptop,
in a second terminal:

```
ssh -N -L 5006:<compute-node>:5006 <user>@login.expanse.sdsc.edu
```

where `<compute-node>` is the name printed by `hostname` on the
allocation (for example `exp-2-45`). Open `http://localhost:5006` in
your browser. The server has no authentication, so while it runs any
process on the cluster network can open it; stop it when you are done
(Ctrl-C on the node).

## The three tabs

**Catalog** is every frame that sees the planet -- about 47,600 of the
113,000 (frame, band half) rows -- as boresight points, loaded once as
an Arrow table and held as typed arrays in the browser so that every
filter is a client-side pass over those arrays and hovering is real GPU
point picking rather than a query against a raster. The filter toolbar
is orbit range, band half, pixel size, emission angle, on-planet
fraction, dayside, latitude band, and, when
`index/trackability_frames.parquet` exists, same-pass revisit; three
coverage charts (frames per latitude band, per orbit, per month) are
computed server-side from `/api/catalog/summary` with the same
parameters, so the map's count and the charts' count cannot disagree. A
box or lasso drag on the map, or a checked row in the table, adds to the
**selection tray**; the tray's contents export as CSV or drive "Build
stack..." (regime 1) and "Show in Strips" (regime 2). **Thresholds
exclude rather than require**: a frame whose emission angle the
geometry engine could not fix stays on the map instead of vanishing the
moment a slider moves.

**Poles** views the region stacks under `<mirror>/regions/`, and can
also build a new one from a tray selection (`POST /api/stacks/build`).
Choose a stack from the list; the player steps through time, the
stretch sliders set vmin/vmax, and the graticule (parallels every 2 deg,
meridians every 30 deg) and the emission overlay come from the stack's
own coordinate arrays, served as a GeoJSON the browser draws with a
locked-aspect `OrthographicView`. The colour map is applied in the
browser through a 256-entry lookup table, so changing it is an instant
redraw of pixels already downloaded, not a new request. Actions --
render a movie (played back with a native `<video>` element once done),
export a goflow triple dataset -- run as background jobs polled from
`/api/jobs` and report where they wrote.

**Strips** is the strip library: a filtered table, a map of the strip
centres coloured by year, the strip itself with its graticule and
local-time contours drawn through the same colour-map viewer as Poles,
and the statistics of the current strip -- isotropic spectrum, the
one-dimensional spectra along x and along y, and the second- and
third-order structure functions, plotted with Plotly.

## What it writes, and what it does not

Everything the GUI writes lands under `<mirror>/gui_cache/`: saved
selections as `selections/<id>.json` (`POST /api/selections`), per-strip
statistics as `stats_<strip_id>.nc` (the Dataset
`stats2d.strip_statistics` returns, so a later population run can read
them), per-stack display-stretch and graticule caches as
`meta_<key>.json`, job records mirrored under `jobs/` so a restarted
server can still report what the last run produced, and, by default,
the movies, PNGs and goflow datasets under `gui_cache/exports/`. Those
export destinations are text inputs and can be pointed anywhere the
user can write. Nothing else under the mirror is touched, and no
request leaves the node.

A frame stack is 2 GB and is never loaded whole: the dataset is opened
lazily, one time step is read when the player moves, and the display
stretch is a strided subsample of a few steps computed once and cached.
Opening the 294-step, 2.25 GB `M_orbits4_frame.nc` costs about 280 MB of
resident memory and under a second, and each step after that reads in
about 0.2 s.

## The selection tray

There is no "save session" file in this version: the working selection
and the filter toolbar persist automatically in the browser's own
`localStorage`, so a reload lands you back where you were on that
browser. What you save explicitly is a **named selection** -- the
tray's "Save selection" button posts it to `/api/selections`, which
writes the small JSON file above and makes it visible to anyone else
pointed at the same mirror; that is the artifact worth committing next
to a figure, since it is the selection the figure was made from.
