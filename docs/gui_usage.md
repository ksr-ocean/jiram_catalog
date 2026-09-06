# Using the catalog browser (`jiram-catalog gui`)

The GUI is a Panel application served from a cluster node and opened in a
browser on the laptop through an SSH tunnel. It draws products the
command line has already written -- the frame index and its geometry, the
region stacks, the strip library -- and writes nothing except its own
cache. There is no display on the cluster and none is needed: the server
speaks HTTP, the browser does the drawing.

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
(Ctrl-C on the node). The web socket origin check is disabled by
default so the tunnelled `localhost:5006` address is accepted.

## The three tabs

**Catalog** is every frame that sees the planet -- about 47,600 of the
113,000 (frame, band half) rows -- as boresight points, drawn with
datashader so that the filters redraw instantly. The sidebar filters are
orbit range, date range, band half, pixel size, emission angle, on-planet
fraction, dayside, latitude band, and, when
`index/trackability_frames.parquet` exists, same-pass revisit. Below
5,000 rows the individual points are overlaid with their metadata in the
hover. A box on the map is a selection; the selection table exports as
CSV, and its orbits go to the Poles tab or its latitude band to the
Strips tab. **Thresholds exclude rather than require**: a frame whose
emission angle the geometry engine could not fix stays on the map instead
of vanishing the moment a slider moves.

**Poles** views the region stacks under `<mirror>/regions/`. Choose one
from the list or type any path; the player steps through time, the
percentile sliders set the stretch, and the graticule (parallels every
2 deg, meridians every 30 deg) and the emission overlay come from the
stack's own coordinate arrays. Actions run in a background thread and
report where they wrote: a movie (`movie.write_movie`), a goflow triple
dataset (`export_goflow.export_stack`), or a PNG of the current step.

**Strips** is the strip library: a filtered table, a map of the strip
centres coloured by year, the strip itself with its graticule and
local-time contours (mask drawn as transparency, cursor readout of
latitude, longitude, local time and emission), and the statistics of the
current strip -- isotropic spectrum, the one-dimensional spectra along x
and along y, and the second- and third-order structure functions.

## What it writes, and what it does not

Everything the GUI writes lands under `<mirror>/gui_cache/`: per-strip
statistics as `stats_<strip_id>.nc` (the Dataset `stats2d.strip_statistics`
returns, so a later population run can read them), and, by default, the
movies, PNGs and goflow datasets under `gui_cache/exports/`. Those three
destinations are text inputs and can be pointed anywhere the user can
write. Nothing else under the mirror is touched, and no request leaves
the node.

A frame stack is 2 GB and is never loaded: the dataset is opened lazily,
one time step is read when the player moves, and the display stretch is a
strided subsample of a few steps. Opening the 294-step, 2.25 GB
`M_orbits4_frame.nc` costs about 280 MB of resident memory and under a
second, and each step after that reads in about 0.2 s.

## Saving a session

The sidebar's **Save session** button writes the filters, the selection
and the current stack and strip as a small JSON file
(`CatalogState.to_json`); dropping that file back into the file input
restores them. A session is worth committing next to a figure: it is the
selection the figure was made from.
