# Interpreting VICAR/MIPL Map-Projection Label Keywords in JIRAM APIMAP6E Output

**Scope.** Interpretation of the map-projection label keywords produced by the JIRAM
ground-pipeline tasks JIR2VIC → APIMAP6E (JPL MIPL, ca. 2019-2020), for the north-polar
example:

```
MPOX=3800.0 MPOY=3800.0 MPS=15.0 DU=-90.0 XOFF=0.0 YOFF=0.0 MPROJ=4
NL=3000 NS=3200 PXL_MINLINE=2 PXL_MINSAMP=139 NORTHANG=66.3493
CENLAT=83.1502 CENLON=305.733 MINLAT=79.8064 MAXLAT=84.7971 MAXLON=262.54
```
(a second file in the set has `MAXLAT=89.985`). Body: Jupiter, radii 71492×66854 km
(equatorial × polar), planetocentric latitude, east-positive longitude.

**Research constraint (logged for the record).** The primary intended sources —
`www-mipl.jpl.nasa.gov` (VICAR User's Guide, `vichelp` program-help pages for
MAP2/MAP3/APIMAP, and `mp_routines.pdf`) — were **unreachable during this session**:
the hostname `www-mipl.jpl.nasa.gov` no longer resolves (NXDOMAIN, confirmed against
both the local resolver and 8.8.8.8) and the bare `mipl.jpl.nasa.gov` (which does
resolve, to 137.78.248.69) refused connections on port 443. The Wayback Machine was
also unreachable from this tool. Google-cached search snippets confirm such pages
existed (e.g. `vichelp/nimscmm2_level2.html`, `vichelp/perslab_level2.html`) but no
snippet for MAP2, MAP3, or APIMAP itself surfaced, and none could be retrieved in
full. GitHub code search on `NASA-AMMOS/VICAR` requires an authenticated session/API
token not available here, so the `mp` library source could not be grepped directly.
**Consequence: no primary MIPL documentation for MPOX/MPOY/MPS/DU/MPROJ was located
or read.** Everything below not explicitly marked "documented" is inference from (a)
the numeric self-consistency of the label values, (b) the well-documented PDS3
MAP_PROJECTION object (a closely related, genuinely accessed standard), and (c) the
JIRAM/Juno literature. This should be treated as a starting hypothesis for an
engineer to falsify against actual VICAR source (`vos/p2/sub/mp*`, `vos/p2/apimap*` in
NASA-AMMOS/VICAR on GitHub) or a live MIPL system, not as verified ground truth.

---

## 1. Meaning of MPROJ, MPOX, MPOY, MPS, DU, XOFF, YOFF

- **MPROJ (projection code).** UNKNOWN precisely which integer maps to which
  projection name in MIPL's internal table — I could not retrieve that table. **Inferred
  (moderate confidence)** that `MPROJ=4` = polar stereographic, based on: (i) the
  JIRAM/Juno literature repeatedly and explicitly states that JIRAM's polar mosaics are
  produced in "polar stereographic projection" — Dinelli et al. 2017, *GRL* 44,
  "Preliminary JIRAM results from Juno polar observations: 1. Methodology..."
  (doi:10.1002/2017GL072929), and Adriani et al. 2020, *JGR Planets* 125,
  "Two-Year Observations of the Jupiter Polar Regions by JIRAM" (doi:10.1029/2019JE006098),
  both describe polar mosaics as stereographic; (ii) a polar stereographic projection is
  the standard MIPL/PDS choice for polar imagery and is consistent with a projection that
  is conformal (locally correct pixel scale) and can be centered exactly on the pole
  while still supporting a constant-km/pixel product over tens of degrees of latitude, as
  seen here. **This was not confirmed against a MIPL projection-code table**, so treat
  "4 = polar stereographic" as the working hypothesis, not fact — a value of 4 for
  orthographic or Lambert azimuthal equal-area cannot be ruled out without the table.

