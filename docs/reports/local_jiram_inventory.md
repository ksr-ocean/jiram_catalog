# JIRAM data folder inventory

Root: `/expanse/lustre/projects/cla119/kaushiks/JIRAM` (read-only survey). Total size 314M (excl. nothing skipped). `__MACOSX` (3.9M) and `__pycache__` are zip/Python build artifacts, not data.

## 1. Python readers

### `vicar_reader.py`
- Parses **VICAR-format** image files (NOT a separate PDS3 label+image pair — the label is embedded in the file itself as `KEY=VALUE` pairs, VICAR-style: `LBLSIZE`, `FORMAT`, `ORG`, `TYPE='IMAGE'`, `HOST`, `INTFMT`, `REALFMT`, etc.).
- `read_vicar_file(path)`: reads a fixed **12800-byte** header block (`LBLSIZE` in the observed files is exactly 12800 for the `.map` images), decodes ASCII up to the first NUL, regex-parses `key=value` pairs into a dict (`parse_vicar_label`), then reads the rest of the file with `np.fromfile` using dtype selected from `FORMAT` (`BYTE`->uint8, `HALF`->int16, `FULL`->int32, `REAL`->float32). Reshapes using `NL`,`NS`,`NB` according to `ORG` (`BSQ`->`(nb,nl,ns)`, `BIL`->`(nl,nb,ns)`, `BIP`->`(nl,ns,nb)`).
- Returns `(label: dict, image_data: np.ndarray)`. For the `.map` files observed: dtype float32 (`FORMAT='REAL'`), shape `(1, 3000, 3200)` (`NB=1, NL=3000, NS=3200`, `ORG='BSQ'`). Units are NOT stated in the label; JIRAM M-band RDR products are typically radiance (W/m²/sr/µm) but this is an inference, not read from the file.
- `__main__` block: globs `*.map` (falls back to `*.tp4`) in a given subfolder, sorts files by `IMAGE_TIME` (via `dateutil.parser.isoparse`), and for each file prints dimensions/instrument/time and saves a `jet`-colormap PNG rendering (`<name>.png`) next to the source file. This is exactly how the `.png` browse images and the four `*.out` log files in this folder were produced (stdout capture).
- Caveat found during inspection: this reader always reads a fixed 12800-byte header. That is correct for the `.map` files (`LBLSIZE=12800`) but NOT correct for the `.tp4` velocity-vector files, whose actual `LBLSIZE=1728`. Running `vicar_reader.py` on `.tp4` files would misparse the label/data boundary. Only `vicar_pair_reader.py` reads the true `LBLSIZE` implicitly by depending on `NL,NS,NB` from the label, but it too hardcodes reading 12800 header bytes in `read_vicar_file` — so it is also only correct for the 12800-byte `.map` labels, not for `.tp4`.

### `vicar_pair_reader.py`
- Contains its own copies of `parse_vicar_label` / `read_vicar_file` (same 12800-byte header assumption; only difference: if `NB>1` it subsamples band 0 via `image_data[::nb]` before reshaping to `(nl,ns)` — i.e. designed for single-band 2-D map-projected frames).
- `get_geo_bounds(label)`: computes lat/lon bounding box of a map-projected image from `CENLAT`, `CENLON`, `PXL_HSCALE`, `PXL_VSCALE`, `NL`, `NS` (simple equirectangular-style box in projection units, degrees per pixel implied by the scale keywords — actually `PXL_HSCALE/VSCALE` in the observed files are ~27690.7, units unclear/likely map-projection ground units, not degrees; treated as "degrees" by the arithmetic in this script, which is a questionable assumption worth flagging to the cataloging-tool designer).
- `crop_to_intersection`: given two labels/images and a common lat/lon box, crops each to the overlapping pixel window.
- **What a "pair" means**: two different `.map` images (passed as CLI args), each read independently, whose geographic (projected) extents are intersected; both images are cropped to that common footprint; then `IMAGE_TIME` of each is diffed (`Δt` in seconds) to characterize the time separation. This is a **spatial co-registration step preparing two time-separated, same-region frames for cloud-motion / feature-tracking (wind vector derivation)** — i.e., pairing consecutive JIRAM map-projected frames of the same target region taken at different times so that cross-correlation tracking (see `output_pairs/`, `JIRAM velocity_vectors at 45 km/`, `TRACKER4` task in `.tp4` history — see below) can be run on them. Output: two cropped `.npy` arrays and a `_delta_t.txt` file per pair, written to `output_pairs/`.

