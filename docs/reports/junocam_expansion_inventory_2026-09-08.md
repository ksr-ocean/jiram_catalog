# JunoCam expansion inventory — 2026-09-08

This is the pre-acquisition reconnaissance record. See the
[expansion delivery record](junocam_expansion_2026-09-08.md) for subsequent
acquisition, individual clearances, repairs and measured mapped products.

Reconnaissance under [the accepted expansion spec](../specs/2026-09-08_junocam_expansion.md) and [inventory assignment](../specs/2026-09-08_junocam_archive_recon.md). The existing archive can support a bounded expansion without downloading whole campaigns: an independently verified option contains **194 full-resolution RGB observations across PJ5, 6, 8, 12, 18, 24, 30 and 34, requiring 7,854,710,784 native bytes (7.3153 GiB)**. These have finite entries in the archive timing table and compatible image dimensions. These conditions establish acquisition/navigation support, not instrument clearance, radiometric validity, common mapped coverage or suitability for motion tracking. The lead chooses the final scope and applies the companion evidence assessment.

No native JunoCam images, shared indexes, policy or application source were changed during this reconnaissance. Retrieved label metadata totalled 865,910 bytes for 337 observations. All 337 label MD5 values matched the existing manifest, all UTC-normalized label `START_TIME` values matched it exactly, and none of these native image paths was already present.

## Sources and reproducible inventory

In paths below, `<mirror>` is `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror`. The durable machine-readable result is:

`<mirror>/junocam/expansion_2026-09-08/inventory/inventory_measurements.json`

It contains `observations` (337 exact versioned IDs), `summaries` (nine candidate windows), and `jiram_kernels` (local frame, strip and kernel checks). Observation fields include `product_id`, `product_stem`, `orbit`, `version`, `n_versions`, `volume`, `file_spec`, `url_lbl`, `url_img`, `start_time`, `stop_time`, `archive_altitude_km`, `label_md5`, `image_md5`, `image_bytes`, `lines`, `samples`, `sample_bits`, `sampling_factor`, `interframe_delay_s`, `filters`, `rationale`, `label_path`, and `native_already_present`. It is 523,692 bytes; SHA256 is `ddce8cd1ead9c70eed71f5874a5adbdb35579eea276d12ab7e19d50d13c93f4b`.

The raw labels are beside it at `inventory/<volume>/<product_id>.LBL`. All are fetched from the observation's exact archive URL. For example, the [PJ5 closest sampled observation](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0012/DATA/RDR/JUPITER/ORBIT_05/JNCR_2017086_05C00107_V02.LBL) and [PJ24 partial observation](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0013/DATA/RDR/JUPITER/ORBIT_24/JNCR_2019360_24C00039_V01.LBL) can be inspected directly.