- **MPOX, MPOY (documented convention only by analogy; exact semantics inferred).**
  By analogy to the PDS3 `IMAGE_MAP_PROJECTION` object's `LINE_PROJECTION_OFFSET` /
  `SAMPLE_PROJECTION_OFFSET` keywords (see §5; these ARE documented — PDS3 Standards
  Reference, Map Projection object) — which give the (line, sample) at which the
  projection's mathematical origin (for a polar case, the pole) falls — **MPOX and MPOY
  are inferred to be the (sample, line) [X=sample, Y=line, per the X/Y-not-line/sample
  ordering suggested by the name] coordinates of the projection origin (the pole for
  MPROJ=4) in the pixel grid that this image's local raster is a cutout of.** Evidence
  for "cutout of a larger grid" rather than "origin inside this raster": `MPOX=MPOY=3800`
  while `NL=3000, NS=3200` — the nominal origin sample/line lies outside the 3000×3200
  raster extent, which only makes sense if MPOX/MPOY are expressed in the coordinate
  system of a larger reference/master grid (see PXL_MINLINE/PXL_MINSAMP, §4) rather than
  local file coordinates. Indexing base (0 vs 1) is **unknown** — VICAR arrays are
  conventionally 1-based, so 1-based is the safer default assumption, but this is not
  verified for this specific label set.

- **MPS.** **Inferred, high confidence**: pixel scale, "15.0" = 15 km/pixel, matching
  the stated 15 km/pixel product. For a polar stereographic projection this scale is
  normally quoted **at the point of tangency (the pole, or the projection's defined
  true-scale latitude)**, not at CENLAT — polar stereographic in the standard (Snyder)
  formulation is true-to-scale either at the pole (tangent case, scale factor k0=1 at
  90°) or at a specified standard parallel (secant case, k0<1 at the pole). Which variant
  MIPL's `mp` library uses (tangent-at-pole vs. secant-at-a-standard-parallel) is
  **unknown** without the `mp_routines` documentation; this matters at the ~0.1-1%
  level for exact reproduction and should be checked against actual pixel positions of
  two known lat/lon control points if exactness is required.

- **DU.** **Unknown, low-confidence inference only.** Given that `CENLAT` already
  supplies the projection-center latitude, DU=-90.0 is unlikely to duplicate that. Two
  candidate readings, neither confirmed: (a) an azimuth/rotation parameter distinct from
  NORTHANG — e.g. a fixed grid-definition angle (orientation of the projection's native
  X-axis relative to the central meridian) rather than the per-image "north-up" angle
  that NORTHANG appears to encode; or (b) a scale-direction or "DU/DV" per-axis pixel
  step sign convention (the name pattern "DU" suggests an axis differential, as in
  du/dv used in some MIPL geometric-transform code, though I have no source confirming
  this for `mp`/APIMAP specifically). I could not disambiguate; flag as an open item for
  the engineer to check against `mp_routines.pdf` or the VICAR source directly.

- **XOFF, YOFF (=0.0 here).** **Inferred**: secondary sample/line offsets applied on top
  of MPOX/MPOY, analogous to a sub-pixel or additional-window offset; zero in this
  dataset so they don't constrain the interpretation further. Not documented from a
  primary source in this session.

## 2. Forward/inverse (line, sample) ↔ (lat, lon) formulas

**No MIPL-specific formula was retrieved** (mp_routines.pdf unreachable). The formulas
below are the **standard polar stereographic equations for a spherical or ellipsoidal
body** as codified in Snyder, J.P. 1987, *Map Projections — A Working Manual*, USGS
Professional Paper 1395 (the near-universal basis cited by the PDS3 Map Projection
standard for exactly this projection family) — offered as the best documented proxy,
**not** as a verified MIPL derivation:

Forward (lat φ, lon λ → line, sample), north polar case, spherical body of radius R:
```
ρ = 2 R k0 tan(π/4 − φ/2)                      [tangent, k0=1 at pole; k0<1 if secant]
x = ρ sin(λ − λ0)
y = ρ cos(λ − λ0)          (λ0 = CENLON, the central meridian)

sample = MPOX + x / MPS  (+ rotation by NORTHANG/DU as needed, sign per §3)
line   = MPOY − y / MPS
```
Inverse (line, sample → φ, λ):
```
x = (sample − MPOX) * MPS
y = (MPOY − line) * MPS
ρ = sqrt(x² + y²)
c = 2 atan(ρ / (2 R k0))
φ = π/2 − c                                    (north polar; φ = asin form for oblique cases)
λ = λ0 + atan2(x, y)
```
A rotation by (NORTHANG and/or DU) about the origin must be folded into the x,y
definitions above if the image is not already north-up in line/sample space — see §3.
Any PXL_MINLINE/PXL_MINSAMP offset must be added/subtracted to translate between this
file's local (1..NL, 1..NS) raster and the MPOX/MPOY reference frame (§4).

