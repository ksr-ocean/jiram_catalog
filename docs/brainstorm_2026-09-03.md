# JIRAM catalog tool — brainstorm (2026-09-03)

Inputs: three subagent reports in `reports/` (local folder inventory,
goflow_rnd contract, PDS archive research), spot-checked by the lead.

## 1. What we actually have and what the archive actually is

**Local folder** = supplementary data of Ingersoll et al. 2022, *Nature
Astronomy* 6, "Vorticity and divergence at scales down to 200 km within
and around the polar cyclones of Jupiter" (DOI 10.1038/s41550-022-01774-0).
Perijove 4 (2017-02-02), M-band (4.78 µm), north pole (80–85°N), already
map-projected by JPL VICAR (JIR2VIC → APIMAP6E) to 3000×3200 float32 at
15 km/px. Four groups (n01–n04) ≈ 8 min apart; each group is 12 sub-frames
(a–l) ≈ 30 s apart = one frame per Juno spin (2 rpm). The 68 `.tp4` files
are JPL TRACKER4 cloud-tracking output: 114k vectors, pixel displacement
and m/s, from n01↔n03 and n02↔n04 (~16 min baseline). The `.xls` is a
radial wind profile of one cyclone.

Two defects in the local readers: hardcoded 12800-byte header (wrong for
`.tp4`, LBLSIZE=1728); `get_geo_bounds` does degree arithmetic on
projection-plane quantities. Neither matters for the new tool; both mean
the readers are not a foundation.

**Full archive** (PDS Atmospheres node): PDS4 bundle `juno_jiram_bundle`,
`data_calibrated/orbit01…orbit77`, plus legacy PDS3 `JNOJIR_xxxx`
volumes. Imager frames are 432×256, float32 radiance W/(m² sr), L-band
(3.45 µm) and M-band (4.78 µm). Spectrometer products are separate.
**Labels carry no geometry** (no lat/lon, footprint, emission angle),
only the list of SPICE kernels used. A 26 MB calibrated-inventory CSV
lists every product. Total image count and size: not established
(an "18 GB" figure surfaced but could not be reproduced). Perijove count
is 71–77 depending on page. First task must measure this.

**goflow_rnd** contract: 3 consecutive frames on a regular square-pixel
grid, constant dt, float32, per-frame valid mask, flat Cartesian, loggrad
input (log10|∇I| clipped and normalized), midpoint-velocity target.
Nothing planetary is implemented; the planetary plan is Saturn/Cassini
and names the workflow we need: inventory by filter/cadence/geometry,
reproject to planet geometry in System III, quantify navigation
uncertainty, run classical tracking alongside, label results by evidence.

## 2. The central design fact

A JIRAM frame is tiny (432×256) and the spacecraft moves fast at
perijove. The paper's 3000×3200 maps are **mosaics of twelve spin-frames
reprojected onto a fixed polar grid**. So "all snapshots of a region over
time as a movie" is not a sequence of images; it is a sequence of
**reprojected mosaics**, each built from the frames that fell in a short
time bin. Everything below follows from that: the catalog's core object is
the per-frame footprint on the planet, computed from SPICE, and the
region organizer is a reprojection-and-binning engine on top of it.

## 3. Proposed architecture (library + CLI first; GUI is a view)

    L0 acquire   mirror inventory CSV + all labels (small) → local index
                 fetch images lazily by query; idempotent, checksummed
    L1 index     one row per frame: product id, orbit, filter, start/stop,
                 sclk, exposure, path + computed geometry: sub-s/c lat/lon,
                 range, boresight intercept, footprint polygon (System III,
                 planetocentric), km/px at centre, emission/incidence/phase
                 at centre, on-planet pixel fraction.  Parquet/GeoParquet,
                 queried with DuckDB or geopandas.  ~1e5 rows: trivial.
    L2 geometry  spiceypy + Juno kernels (SPK, CK, FK, IK juno_jiram_v02.ti,
                 SCLK, LSK, PCK).  Per-pixel lat/lon/emission via sincpt on
                 the 432×256 ray grid, cached once per frame as a companion
                 NetCDF ("navigated frame").  Evaluate henrikmelin/
                 spacecraft_rs before writing our own.
    L3 regions   named selectors in YAML: polygon on the planet + optional
                 co-moving frame (cyclone centre track; zonal drift rate) +
                 projection (polar stereographic at poles; equirectangular
                 or local azimuthal elsewhere) + target km/px + filter +
                 max emission angle + min coverage.
    L4 tiles     for a region: select frames → group into time bins (e.g.
                 one spin-sequence ≈ 6 min) → reproject each frame onto the
                 region grid → mosaic per bin (with per-pixel emission-
                 angle weighting) → stack (T, y, x) + valid mask + per-bin
                 time, dt, mean emission angle.  This stack IS the goflow
                 input modulo a 3-frame windowing adapter.
    L5 products  movies per region per perijove (mp4 via ffmpeg);
                 goflow-ready NetCDF stacks; classical-tracking baseline
                 (phase correlation) for every stack, because the plan
                 already requires it and the paper's .tp4 vectors give us
                 a validation target for PJ4.
    L6 browser   deferred; see §5.