## 2. Subfolders `n01_15km` .. `n04_15km` (identical structure)

Each folder: 24 files = 12 `.map` + 12 `.png`, letters `a`..`l`. Sizes: n01=12M, n02=9.0M, n03=7.2M, n04=5.3M (pngs are small; size differences track image content, all `.map` are the same nominal size ~3000x3200 float32 ≈ 38.4MB uncompressed... wait: actual `.map` file size ≈ 12800(header)+3000*3200*4(data) ≈ 38.4MB per file, but `du -sh` for n01 shows only 12M for the WHOLE folder — see note below under Uncertainties: local `.map` files may be sparse/compressed on this filesystem (lustre reports allocated blocks, not logical size) or files may not all be full-resolution; this needs checking with `ls -la` per file if it matters for the cataloging tool, I only spot-checked file sizes indirectly via `du`.

Representative label (`n01_15km/n01a.map`, first ~4000 bytes): VICAR label, `LBLSIZE=12800 FORMAT='REAL' ORG='BSQ' NL=3000 NS=3200 NB=1`. Key science/geometry fields:
- `MISSION_NAME='JUNO'`, `CAMERA_NAME='JIRM'`, `INSTRUMENT_ID=5`, `FILTER_NAME='M'`
- `IMAGE_TIME='2017-02-02T11:40:03.870'`, `PRODUCT_ID='JIR_IMG_RDR_2017033T114006_V02'`, `ORBIT=4`
- Geometry: `SUBSOLAR_LAT/LON`, `IN_ANGLE` (incidence), `EM_ANGLE` (emission, =16.5375 here), `PH_ANGLE` (phase), `CENLAT=83.15`, `CENLON=305.73` (north-polar target), `MINLAT=79.81`, `MAXLAT=84.80`, `MAXLON=262.54`, `PXL_HSCALE=PXL_VSCALE=27690.7`, `NORTHANG=66.35`, map projection `MPROJ=4` (polar stereographic-style, per `DU=-90.0` — south-up/north-pole-centric convention), `MPS=15.0` (map pixel scale, 15 km/pixel — matches the `_15km` folder-name suffix), `SPACECRAFT_ALTITUDE=115378.0` km.
- Processing history embedded at end of label: `TASK='JIR2VIC'` (JIRAM PDS product -> VICAR conversion, 2019-10-14) -> `TASK='APIMAP6E'` (map-projection, 2020-11-06) -> `TASK='COPY'` (2020-11-06).
- Product ID timestamp in filename-adjacent metadata increases monotonically with the letter suffix (a->l), i.e., each `nXX_15km` folder is one Juno perijove pass broken into 12 consecutive ~30s-cadence sub-frames.

Per-folder time coverage (all `ORBIT=4`, filter `M`, 2017-02-02):
| folder | first IMAGE_TIME | last IMAGE_TIME | n frames |
|---|---|---|---|
| n01_15km | 11:40:03.870 | 11:45:39.521 | 12 |
| n02_15km | 11:48:10.956 | 11:53:46.624 | 12 |
| n03_15km | 11:56:17.872 | 12:01:53.594 | 12 |
| n04_15km | 12:04:24.500 | 12:10:00.348 | 12 |

Gaps of ~2.5-7.5 min between folders (n01 end -> n02 start = 2:31, n02 end -> n03 start = 2:31, n03 end -> n04 start = 2:31). All from the same orbit/perijove pass (ORBIT=4), consecutive groups likely correspond to a repeating imaging cadence during the polar flyby. `.map`/`.png` filename pattern: `n{01..04}{a..l}.map` / same stem `.png`. Stray file `n01_15kmn01h.png` at top level is a duplicate/misplaced copy of `n01_15km/n01h.png` (identical PNG: 459x418 RGBA) — looks like an accidental `os.path` join bug when the folder name was concatenated with the filename without a separator (an artifact of a run of `vicar_reader.py`, not part of the original supplement).

`.png` files: 459x418, 8-bit RGBA, `jet`-colormap renders (matplotlib `imshow` + `savefig`) of each `.map` frame — these are locally-generated browse images, NOT present in the source zip (see zip listings below), so they were produced by running `vicar_reader.py` after unzipping.

## 3. `output_pairs/`

3 files, ~67MB: `n01a_vs_n02a_pair1.npy` (29.9MB), `n01a_vs_n02a_pair2.npy` (38.4MB), `n01a_vs_n02a_delta_t.txt` (`487.086`, seconds). Product of running `vicar_pair_reader.py n01_15km/n01a.map n02_15km/n02a.map`: two cropped-to-common-footprint float32 arrays (different sizes because pixel scale/crop offset differ slightly) and their time separation (487.086 s ≈ 8.1 min, consistent with n01a IMAGE_TIME 11:40:03.870 vs n02a IMAGE_TIME 11:48:10.956... note: actual diff is 487.086s = matches). Only one pair has been run; this folder is evidently a scratch/demo output of a single example invocation of the pair reader, not a systematic product set.

## 4. `1.out`, `2.out`, `3.out`, `4.out`, `time.out`

`{N}.out`: captured stdout of `vicar_reader.py` run once per `n0N_15km` folder — 12 repeated blocks per file (one per sub-frame), each block: `Image dimensions: 3000 x 3200 x 1`, `Instrument: 5`, `Image time: <ISO time>`, `Image data shape: (1, 3000, 3200)`, `Data type: float32`, and the output PNG path. 1884 bytes each, 72 lines each (12 frames x 6 lines).

`time.out`: 48 lines, just `Image time: <ISO time>` for all 48 frames across all 4 folders (n01..n04), in chronological order — looks like a simple aggregated timestamp manifest, likely built by concatenating/filtering the 4 `.out` files (`grep "Image time"`).

## 5. `41550_2022_1774_MOESM4_ESM.xls`

Legacy binary Excel (OLE2 Compound Document, "Composite Document File V2"). Metadata: Author = "Created with the Wolfram Language: www.wolfram.com", Last Saved By = "Microsoft Office User", Creating App = "Microsoft Macintosh Excel", Create Time 2021-12-20, Last Saved 2022-07-08. No human name in metadata.
Read with pandas 1.1.3 + xlrd 1.2.0 (system `/usr/bin/python3` lacks pandas/xlrd; the cluster's `module load anaconda3/2020.11` environment has both — the newer `/home/kaushiks/anaconda3` Python 3.12 has pandas but it fails to import there due to a `libstdc++` GLIBCXX version mismatch on this host).
- 1 sheet: `Sheet1`, shape (301 rows, 2 columns).
- Columns: `r, km`, `v, m/s`.
- `r` runs 10 to 6010 km in ~20 km steps (301 points); `v` ranges -26.07 to +81.38 m/s (mean 22.96).
- First 5 rows: (10, 2.30), (30, 4.06), (50, 7.15), (70, 10.97), (90, 13.21).
- Interpretation (inference, not stated in file): a **radial (azimuthal) wind-speed profile**, e.g. of a polar cyclone, out to ~6000 km radius — consistent with the polar (`CENLAT≈83°N`) imagery and cloud-tracked wind vectors elsewhere in this folder.

## 6. Zip files (`unzip -l` only, not extracted)

### `41550_2022_1774_MOESM2_ESM.zip` (25.2 MB compressed on disk / 1,843,828,489 bytes uncompressed, 104 real entries + AppleDouble)
- Top-level dirs: `n01_15km/`, `n02_15km/`, `n03_15km/`, `n04_15km/`, plus `__MACOSX/` mirror.
- Extensions: 96 entries ending `.map` = 48 real `.map` files (12 per folder x 4) + 48 `__MACOSX/._*.map` AppleDouble resource-fork sidecars (macOS zip artifacts, no data).
- No `.png`, no README/txt/doc/pdf inside either zip.
- This zip is the **source archive for the `n01_15km`..`n04_15km` folders** (filenames match exactly); the local `.png` browse images and `.out` logs were generated afterward, not shipped in the zip.

### `41550_2022_1774_MOESM3_ESM.zip` (121.1 MB compressed / 154,621,456 bytes uncompressed, 138 real entries + AppleDouble)
- Top-level dir: `JIRAM velocity_vectors at 45 km/` (with embedded space), plus `__MACOSX/` mirror.
- Extensions: 136 entries ending `.tp4` = 68 real `.tp4` files + 68 `__MACOSX/._*.tp4` sidecars.
- This zip is the **source archive for the `JIRAM velocity_vectors at 45 km/` folder** (same 68 filenames, same two-orbit-group prefixes `n0103*`/`n0204*`).
- Internal file dates cluster 2021-02-22 to 2021-02-26 (original processing/tracker run dates), with directory entries dated 2021-10-25 (repackaging/zip-creation date, matching the `com.apple.quarantine` Firefox-download timestamp found in the local `__MACOSX` AppleDouble files, decoded to 2021-10-21).

Both zips' listings are well under 200 entries, so no pattern-summarization was needed; every entry was enumerated.

## 7. `JIRAM velocity_vectors at 45 km/`

68 `.tp4` files, 142M total. Two filename-prefix groups: `n0103*` (34 files) and `n0204*` (34 files), each followed by a 2-letter suffix (`aa`,`ab`,`ba`,`bb`,`bc`,`cb`,... up to `ll`) — 34 letter-pair combinations forming a "band-diagonal" adjacency pattern over the 12 sub-frame letters `a..l`.

Verified by reading headers of several files (`n0103aa`, `n0103ab`, `n0103ba`, `n0204aa`, `n0204ll`):
- `n0103<XY>.tp4`: cross-correlates sub-frame `X` of the `n01_15km` group against sub-frame `Y` of the `n03_15km` group (e.g. `n0103ab` -> `TTIME0`=n01a's IMAGE_TIME, `TTIME1`=n03b's IMAGE_TIME). `n0204<XY>.tp4` does the same for `n02_15km` vs `n04_15km`. So the tracking baseline is **two folders apart** (~16 min separation, `DT`≈973-1005 s), not adjacent folders — presumably chosen for a longer, more precise baseline than the ~8 min adjacent-folder gap, appropriate for slow cloud-level (45 km altitude) motions.
- The two-letter suffix is NOT free-form; each of the 12 sub-frames only appears paired with itself and its near-neighbor sub-frames (self + 1-2 letters away), consistent with a rolling/chained sub-frame correlation rather than an all-pairs correlation.

**Format**: same VICAR-style embedded label as the `.map` files but with `LBLSIZE=1728` (NOT 12800 — confirmed by byte-inspection; `vicar_reader.py`/`vicar_pair_reader.py` as written would misparse these files since they hardcode a 12800-byte header read). Also `EOL=1`: a **trailing** 224-byte VICAR label follows the data, ending with processing history `TASK='JIR2VIC'` -> `'APIMAP6E'` -> `'COPY'` -> **`TASK='TRACKER4'`** (2021-02-22), the cross-correlation/feature-tracking task that produced the velocity vectors. Total file size = 1728 (header) + NL*NS*4 bytes (float32 data, `NL=114242`, `NS=8`, `NB=1`, `ORG='BSQ'`, ~3.66MB) + 224 (trailing label) — verified exactly for `n0103aa.tp4` (3,657,696 bytes).
- Extra tracker-specific header fields (beyond the `.map` label fields): `LINEMIN/MAX`, `SAMPMIN/MAX` (valid image window), `TTIME0`/`TTIME1` (times of the two frames tracked), `TMPS0`/`TMPS1`, `GRID=1`, `NSW=15,NLW=15` (correlation template window, pixels), `NSAREA=35,NLAREA=35` (search area, pixels), `DT` (seconds between TTIME0/TTIME1).

**Data content** (decoded with numpy, `NL=114242` rows x `NS=8` float32 columns — i.e. a flat table of 114,242 tracked-feature vectors, NOT a 2-D image): by inspecting column ranges/relationships,
- col0, col1: integer-valued pixel (sample, line) coordinates of the tracked template in frame TTIME0 (col0 range 1100-1591, col1 range 663-1270 for `n0103aa`).
- col2, col3: matched (sample, line) position in frame TTIME1 (sub-pixel float).
- col4 = col2-col0, col5 = col3-col1: pixel displacement (sample, line).
- col6, col7: physical velocity components in m/s — verified col6 ≈ col4 * `MPS`(=15 km/pixel, 15000 m) / `DT`(s), and likewise col7 from col5 (e.g. for the first vector in `n0103aa`: col4=14.31 px -> col6=220.44 m/s, matches 14.31*15000/974.002=220.5). So **velocities are in the map-projection pixel grid (sample/line), not directly in lat/lon**; they can be converted to zonal/meridional wind or geographic coordinates by combining with the map projection metadata (`CENLAT`,`CENLON`,`MPS`,`MPROJ`,`DU`, north-polar stereographic per the `.map` labels) the same way `vicar_pair_reader.py`'s `get_geo_bounds` does for images — but no lat/lon columns are stored directly in the `.tp4` files themselves.
- 114,242 vectors out of a theoretical max grid of ~(1591-1100)x(1270-663)≈298,644 -> roughly 38% coverage, consistent with a `GRID=1`-pixel search restricted to a valid/high-confidence subset (e.g. non-zero correlation peaks retained, edges/limb excluded).

**"45 km" in the folder name** is very likely the atmospheric altitude level (above some reference, e.g. 1-bar or NH3-cloud-top level) that this cloud-tracked wind product is nominally attributed to, NOT the pixel scale (which is 15 km/pixel, same map grid as `n01-n04_15km`). This is an inference from context (folder naming + JIRAM literature convention of quoting cloud-tracked winds at a nominal altitude), not something written explicitly inside the `.tp4` label itself.

## 8. `n01_15kmn01h.png` (top-level PNG)

Duplicate of `n01_15km/n01h.png` (byte-for-byte same dimensions: 459x418 RGBA). Almost certainly a path-concatenation artifact from a `vicar_reader.py` run with a malformed `dr` argument (missing `/` separator) — not part of the original supplementary data.

## 9. Paper identification (no web access used, per instructions)

Evidence gathered purely from local files:
- `41550_2022_1774_MOESM{2,3,4}_ESM.{zip,zip,xls}` — the `41550` prefix is Nature Astronomy's journal e-ISSN-derived article-numbering stem used by Springer Nature for supplementary material filenames (`<journal-number>_<year>_<article-number>_MOESM<n>_ESM.<ext>`), so this is **Nature Astronomy, 2022, article number 1774**, Supplementary Data 2 (JIRAM `.map` images), Data 3 (velocity vectors), Data 4 (radial velocity profile, `.xls`).
- Data content: JUNO/JIRAM M-band, north-polar-centered (`CENLAT≈83°N`) map-projected imagery from perijove/orbit 4 (2017-02-02), 15 km/pixel, plus cloud-tracked wind vectors nominally at "45 km" altitude, plus a radial wind-speed profile out to ~6000 km — this content strongly matches studies of Jupiter's north-polar cyclone(s)/circumpolar cyclone dynamics imaged by JIRAM (the instrument famous for revealing the octagonal arrangement of cyclones around Jupiter's north pole, Adriani et al. 2018). A 2022 Nature Astronomy paper analyzing JIRAM-derived winds/vorticity of a Jovian polar cyclone at orbit 4 is consistent with this evidence.
- No author names were recoverable from any file: zip listings contain no README/author file; `__MACOSX` AppleDouble sidecars only contain macOS extended attributes (`com.apple.quarantine`, tagged "Firefox" download, decoded timestamp 2021-10-21; `com.dropbox.attrs` on one file, implying original distribution via Dropbox before zip repackaging) — no personal names. The `.xls` metadata author field says only "Microsoft Office User" / Wolfram Language. VICAR processing-history `USER='spe'` (initials of whoever ran the `JIR2VIC`/`APIMAP6E`/`TRACKER4` pipeline steps at the source institution, e.g. INAF/ASI JIRAM ground segment) appears in every label but cannot be resolved to a full name locally.
- **Confidence: low-to-medium** that this is specifically a JIRAM north-polar-cyclone wind-dynamics paper in Nature Astronomy 2022 (article #1774) — the supplementary-file naming convention is essentially certain (Nature Astronomy 2022), but the specific title/authors could not be confirmed without a web lookup, which was intentionally not performed.

## Uncertainties and assumptions (see also inline notes above)

1. **Units of JIRAM radiance/counts** in the `.map`/`.tp4` `FORMAT='REAL'` pixel values are not stated anywhere in the VICAR label; assumed to be calibrated radiance (W m⁻² sr⁻¹ µm⁻¹) based on general JIRAM RDR product knowledge, not confirmed from the files.
2. **`PXL_HSCALE`/`PXL_VSCALE` units** (27690.7 in the `.map` label) — `vicar_pair_reader.py`'s `get_geo_bounds` treats these as "degrees per pixel"-like quantities directly usable in the lat/lon arithmetic, which looks dimensionally inconsistent (27690.7 degrees/pixel is absurd) — more likely these are polar-stereographic **projection-plane units** (e.g. mm or an internal VICAR map unit) that must be converted using `MPS` (15 km/pixel) and the map projection (`MPROJ=4`, north-polar stereographic per `DU=-90`) rather than added directly to `CENLAT`/`CENLON`. I flag this as a probable bug/approximation in `vicar_pair_reader.py`'s geo-bounds math for the cataloging tool designer to be aware of; I did not attempt to fix or fully resolve it.
3. **`du -sh` folder sizes for `n01`-`n04`** (12M, 9.0M, 7.2M, 5.3M) look smaller than the expected ~38MB/file x 12 files/folder computed from `NL x NS x 4 bytes + header`; this is likely a lustre sparse/block-reporting artifact (`du` reports allocated blocks) or the on-disk data may be more compressible/sparse than assumed — not independently verified with `ls -la` per file or `du --apparent-size`.
4. **Exact meaning of `.tp4` columns 0-7** was inferred purely by numeric cross-checking (pixel displacement x scale/time = velocity), not from any documentation string in the label; column order (sample-then-line vs line-then-sample, and which of col6/col7 is "zonal" vs "meridional") is my best-fit interpretation, not confirmed.
5. **"45 km" altitude attribution** for the velocity-vector folder name is inferred from context/JIRAM literature convention, not stated inside any file read.
6. **Paper title/authors** could not be determined (web lookup intentionally excluded per task instructions); only the journal/year/article-number and general science-content inference are offered, with stated low-to-medium confidence.
7. I did not open every file in `n01`-`n04`/velocity-vectors/zips — per instructions, one representative file's header was read per subfolder/zip, plus a few extra `.tp4` headers to confirm the pairing-pattern hypothesis (task explicitly allows "do not open every file").
8. `time.out`'s exact generation method (which script produced it) is inferred (concatenation/grep of the four `.out` files) — no script for generating `time.out` exists in the folder to confirm.