**Radius / oblateness — inferred, not documented.** Given Jupiter is stated with two
radii (71492×66854 km) and MPS is a single scalar km/pixel with no separate
line-scale/sample-scale, and MPROJ is a *map* projection (not a photogrammetric
body-intersection step, which happens upstream in JIR2VIC), the most consistent
assumption is that APIMAP6E projects onto a **sphere** using a single reference
radius (plausibly the local planetocentric radius at CENLAT for an authalic/conformal
approximation, or simply the equatorial radius) — VICAR/MIPL `mp` routines for giant
planets historically support **oblate-spheroid** projections too (this is a documented
general MIPL capability, e.g. for Voyager/Galileo/Cassini limb and reseau work), so an
oblate treatment cannot be excluded. **This is genuinely unknown from documentation
retrieved here**; it is exactly the kind of parameter that should be checked by taking
two labeled control latitudes on the same image and testing whether an authalic/
mean-radius sphere or the true oblate spheroid reproduces MPS self-consistently.

## 3. Direction of increasing longitude across the image

**Inferred, moderate-high confidence.** With planetocentric **east-positive** longitude
(stated as given) and a **north-polar** stereographic view, the standard convention
(shared by essentially all planetary polar-stereographic products, e.g. PDS, ISIS,
USGS) is: **looking down on the north pole from above (i.e. as conventionally
displayed, north pole at image center), longitude increases counter-clockwise** — this
is simply what "east-positive" means geometrically when viewed from outside the
rotation axis looking toward the pole from the +z (north) side, consistent with a
right-handed planetocentric frame. (By contrast, looking down on the *south* pole,
east-positive longitude increases clockwise.) NORTHANG then specifies how this
longitude-increasing sense is rotated relative to the image's line/sample axes (i.e.
which way "up" in the raster points relative to true north) — see §4. This reasoning
is standard planetary-cartography convention, not something separately confirmed in a
MIPL document during this session; flag as inferred.

## 4. PXL_MINLINE / PXL_MINSAMP and NORTHANG

- **PXL_MINLINE, PXL_MINSAMP — inferred, moderate-high confidence.** Given values
  (2, 139) are small integers, not fractions of MPS, and not lat/lon-like, they read as
  **pixel-index offsets**: the (line, sample) — in the larger reference/master grid
  whose origin is (MPOX, MPOY) — at which this file's local pixel (1,1) begins. This is
  the standard way MIPL mosaic/cutout tools tag a sub-image so it can be re-embedded
  into (or reconstructed relative to) the full projection space without re-deriving the
  geometry — directly relevant to "reproduce a published mosaic grid exactly": to place
  this NL×NS tile correctly, offset every local (line, sample) by
  (PXL_MINLINE−1, PXL_MINSAMP−1) [if 1-based] before applying the MPOX/MPOY-referenced
  formulas in §2, or equivalently substitute
  `sample_global = sample_local + PXL_MINSAMP − 1`,
  `line_global = line_local + PXL_MINLINE − 1` into those formulas. **Not documented
  from a primary source**; consistent with, but not proven by, the numeric example.

- **NORTHANG — inferred, moderate confidence.** Reads as "north angle": the clockwise
  (per common MIPL/planetary convention) angle, in degrees, from the image's "up"
  (−line) direction to true celestial/cartographic north at the image's projection
  center, i.e. a per-image, per-observation-geometry quantity (66.3493° here) distinct
  from any fixed projection-definition parameter like DU. This matches its use
  elsewhere in planetary image labels (e.g. PDS `NORTH_AZIMUTH`, a documented PDS3
  keyword — see §5) as a display/QA angle rather than a value consumed by the forward
  map-projection math itself (which is already fully determined by MPROJ/MPOX/MPOY/MPS/
  CENLAT/CENLON). Not confirmed against MIPL documentation.