Repository: a new `jiram_catalog` package (uv, Python 3.12, torch-free),
with a thin adapter that emits goflow_rnd's NetCDF layout. Keep it out of
goflow_rnd so the catalog stays useful without the ML stack.

## 4. The objective gate for the whole pipeline

Reproduce the paper from raw PDS data: take PJ4 M-band frames, navigate
with SPICE, reproject to the paper's north-polar grid at 15 km/px, mosaic
the n01 group, and difference against `n01a…l.map`. Then run classical
tracking on n01↔n03 and compare to the `.tp4` vectors. Geometry errors,
projection errors, and tracking errors each show up in a different
place. This gate is RED today and defines done for L0–L5.

## 5. GUI: three options, one recommendation

(a) **No GUI.** CLI + notebooks + a generated static HTML catalog
    (thumbnails per perijove/region, links to movies). Cheapest; works
    over ssh; enough for one researcher.
(b) **Thin web app** (server-rendered plotting, run on a node with port forwarding):
    footprint polygons on a polar or cylindrical map, time slider,
    draw-a-region tool, "make stack / make movie" buttons. Cartopy's
    Globe accepts Jupiter's radii, so map projections come for free.
    Right size for a small group; ~1–2 weeks of work once L1 exists.
(c) **napari locally** for inspecting (T, y, x) stacks with vector
    overlays. Not a catalog; an excellent viewer. Zero build cost.

Recommendation: build L0–L5 first; (a) immediately as a byproduct of L5;
(c) for stack inspection now; decide on (b) after seeing the index,
because the GUI is a view over the index and its design depends on how
many regions and perijoves turn out to matter.

## 6. Decisions only Kaushik can make

1. Imager only, or index the spectrometer too? (Recommend imager only
   for v1; spectrometer rows can be added to L1 later.)
2. Regions first: north/south polar cyclones (paper territory, fixed
   frame) vs. GRS and mid-latitude jets (need co-moving frames).
3. Latitude convention (planetocentric vs planetographic) and target
   km/px (15 as in the paper, or native at closest approach).
4. Where the mirror lives on Lustre, once the size is measured.
5. Whether v1 must run on the cluster only, or also on a laptop
   (decides packaging and the GUI choice).

## 7. First milestone (proposed, ~1–2 days of subagent work)

"Index-only crawl": mirror the inventory CSV and every imager label;
build the L1 table without geometry; report real counts, per-orbit
counts, filters, cadence histograms, and total size. In parallel, stand
up the SPICE geometry engine on PJ4 only and validate centre lat/lon and
km/px against the paper's `.map` labels (CENLAT≈83°N, MPS=15,
SPACECRAFT_ALTITUDE=115378 km). Routing: crawl → Sonnet (class A/K);
geometry engine → Opus (class E, subtle); gates written by the lead.

## 8. Risks to name now

- Navigation accuracy: CK pointing errors alias directly into velocity.
  The paper did geometric control; we must quantify residuals (limb or
  feature re-registration) before trusting any goflow vectors.
- Radiance vs. clouds: M-band is thermal emission through cloud gaps
  (bright = clear); limb darkening varies with emission angle within a
  mosaic. The loggrad input helps; mosaic seams may still leave edges.
- dt selection: the plan flags midpoint-target validity against the
  decorrelation time; 30 s spin cadence vs ~8 min group spacing gives two
  natural dt choices, both should be produced.
- Scale unknown: everything above assumes the archive fits on Lustre;
  the first milestone measures it.