The current [PDS Imaging archive directory](https://planetarydata.jpl.nasa.gov/img/data/juno/) lists JunoCam volumes `JNOJNC_0001` through `JNOJNC_0035`, matching the local manifest's volume set. The following existing files were read:

| Local file | Measured contents / role |
| --- | --- |
| `junocam/manifest/manifest.parquet` | 82,668 preferred records: 41,334 RDR and 41,334 EDR; 23 columns |
| `junocam/manifest/manifest_files.parquet` | 84,332 all-version records across 35 volumes; 23 columns |
| `junocam/index/junocam_images.parquet` | 3,788 parsed label rows, 58 columns; 2,080 PJ4 and 1,708 PJ58 rows |
| `index/frames.parquet` | 85,108 JIRAM image records |
| `index/frames_geo.parquet` | 113,565 JIRAM band/half geometry rows |
| `strips/strips.parquet` | Existing JIRAM and JunoCam strip availability and times |
| `spice/ck/`, `spice/spk/`, `spice/ik/` | 89 CK files, 72 SPK files and two instrument kernels |

Manifest identity, version, target, orbit, acquisition time, source URL and label checksum fields are sufficient to reproduce these candidate selections. `index_row_json` additionally preserves altitude and rationale. Native byte counts, image checksums and sampling geometry require the labels; they are absent as first-class manifest columns. The parsed image index has these fields, including `sampling_factor`, `framelets_exact`, `img_size_bytes`, `md5`, exposure, TDI, temperatures and SCLK, but currently covers only PJ4/PJ58.

Independent sorting of the all-version table by `(level, product_stem, version, volume)` and retaining the last row per `(level, product_stem)` reproduces the preferred product-ID set exactly. The preferred record must be selected before scientific screening; an ineligible newest version does not justify silently choosing an older processing version. The matching volume set and 337 current label checksums make the manifest adequate for this bounded selection. This was not a fresh checksum audit of every volume index or all archive products; the live archive can revise existing volume contents.

## Candidate windows and measured cost

For each requested pass, the reconnaissance selected preferred RDR records with target `JUPITER`, found the observation with the smallest positive archive `SPACECRAFT_ALTITUDE`, and retained observations whose `START_TIME` falls within 90 minutes either side. **This is an inferred candidate window around an observed time, not a precise dynamical perijove epoch or the archive's defined timing-correction swath.** Whole-campaign and same-day counts show why an orbit/day-only download is too broad. Counts below exclude other targets.

| PJ | Preferred Jupiter campaign date range | Campaign RDR | RDR on centre's UTC day | Window preferred / all versions of those stems |
| --- | --- | ---: | ---: | ---: |
| 3 | 2016-12-10–16 | 587 | 121 | 40 / 81 |
| 5 | 2017-03-20–04-01 | 1,119 | 105 | 17 / 31 |
| 6 | 2017-05-12–24 | 1,140 | 123 | 36 / 66 |
| 8 | 2017-08-26–09-06 | 1,101 | 116 | 25 / 46 |
| 12 | 2018-03-31–04-01 | 152 | 114 | 34 / 53 |
| 18 | 2019-02-12–13 | 76 | 72 | 44 / 66 |
| 24 | 2019-12-26–27 | 75 | 69 | 39 / 39 |
| 30 | 2020-11-08 | 108 | 108 | 51 / 51 |
| 34 | 2021-06-08 | 130 | 130 | 51 / 51 |
| Total within candidate windows | — | — | — | **337 / 484** |

`C` denotes RGB, `M` methane, and `R`/`G` single red/green images. These are observation counts, not the number of individual physical filter framelets. The last column is an **exact label-declared native payload sum** (`RECORD_BYTES × FILE_RECORDS`), not observed network traffic; MiB means 2²⁰ bytes. It excludes derived products, retries and filesystem overhead.

| PJ | Window centre UTC | Actual first–last selected START_TIME, same day | C / M / other | Summed M | Native bytes | MiB |
| --- | --- | --- | --- | ---: | ---: | ---: |
| 3 | 2016-12-11 17:04:08.180 | 15:44:58.170–18:31:02.091 | 22 / 12 / 6 R | 11 | 960,958,464 | 916.44 |
| 5 | 2017-03-27 08:53:09.653 | 07:30:10.651–10:20:20.439 | 15 / 2 / 0 | 2 | 566,759,424 | 540.50 |
| 6 | 2017-05-19 06:03:04.775 | 04:46:04.284–07:30:52.815 | 32 / 4 / 0 | 0 | 1,251,319,808 | 1,193.35 |
| 8 | 2017-09-01 21:49:21.520 | 20:45:11.368–23:05:43.264 | 21 / 4 / 0 | 1 | 957,853,696 | 913.48 |
| 12 | 2018-04-01 09:45:55.040 | 08:16:05.770–11:10:48.573 | 27 / 7 / 0 | 0 | 1,182,973,952 | 1,128.17 |
| 18 | 2019-02-12 17:35:03.468 | 16:09:53.849–19:01:42.169 | 35 / 1 / 8 G | 0 | 1,545,797,632 | 1,474.19 |
| 24 | 2019-12-26 17:38:26.273 | 16:16:31.404–19:01:45.560 | 30 / 5 / 4 G | 0 | 1,359,850,496 | 1,296.85 |
| 30 | 2020-11-08 01:51:17.180 | 00:32:07.881–03:20:44.833 | 39 / 8 / 4 R | 0 | 1,860,526,080 | 1,774.34 |
| 34 | 2021-06-08 07:48:20.461 | 06:28:32.064–09:14:19.200 | 43 / 7 / 1 R | 0 | 1,937,309,696 | 1,847.56 |

All 337 together require **11,623,349,248 bytes (10.8251 GiB)**. The broad sample contains 264 RGB, 50 methane and 23 single-band visible observations. All label sample depths are 16 bits. All RGB records have `SAMPLING_FACTOR=1` and 1,648 samples; the 14 summed methane records have `SAMPLING_FACTOR=2` and **816 samples**, so a fixed full-resolution detector interpretation is wrong for those products. The other methane and single-band visible records have factor 1 and 1,648 samples. Full-resolution sampling alone does not prove complete framelets.

One RGB label explicitly identifies lost/partial data: `JNCR_2019360_24C00039_V01`, 2019-12-26 18:01:28.240 UTC, is only **672 × 1,648** samples (2,214,912 bytes), with rationale `Lost PJ24 Jet S2 - partial`. Its height is divisible by neither 128 nor 384. It must not be interpreted as a regular complete RGB framelet stack; exclusion is the conservative acquisition option. Its timing-table entry is also `NULL`. [Source label](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0013/DATA/RDR/JUPITER/ORBIT_24/JNCR_2019360_24C00039_V01.LBL).

The [archive timing description](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/EXTRAS/TIMING_OFFSETS_DESCRIPTION.TXT) explains that measured corrections are already applied to the label's start time. Do not add them again. Nonzero finite offsets indicate a correction, zero indicates none was needed, and `NULL` means no offset could be determined. The table's units are image pixels; the nominal conversion is N/311.2 seconds. Residual navigation errors can remain. Single-band visible images are outside this timing-table procedure.

An exact `product_id` join to the current [RDR timing table](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/EXTRAS/RDR_TIMING_OFFSETS.TXT) finds 255 finite, 55 listed-NULL and 27 unlisted entries among the 337 candidates. Restricting to finite entries, RGB, factor 1, 1,648 samples, complete 384-row RGB groups, and the eight locally supported passes produces the following **format/navigation-supported option**, independently matching the companion evidence inventory:

| PJ | RGB observations | Exact native bytes |
| --- | ---: | ---: |
| 5 | 12 | 473,358,336 |
| 6 | 32 | 1,197,318,144 |
| 8 | 20 | 896,090,112 |
| 12 | 19 | 817,618,944 |
| 18 | 23 | 853,057,536 |
| 24 | 18 | 688,521,216 |
| 30 | 27 | 1,098,596,352 |
| 34 | 43 | 1,830,150,144 |
| Total | **194** | **7,854,710,784** |

The existence of a timing entry does not clear instrument faults or certify radiometry. Clearance must cite inspected instrument evidence and later pixel/geometry checks for the exact products. The [cumulative errata](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/ERRATA.TXT) and companion evidence report govern those decisions.

## JIRAM overlap and local navigation support

JIRAM overlap was measured by actual timestamps against the same ±90-minute windows, across all `orbit_dir` values. Actual IMG existence and byte sizes were checked on disk; the `img_present` snapshot was false for all 1,978 matching records despite all corresponding images being present. These stale flags must not be interpreted as a need to redownload the images. Geometry rows count physical band/half records, so they can exceed image counts.

| PJ | JIRAM frames / actual IMG files | `geo_ok` band/half rows | Existing JIRAM strip overlaps | Native JIRAM bytes already local | Reconstructed CK/SPK covers entire window |
| --- | ---: | ---: | ---: | ---: | --- |
| 3 | 0 / 0 | 0 | 0 | 0 | No matching dated pair found; current kernel selection fails at centre |
| 5 | 230 / 230 | 230 | 0 | 50,872,320 | Yes |
| 6 | 278 / 278 | 278 | 0 | 61,489,152 | Yes |
| 8 | 296 / 296 | 296 | 0 | 65,470,464 | Yes |
| 12 | 323 / 323 | 323 | 0 | 71,442,432 | Yes |
| 18 | 261 / 261 | 499 | 0 | 110,370,816 | Yes |
| 24 | 161 / 161 | 304 | **33** | 67,239,936 | Yes |
| 30 | 200 / 200 | 369 | 0 | 81,616,896 | Yes |
| 34 | 229 / 229 | 416 | 0 | 92,012,544 | Yes |

The existing JIRAM strip library has 162 PJ4 strips and 127 PJ24 strips overall; the 33 PJ24 overlaps above use only the candidate window. Its full PJ24 interval is 2019-12-26 16:49:28.238–20:41:32.704 UTC. Thus PJ24 offers the most immediate existing-strip comparison. The other seven supported passes have native JIRAM images and geometry but need JIRAM strips built for GUI comparison. Timestamp overlap does not establish spatial overlap, illumination compatibility or common resolution; those remain processing-stage measurements.

Two naming traps make date-based selection essential. JIRAM `orbit_dir=3` contains 3,900 records from **2016-08-26 through 2016-08-28**, whereas JunoCam PJ3 is **2016-12-11**. Current `KernelSet.for_orbits([3])`, driven by JIRAM label kernel references, does not support that December JunoCam window. In PJ34, the JIRAM folder also contains the **June 7 Ganymede encounter**; the Jupiter comparison window here is **June 8**. Choosing the minimum-altitude JIRAM record without filtering target/date would select the wrong encounter.

The date-matching kernel files below were inspected with SPICE `ckcov(-61000, needav=False, level='INTERVAL', tol=0, timsys='TDB')` and `spkcov(-61)`. Each listed pair has an actual interval containing its entire candidate window. Exact subsecond interval endpoints and local byte sizes are retained in `jiram_kernels` in the JSON; coverage was not inferred solely from the filenames.

| PJ | Local CK filename | Local SPK filename |
| --- | --- | --- |
| 5 | `juno_sc_rec_170326_170327_v01.bc` | `spk_rec_170228_170422_170427.bsp` |
| 6 | `juno_sc_rec_170514_170520_v01.bc` | `spk_rec_170422_170608_170621.bsp` |
| 8 | `juno_sc_rec_170827_170902_v01.bc` | `spk_rec_170728_170918_170922.bsp` |
| 12 | `juno_sc_rec_180401_180407_v01.bc` | `spk_rec_180307_180429_180504.bsp` |
| 18 | `juno_sc_rec_190211_190213_v01.bc` | `spk_rec_190118_190312_190319.bsp` |
| 24 | `juno_sc_rec_191222_191228_v01.bc` | `spk_rec_191201_200124_200129.bsp` |
| 30 | `juno_sc_rec_201107_201108_v01.bc` | `spk_rec_201014_201205_201208.bsp` |
| 34 | `juno_sc_rec_210606_210612_v01.bc` | `spk_rec_210513_210630_210707.bsp` |

The existing kernel selection also successfully evaluated both spacecraft-to-Jupiter frame transformation and aberration-corrected Juno position at each of those eight centres. This establishes basic frame/ephemeris availability; full JunoCam detector projection and every exposure/framelet time still require processing validation. Static files include the leap-second, planetary constants, frame, clock and planetary SPK kernels. The JunoCam IK `juno_junocam_v03.ti` is local and is loaded separately by the camera path.

## Exact acquisition bounds and incremental-write hazards

Inspection of [mirror.py](../../src/jiram_catalog/junocam/mirror.py) shows that the stock `junocam mirror` CLI cannot express an exact product-ID set or UTC interval. It reads `manifest_files.parquet`, so its downloads include **every retained processing version**. `--orbits` selects campaigns; `--doy` limits image selection to a day-of-year bucket, not a target or exact UTC interval. With `--kinds labels,data`, labels still include every level/day of the selected orbit. `--kinds data` does fetch only the data selection's prerequisite labels, but the image selection remains all-version and day-based. If the preferred manifest is missing, `mirror_files` first builds and writes a new manifest.

Exact acquisition can use the already existing lower-level `_mirror_kind(kind, rows, root, jobs)` with a DataFrame containing **only the lead's accepted exact preferred rows**: labels first, then data, root from `junocam_root(mirror)`, and `jobs=2` within the implementation's maximum of four. That function does not write the manifest or indexes. It does write the normal native mirror locations and download logs. It is a private API rather than a supported CLI contract; the lead can instead add a bounded public entry point under a separate implementation spec. No such acquisition call was executed here. Never replace the shared all-version manifest with a filtered temporary table to constrain a download.

Before an acquisition, resolve accepted IDs against `manifest.parquet` and assert exact one-to-one membership, `level='RDR'`, expected pass, version, source URL, byte count and checksum. Preserve that frozen ID list separately. Read the cached label for each product before downloading its native file; the inventory supplies expected image bytes and image MD5 values for all 337 records. The downloader checks size against the sibling label but does **not** verify image MD5. Its `bytes_downloaded` value sums destination sizes including already-complete files, so it is not a reliable measure of new network transfer.

The existing writer behavior matters even with safe acquisition bounds:

- `junocam.index.build_index`, `quality.build_quality` and `geo.build_geo` replace all rows for selected passes while retaining other passes. An incomplete rebuild of a selected pass can remove previously indexed same-pass records. Their shared `.parquet.tmp` + replace operations do not provide cross-process transaction locking; concurrent independent runs can lose one another's updates.
- `junocam.strips.update_index` replaces the selected pass's entire JunoCam group, retaining other instruments. It also **deletes old selected-pass NetCDFs absent from the fresh selection**, and removes unlisted NetCDFs from that selected orbit's JunoCam strip directory. A partial same-pass build is therefore destructive within that pass. Existing PJ4 data are preserved only if that pass is not selected or a preservation-aware execution path is established.
- `_mirror_kind` preserves files it considers complete, but unlinks incomplete destinations before retrying. The expansion metadata cache used here is outside the normal native product tree; label discovery will not index these reconnaissance samples accidentally.

These are audit findings, not repaired behavior. The companion pipeline audit and the lead's fixed execution gates should resolve safe incremental operation before shared-index or strip writes.

## Read-only reproduction

Run from the repository with the existing environment. This verifies the preferred-version selection, reconstructs all nine inferred windows, checks cached label hashes/times/bytes, and reproduces the independent 194-product option. It prints only; it neither downloads nor writes indexes.

```bash
uv run python - <<'PY'
import hashlib, json
from pathlib import Path
import pandas as pd
from jiram_catalog.junocam.labels import load_pvl

root = Path('../jiram_mirror').resolve()
base = root / 'junocam/expansion_2026-09-08'
record = json.loads((base / 'inventory/inventory_measurements.json').read_text())
observed = pd.DataFrame(record['observations'])
m = pd.read_parquet(root / 'junocam/manifest/manifest.parquet')
a = pd.read_parquet(root / 'junocam/manifest/manifest_files.parquet')
latest = a.sort_values(['level', 'product_stem', 'version', 'volume'])
latest = latest.drop_duplicates(['level', 'product_stem'], keep='last')
assert set(latest.product_id) == set(m.product_id)
r = m[(m.level == 'RDR') & (m.target_name == 'JUPITER')].copy()
r['altitude'] = r.index_row_json.map(
    lambda v: float(json.loads(v)['SPACECRAFT_ALTITUDE'].split()[0]))
parts = []
for item in record['summaries']:
    g = r[r.orbit == item['orbit']]
    positive = g[g.altitude > 0]
    centre = positive.loc[positive.altitude.idxmin(), 'start_time']
    selected = g[(g.start_time - centre).abs() <= pd.Timedelta('90min')]
    parts.append(selected)
    assert len(selected) == item['preferred_count']
selected = pd.concat(parts)
assert set(selected.product_id) == set(observed.product_id)
for row in observed.itertuples():
    p = Path(row.label_path)
    assert hashlib.md5(p.read_bytes()).hexdigest() == row.label_md5
    label = load_pvl(p)
    assert int(label['RECORD_BYTES']) * int(label['FILE_RECORDS']) == row.image_bytes
    assert pd.to_datetime(label['START_TIME'], utc=True) == pd.to_datetime(row.start_time, utc=True)
t = pd.read_csv(base / 'evidence/JNOJNC_0035_EXTRAS_RDR_TIMING_OFFSETS.TXT')
z = observed.merge(t[['PRODUCT_ID', 'OFFSET']].drop_duplicates(),
                   left_on='product_id', right_on='PRODUCT_ID',
                   how='left', validate='one_to_one')
finite = pd.to_numeric(z.OFFSET, errors='coerce').notna()
option = z[(z.orbit != 3) & (z.filter_code == 'C') & (z.samples == 1648)
           & (z.sampling_factor == 1) & (z.lines % 384 == 0) & finite]
assert len(observed) == 337 and observed.image_bytes.sum() == 11623349248
assert len(option) == 194 and option.image_bytes.sum() == 7854710784
print(option.groupby('orbit').agg(count=('product_id', 'size'),
                                 native_bytes=('image_bytes', 'sum')))
PY
```

To recheck local kernel coverage without downloads or metadata writes, load `KernelSet.static_paths(root)`, use each JSON entry's `candidate_kernels` filenames under `spice/ck` or `spice/spk`, call `spiceypy.ckcov`/`spkcov` with the arguments above, and compare the interval endpoints with `str2et(centre ± 90 minutes)`. The JSON stores the exact checked intervals as UTC and the corresponding `window_covered` result. For JIRAM, select `frames.start_time` within those same windows; count real `(root / label_path).with_suffix('.IMG')` files, match geometry by `product_id`, and select JIRAM strip intervals satisfying `time_start <= end` and `time_end >= start`.

## Judgment calls and unresolved limits

- Used equal ±90-minute windows around the minimum-positive-altitude **Jupiter label observation** to compare costs across passes. Alternatives were whole orbit campaigns, whole UTC days or the archive timing swaths; the first two are too broad for a bounded inventory, and the swaths remain separate evidence rather than interchangeable time bounds.
- Counted preferred RDR observations separately from all-version files and physical bands. Used exact label-declared uncompressed native sizes instead of archive tarball sizes, HTTP headers or inferred average image sizes. Derived-product disk and processing costs remain unmeasured.
- Retrieved all 337 small candidate labels so sums, sampling dimensions and checksums are exact. Used two throttled metadata workers, wrote only new expansion metadata, and did not acquire native images.
- Compared UTC-normalized label times, avoiding a false mismatch between timezone-aware PVL times and the manifest's naive UTC timestamps. No timing corrections were applied again.
- Used exact versioned timing-table joins; neither a `NULL` entry nor an absent entry was treated as successful navigation assessment. The 194-row option is an inventory result, not a scientific clearance or final acquisition decision.
- Flagged the PJ24 partial product and 14 summed methane products explicitly. Exclusion versus a separately validated specialized processing path remains the lead's choice; no policy was changed.
- Measured JIRAM overlap by actual dates and verified IMG files, rather than trusting orbit-folder numbering or stale presence flags. Checked actual CK/SPK intervals for dated local candidates, while leaving missing PJ3 navigation and all spatial co-coverage unresolved.
- Identified shared-index replacement, concurrency and strip deletion hazards without repairing them. Final accepted IDs, instrument evidence, resource budget and execution/preservation gates remain with the lead.