## 5. APIMAP6E is not publicly documented — closest documented relatives

**Confirmed by extensive search (documented absence):** no public documentation for
`APIMAP6E`, `APIMAP`, or `JIR2VIC` was found anywhere (JPL/MIPL site, GitHub, PDS,
published literature, general web search) during this session. This is consistent with
these being internal/mission-specific MIPL pipeline programs (the "6E" version suffix
is typical MIPL practice for recompiled program variants) that were never part of the
public VICAR open-source release or its help-file set, or that the help files exist
only on `www-mipl.jpl.nasa.gov`, which could not be reached this session (see header).

**Closest documented relatives (genuinely consulted, though full text not retrievable
this session due to the same connectivity issue for the MIPL-hosted copies):**
- **VICAR itself** — open-sourced at NASA-AMMOS/VICAR on GitHub
  (https://github.com/NASA-AMMOS/VICAR) and documented at
  https://nasa-ammos.github.io/VICAR-DOCS/ (confirmed reachable; lists a "Delivery
  specific help for individual VICAR programs" index at `/VICAR-DOCS/vichelp.html`,
  not individually fetched this session for map2/map3/apimap due to time).
- **MAP2 / MAP3** — the standard public-facing MIPL map-projection programs; per
  general MIPL practice (as referenced in search-indexed fragments of the MIPL site,
  e.g. `vichelp/perslab_level2.html`, `vichelp/nimscmm2_level2.html` confirming the
  existence and URL pattern of the `vichelp` program-help corpus) these are the
  documented ancestors whose parameter set (PROJECT/scale/center-lat-lon/line-sample
  offsets) APIMAP6E almost certainly extends or wraps for the JIRAM-specific pipeline.
- **VICAR "MAP" label property keywords** (`MAP_PROJECTION_TYPE`, `MAP_SCALE`,
  `CENTER_LATITUDE`, `CENTER_LONGITUDE`, `LINE_PROJECTION_OFFSET`,
  `SAMPLE_PROJECTION_OFFSET`) — these are **documented** PDS3 `IMAGE_MAP_PROJECTION`
  object keywords (PDS3 Standards Reference; instances confirmed via multiple live PDS
  `.LBL`/`.cat` files retrieved this session, e.g. HiRISE and other PDS3 map-projected
  product labels). `LINE_PROJECTION_OFFSET`/`SAMPLE_PROJECTION_OFFSET` are documented as
  the (line−1)/(sample−1) at which the projection origin falls — structurally the same
  role inferred above for MPOX/MPOY, and the recommended cross-check: if the engineer
  can find *any* PDS3-labeled MAP-projected product from the same MIPL pipeline family,
  comparing its `LINE_PROJECTION_OFFSET`/`SAMPLE_PROJECTION_OFFSET` values against that
  product's VICAR-native MPOX/MPOY should pin down the offset/indexing convention
  empirically.
- **The MIPL "mp" map-projection library** (`mp_routines.pdf`) — referenced repeatedly
  by title in search results as the authoritative low-level source for exactly the
  MPROJ code table and DU/rotation semantics needed here, but **the document itself
  could not be located or fetched this session**.

**Bottom line on inference vs. documentation:** essentially every quantitative
formula and every semantic reading of DU, MPOX/MPOY's exact indexing base, and the
MPROJ=4 code assignment in this note is an **inference**, made because the primary MIPL
sources (VICAR User's Guide, vichelp pages, mp_routines.pdf) were unreachable from this
environment in the time available. The one keyword-family that rests on genuinely
retrieved documentation is the PDS3 `IMAGE_MAP_PROJECTION` object terminology used as
the interpretive analogy throughout. Before reproducing a published mosaic pixel-exact,
the engineer should validate the MPROJ code table, the MPS true-scale latitude, the DU
semantics, and the MPOX/MPOY indexing base against either (a) the live VICAR GitHub
source for the `mp` library and any `apimap*` program, or (b) empirically, by
back-projecting the four corner lat/lons given in the label (MINLAT/MAXLAT/MAXLON, and
CENLAT/CENLON at the presumed origin pixel) through the candidate formula and checking
for a consistent MPS and rotation.
