# Using the catalog browser (`jiram-catalog gui`)

The GUI is two halves of one process: a small FastAPI backend
(`src/jiram_catalog/api/`) that answers Arrow, JSON and PNG under `/api`
and reads mirrored observations and derived products, and a React
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

## The five views

**Explore** finds mapped observations with task presets, density/footprint
maps, coverage-based latitude filters and a provenance inspector.
**Time series** displays region frames, snapshots and accumulating sweeps,
with physical-band movies and readiness-checked exports. **Image library**
shows per-pass strips and individual/population intensity statistics.
**Compare** provides linked split/blink viewing, shared masks and measured
registration with explicit uncertainty assumptions. **Coverage** searches
archive metadata and shows processing stages, source age, exclusion reasons
and reference links.

Use the [illustrated guide](gui_guide.md) for a walkthrough of all five views,
with current screenshots, controls and worked workflows. The
[scientific workflow and capability matrix](research_workflow.md) explains
interpretation and validation limits.

## Products and caches

Selections, display caches and job records stay under `<mirror>/gui_cache/`.
New research caches and rendered movies use `gui_cache/research/`;
velocity-model exports use `gui_cache/exports/`. GUI export destinations must
stay inside that exports directory, and an explicitly named destination must
be empty. Default export identities include source/policy/settings so old
realization files cannot mix with a new filtered dataset. Region builds write
under `<mirror>/regions/`. Published ground truth is read-only.

Stacks are opened lazily; image display and movie normalization read one
physical frame at a time. Population analysis is bounded to 100 requested
strips and records independent-pass weighting. Figures and recipes download
through the browser. Reference links open external sites; ordinary image
viewing uses the local mirror.

JunoCam failure exclusion is enforced before pixel access, statistics and
jobs. The default catalog requires an eligible preferred observation version.
Excluded and unassessed data can be inspected as metadata only. Existing
JunoCam movies from before this policy are not served; new movies carry the
filtered source identity and chosen band/normalization in their cache key.

## The selection tray

There is no "save session" file in this version: the working selection
and the filter toolbar persist automatically in the browser's own
`localStorage`, so a reload lands you back where you were on that
browser. What you save explicitly is a **named selection** -- the
tray's "Save selection" button posts it to `/api/selections`, which
writes the small JSON file above and makes it visible to anyone else
pointed at the same mirror; that is the artifact worth committing next
to a figure, since it is the selection the figure was made from.
