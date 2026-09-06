# Spec: SPICE geometry engine for JIRAM camera frames (milestone 2)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog
(`uv` project, Python 3.12, package `jiram_catalog` under `src/`). An
index and mirror already exist (`jiram_catalog.index.load_frames`,
mirror root `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror`, CLI
`jiram-catalog` in `src/jiram_catalog/cli.py` built with argparse). Read
`src/jiram_catalog/cli.py` and `src/jiram_catalog/index.py` first to
follow their conventions (mirror-root resolution, `--orbits` parsing,
logging).

## Goal
Per-pixel planetocentric latitude, east longitude, range, emission,
incidence and phase for any JIRAM camera frame (128 lines x 432
samples) at its label epoch, computed with SPICE kernels in a vectorised
way, plus kernel management and a `kernels` CLI subcommand.

## Files in scope (create/modify)
- `src/jiram_catalog/geometry.py`      (create) engine and KernelSet
- `src/jiram_catalog/kernels.py`       (create) kernel manifest and download
- `src/jiram_catalog/cli.py`           (modify) add `kernels` subcommand only
- `tests/test_geometry_offline.py`     (create) offline unit tests
- `pyproject.toml`, `uv.lock`          (`uv add spiceypy certifi`)

READ-ONLY: `tests/test_gate_geometry_pj4.py`, `tests/test_gate_pj4.py`,
`tests/fixtures/`, `docs/`, `scripts/`, `CLAUDE.md`, `README.md`,
`.gitignore`, `vendor/`, all other files under `src/jiram_catalog/`
except `cli.py`. Never write under `/expanse/lustre/projects/cla119/kaushiks/JIRAM`
or `~/scratch`. Outside the repo you may write only under the mirror
root and `/tmp`. A full-archive label mirror is running in the
background writing under `<mirror>/pds4/`; do not touch that tree.

## Kernel facts (measured 2026-09-04)
- Mirror layout: `<mirror>/spice/{lsk,pck,fk,ik,sclk,spk,ck}/<file>`.
  Present now: `lsk/naif0012.tls`, `pck/pck00010.tpc`, `fk/juno_v12.tf`,
  `ik/juno_jiram_v02.ti`, `sclk/JNO_SCLKSCET.00211.tsc`,
  `spk/spk_rec_170106_170228_170307.bsp`,
  `ck/juno_sc_rec_170129_170204_v01.bc`. This set was verified to
  reproduce the archive's own geometry for 48 orbit-4 frames.
- Each index row carries `spice_kernels` (names joined by `;`), listing
  the kernels the archive used, e.g. `juno_v09.tf;juno_jiram_v01.ti;
  juno_struct_v01.ti;naif0012.tls;pck00010.tpc;JNO_SCLKSCET.00061.tsc;
  de436s.bsp;juno_struct_v04.bsp;jup310.bsp;spk_rec_170106_170228_170307.bsp;
  juno_sc_rec_170129_170204_v01.bc`. Only the date-specific CK
  (`*.bc`) and reconstructed SPK (`spk_rec_*.bsp`, also accept
  `juno_rec_*.bsp`, `spk_pre_*.bsp`, `juno_pre_*.bsp`) entries are used
  from that list; every other entry is ignored in favour of the static
  set in the mirror.
- Operational NAIF server: `https://naif.jpl.nasa.gov/pub/naif/JUNO/kernels/<kind>/<file>`
  with kind in `lsk pck fk ik sclk spk ck`. It fails TLS verification
  with the system CA bundle; downloads must pass the certifi bundle:
  `wget --ca-certificate=<certifi.where()> ...`. The PDS SPICE archive
  `https://naif.jpl.nasa.gov/pub/naif/pds/data/jno-j_e_ss-spice-6-v1.0/jnosp_1000/data/<kind>/<file>`
  holds the same CKs under identical names and the reconstructed SPKs
  renamed `spk_rec_X.bsp` -> `juno_rec_X.bsp`.
- Instrument kernel `juno_jiram_v02.ti`: frames `JUNO_JIRAM_I_LBAND`
  (NAIF id -61411) and `JUNO_JIRAM_I_MBAND` (-61412); boresight
  (0,0,1); FOV_REF_VECTOR (1,0,0); half-angles 0.872 deg along +X (128
  lines) and 2.943 deg along +Y (432 samples); `INS-6141N_IFOV =
  (0.000237767, 0.000237767)` rad/pixel; `PIXEL_LINES=128`,
  `PIXEL_SAMPLES=432`. The IK's detector diagram places pixel
  (sample 1, line 1) at +X,+Y and (sample 432, line 128) at -X,-Y; +X
  is the spacecraft spin direction, +Y cross-track.
- The archive geometry epoch is the label `START_TIME` (index column
  `start_time`) exactly; Juno spins at 2 rpm, so an epoch error of 0.1 s
  moves the boresight by more than a degree. Never add exposure offsets.

## API (exact names; the READ-ONLY gate imports these)
```python
from jiram_catalog.geometry import KernelSet, FrameGeometry, frame_geometry, pixel_directions

def pixel_directions(lines: int, samples: int, ifov_rad: float) -> np.ndarray  # (lines, samples, 3) unit vectors

class KernelSet:
    paths: list[Path]
    def __init__(self, paths: Sequence[str | Path]) -> None      # furnsh in the given order
    def unload(self) -> None                                     # unload exactly these paths
    def __enter__/__exit__                                       # exit -> unload
    @classmethod
    def static_paths(cls, mirror: str | Path) -> list[Path]
    @classmethod
    def for_orbits(cls, mirror: str | Path, orbits: Iterable[int], frames: pd.DataFrame | None = None) -> "KernelSet"

@dataclass
class FrameGeometry:
    epoch: str; et: float; trgepc: float; band: str; frame: str; ifov_rad: float
    lines: int; samples: int
    lat: np.ndarray; lon_east: np.ndarray; range_km: np.ndarray
    emission: np.ndarray; incidence: np.ndarray; phase: np.ndarray
    on_planet: np.ndarray                     # bool (lines, samples)
    boresight: dict                           # keys lat, lon_east, range_km, emission, incidence, phase (NaN if off-planet)
    corners: np.ndarray                       # (4, 2) [lat, lon_east] for (line,sample) = (1,1), (1,432), (128,1), (128,432); NaN off-planet
    sub_sc_lat: float; sub_sc_lon_east: float; sc_altitude_km: float
    obspos_km: np.ndarray                     # (3,) observer position in IAU_JUPITER used for the intercepts
    sunpos_km: np.ndarray                     # (3,) Sun position in IAU_JUPITER used for illumination

def frame_geometry(epoch, band: str, kernels: KernelSet, *, lines: int = 128, samples: int = 432, abcorr: str = "LT+S") -> FrameGeometry
```
`epoch` accepts an ISO-8601 UTC string (`2017-02-02T11:40:03.870`, with
or without trailing `Z`), a `datetime`, a `numpy.datetime64`, or a
`pandas.Timestamp`; all are converted to an ISO string
`YYYY-MM-DDTHH:MM:SS.ffffff` (six fractional digits, no rounding or
truncation beyond microseconds; strings with fewer digits are
zero-padded, strings with more than six are rejected with `ValueError`)
before `spiceypy.str2et`. `band` is `"L"` or `"M"` (case
sensitive; anything else raises `ValueError`). `kernels` is only used to
assert that kernels are loaded (`spiceypy.ktotal("ALL") > 0`), since
SPICE state is global.

## Algorithm (normative)
1. `frame = "JUNO_JIRAM_I_MBAND"` for M, `"JUNO_JIRAM_I_LBAND"` for L;
   `ins_id = -61412` / `-61411`; `ifov_rad = spiceypy.gdpool(f"INS{ins_id}_IFOV", 0, 2)[0]`.
2. `pixel_directions`: pinhole model in the band frame. For 1-based line
   `l` and sample `s`: `x = ifov * (lines/2 + 0.5 - l)`,
   `y = ifov * (samples/2 + 0.5 - s)`, `d = (x, y, 1) / |(x, y, 1)|`.
   Array index `[l-1, s-1]`. (So `[0,0]` has +x,+y; the mean of the four
   central pixels' directions, normalised to unit length, is `(0,0,1)` to 1e-12.)
3. `et = str2et(epoch)`. Light-time epoch and observer position: call
   `sincpt("ELLIPSOID","JUPITER",et,"IAU_JUPITER",abcorr,"JUNO",frame,[0,0,1])`.
   If it succeeds: `trgepc` from it and `obspos = spoint - stlabx(srfvec)`
   (REVISED 2026-09-04 after implementation: `srfvec` returned by
   `sincpt` with an `S` correction is the *apparent* vector, so it must
   be converted to geometric with the same inverse aberration used for
   the pixel rays in step 4; the same applies to the probe and `spkpos`
   fallbacks below. Measured per-pixel agreement with the oracle:
   8e-5 deg, versus 4e-3 deg without this conversion).
   If it raises `NotFoundError` (boresight off-planet): try the same call
   for the four corner directions and the 4 edge midpoints in turn and
   use the first success; if all fail, use
   `pos, lt = spkpos("JUPITER", et, "IAU_JUPITER", abcorr, "JUNO")`,
   `trgepc = et - lt`, `obspos = -pos`.
4. Ray directions in `IAU_JUPITER`: `R1 = pxform(frame, "J2000", et)`;
   `dJ = d @ R1.T`; if `abcorr` contains `S`: the pixel ray is the
   *apparent* direction seen by the instrument, so convert it to the
   geometric direction with the INVERSE stellar-aberration correction
   (the semantics of `spiceypy.stlabx`, which is what `sincpt` inverts),
   using the observer velocity relative to the solar system barycentre,
   `v = spkezr("JUNO", et, "J2000", "NONE", "SOLAR SYSTEM BARYCENTER")[0][3:6]`,
   via a vectorised re-implementation that agrees with `spiceypy.stlabx`
   to 1e-9 rad per ray (provide both directions in one helper,
   `aberrate(dirs, v, inverse: bool)`, forward = `stelab`); then
   `R2 = pxform("J2000", "IAU_JUPITER", trgepc)`; `u = dJ @ R2.T`.
   If `abcorr == "NONE"`, skip the aberration and use `R2` at `et`.
5. Ellipsoid intercept (vectorised numpy, float64): radii
   `a, b, c = bodvrd("JUPITER", "RADII", 3)[1]`; scale `obspos` and `u`
   by `1/(a,b,c)`, solve `|p + t q|^2 = 1` for the smallest positive `t`;
   discriminant < 0 or `t <= 0` -> off-planet (NaN in every per-pixel
   array, `on_planet=False`). Surface point `spoint = obspos + t * u`
   (unscaled units, km).
6. Per-pixel quantities: `lat, lon` from `spoint` (planetocentric:
   `lat = atan2(z, hypot(x, y))`, `lon_east = atan2(y, x) mod 360`, in
   degrees); `range_km = |spoint - obspos|`; outward normal
   `n = normalize(spoint / (a^2, b^2, c^2))`; `emission = angle(n, obspos - spoint)`;
   `sunpos = spkpos("SUN", trgepc, "IAU_JUPITER", abcorr, "JUPITER")[0]`;
   `incidence = angle(n, sunpos - spoint)`; `phase = angle(obspos - spoint, sunpos - spoint)`;
   all angles in degrees.
7. Boresight dict: from the step-3 `sincpt` result when it succeeded
   (lat/lon/range from `spoint`/`srfvec`; emission/incidence/phase from
   `illumf("ELLIPSOID","JUPITER","SUN",et,"IAU_JUPITER",abcorr,"JUNO",spoint)`),
   else all NaN. Corners: from the per-pixel arrays at the four corner
   indices. Sub-spacecraft point: `subpnt("NEAR POINT/ELLIPSOID","JUPITER",et,"IAU_JUPITER",abcorr,"JUNO")`;
   `sc_altitude_km = |srfvec|`; `sub_sc_lat`, `sub_sc_lon_east` from
   its `spoint`.
8. Performance: no Python loops over pixels; one call of
   `frame_geometry` for a 128x432 frame must take < 1.0 s wall after
   kernels are loaded.

## KernelSet decisions
- `static_paths(mirror)`: in this order: `lsk/naif0012.tls`,
  `pck/pck00010.tpc`, `fk/juno_v12.tf`, `ik/juno_jiram_v02.ti`, the
  highest-numbered `sclk/JNO_SCLKSCET.*.tsc` present, then, only if
  present, `spk/de442s.bsp`, `spk/jup380s.bsp`, `spk/jup388s.bsp`. The
  first five are required; a missing one raises `FileNotFoundError`
  naming the path.
- `for_orbits(mirror, orbits, frames=None)`: `frames` defaults to
  `jiram_catalog.index.load_frames(mirror, orbits)`; collect the set of
  CK/SPK names (rule in Kernel facts) from `spice_kernels` of rows with
  `parse_ok`; resolve to `<mirror>/spice/ck/<name>` or `spice/spk/<name>`
  (for a missing `spk_rec_X.bsp` also accept `juno_rec_X.bsp`); if any is
  missing raise `FileNotFoundError` listing all missing names and the
  hint `run: jiram-catalog kernels --orbits ...`; load static paths, then
  SPKs (sorted), then CKs (sorted).
- Loading uses `spiceypy.furnsh` per path; `unload` uses
  `spiceypy.unload` per path in reverse order. Never call `kclear`.

## `kernels` subcommand and `kernels.py`
`jiram-catalog kernels --orbits SPEC [--mirror PATH] [--jobs N] [--dry-run]`:
- `needed_kernels(frames) -> list[tuple[kind, name]]` (kind `ck`/`spk`)
  from the same rule as `for_orbits`, plus the static set (`lsk`
  `naif0012.tls`, `pck` `pck00010.tpc`, `fk` `juno_v12.tf`, `ik`
  `juno_jiram_v02.ti`, `sclk` = highest-numbered `JNO_SCLKSCET.*.tsc`
  in the operational `sclk/` listing, and `spk` `de442s.bsp`,
  `jup380s.bsp`, `jup388s.bsp`).
- A file is present when it exists under `<mirror>/spice/<kind>/` with
  size > 0 (also accept the `juno_rec_` rename for SPKs). Missing files
  are downloaded with `wget -q --ca-certificate=<certifi.where()> --tries=3 --timeout=120 -c -O <dest>.part <url>`
  then renamed to `<dest>` on success (exit 0); on failure with the
  operational URL, retry once with the PDS-archive URL (SPKs with the
  rename); still failing -> reported and exit status 1. Up to `--jobs`
  (default 3, cap 4) concurrent downloads via `ThreadPoolExecutor`.
- `--dry-run` prints the needed list with present/missing and total
  missing bytes if a `Content-Length` is obtainable via `wget --spider`
  (skip the size if not), and downloads nothing.
- Prints per-kind counts: needed, present, downloaded, failed.

## Offline tests you must write (`tests/test_geometry_offline.py`; no kernels needed)
- `pixel_directions(128, 432, 0.000237767)`: shape, unit norm; the mean
  of the four central pixels' directions, normalised to unit length,
  equals (0,0,1) within 1e-12; `[0,0]` has positive x and y, `[127,431]`
  negative x and y, `[0,431]` positive x negative y; the angle between
  `[0,0]` and `[0,431]` equals `2*atan(y / sqrt(1 + x*x))` with
  `x = 63.5*ifov` and `y = 215.5*ifov` (both rays share line 1), within
  1e-9 rad; derive the expected value in the test from those formulas,
  do not hard-code a number.
- Ellipsoid intercept helper on radii (3,2,1): observer (10,0,0), ray
  (-1,0,0) -> point (3,0,0), t=7; observer (0,0,10), ray (0,0,-1) ->
  (0,0,1); ray (0,1,0) from (10,0,0) -> off-planet; a batch of mixed
  rays returns NaN exactly where the scalar version misses.
- Vectorised stellar aberration helper: `aberrate(dirs, v, inverse=False)`
  equals `spiceypy.stelab` and `aberrate(dirs, v, inverse=True)` equals
  `spiceypy.stlabx` for 20 random unit vectors and a velocity of
  (10, -20, 5) km/s, within 1e-12 in each component (neither needs
  kernels); and applying forward then inverse returns the input within
  1e-7 per component (NAIF's `stlabx` reverses the rotation rather than
  inverting it exactly, so the round trip differs at order (v/c)^2).
- Planetocentric lat/lon and emission/incidence/phase helpers on hand-
  computed cases (e.g. point (a,0,0) on the sphere a=b=c, observer at
  (2a,0,0) -> emission 0; observer at (2a, a, 0) -> emission 45 deg;
  observer at (a, a, 0) -> emission 90 deg).
- `needed_kernels` on a tiny DataFrame with two rows whose
  `spice_kernels` contain `de436s.bsp;spk_rec_A.bsp;juno_sc_rec_B.bc`
  and `spk_rec_A.bsp;juno_sc_rec_C.bc;junk.tf`: returns exactly
  `[("spk","spk_rec_A.bsp"),("ck","juno_sc_rec_B.bc"),("ck","juno_sc_rec_C.bc")]`
  plus the static entries (mock the sclk listing lookup so the test is
  offline).
- `epoch` normalisation: the four accepted input types for
  `2017-02-02T11:40:03.870` all produce the identical ISO string
  `2017-02-02T11:40:03.870000`; a trailing `Z` is accepted; seven
  fractional digits raise `ValueError`.

## Validation that defines done (all must pass)
```
uv sync
JIRAM_SKIP_GATES=1 uv run pytest -q
uv run jiram-catalog kernels --orbits 4 --dry-run     # must list every file as present except possibly de442s/jup380s/jup388s
uv run jiram-catalog kernels --orbits 4               # downloads only the static SPKs if missing
uv run pytest -q                                      # includes READ-ONLY tests/test_gate_geometry_pj4.py and tests/test_gate_pj4.py
```
Do not run `kernels` for any orbit other than 4. Passing a gate by
editing the gate, the fixtures, or the tolerance is a task failure.

## Report (at most 40 lines)
Commands and outcomes; the worst-case deviations the gate printed (add a
temporary print if needed, but do not leave debugging code in `src/`);
wall time of one `frame_geometry` call; every judgment call.

If any part of this spec is ambiguous or underdetermined, do NOT choose an
interpretation. Stop, list the ambiguities and the options, and make no
further changes. Also list every judgment call you made, however minor,
at the end of your final message.
