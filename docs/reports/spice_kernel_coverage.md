# SPICE kernel coverage for Juno JIRAM, 2016-2025 — measured inventory

Method: directory listings only (Apache mod_autoindex HTML, parsed for name/mtime/size),
fetched sequentially with `curl --cacert <certifi bundle>` against naif.jpl.nasa.gov.
No bulk kernel downloads were performed. Raw listing HTML and parsing scripts are in the
session scratchpad (not under a tracked path).

Sources:
- PDS SPICE archive: `https://naif.jpl.nasa.gov/pub/naif/pds/data/jno-j_e_ss-spice-6-v1.0/jnosp_1000/`
- Operational NAIF kernels: `https://naif.jpl.nasa.gov/pub/naif/JUNO/kernels/`

## Task 1 — Yearly metakernels and archive stop date

`extras/mk/` contains `juno_YYYY_vVV.tm` for YYYY = 2011 through 2026 (2026 is a
partial/in-progress year). Version counts observed (latest version per year is the
one currently authoritative):

| Year | Versions present | Latest file | Latest mtime |
|---|---|---|---|
| 2011 | v01-v07 (7) | juno_2011_v07.tm | 2024-01-30 |
| 2012 | v01-v07 (7) | juno_2012_v07.tm | 2024-01-30 |
| 2013 | v01-v07 (7) | juno_2013_v07.tm | 2024-01-30 |
| 2014 | v01-v07 (7) | juno_2014_v07.tm | 2024-01-30 |
| 2015 | v01-v07 (7) | juno_2015_v07.tm | 2024-01-30 |
| 2016 | v01-v09 (9) | juno_2016_v09.tm | 2024-01-30 |
| 2017 | v01-v12 (12) | juno_2017_v12.tm | 2024-01-30 |
| 2018 | v01-v10 (10) | juno_2018_v10.tm | 2024-01-30 |
| 2019 | v01-v08 (8) | juno_2019_v08.tm | 2024-01-30 |
| 2020 | v01-v06 (6) | juno_2020_v06.tm | 2024-01-30 |
| 2021 | v01-v06 (6) | juno_2021_v06.tm | 2024-01-30 |
| 2022 | v01-v06 (6) | juno_2022_v06.tm | 2024-01-30 |
| 2023 | v01-v04 (4) | juno_2023_v04.tm | 2024-09-17 |
| 2024 | v01-v06 (6) | juno_2024_v06.tm | 2026-01-20 |
| 2025 | v01-v04 (4) | juno_2025_v04.tm | 2026-08-04 |
| 2026 | v01 (1, partial) | juno_2026_v01.tm | 2026-08-04 |

`mkinfo.txt` (4.1K) describes the naming/priority convention only; it does not enumerate
files — the table above comes from the `extras/mk/` directory listing itself.

`aareadme.txt` states the archive is "a single virtual volume... covering from launch,
2011 AUG 05, through the end time of the latest spacecraft orientation file supplied in
the data set" — i.e. it is a rolling/cumulative archive, not a fixed-vintage one.

**Coverage verdict: yes, the archive covers all of 2016-2025.** `juno_2025_v04.tm`
(created 2026-08-04) lists reconstructed CKs through `juno_sc_rec_251221_251227_v01.bc`
and reconstructed SPKs through `juno_rec_251206_260108_260115.bsp`; `juno_2026_v01.tm`
extends CK coverage to `juno_sc_rec_260118_260124_v01.bc` (stop ~2026-01-24). So archive
stop date at the time of this check is **2026-01-24**, well past the 2016-2025 window
this task cares about.

## Task 2 — CK/SPK file counts, sizes, date ranges, gaps

Counts/bytes are for **all files** in each directory (kernels + `.lbl` labels for the
PDS archive; kernels + `.lbl`/`.txt` for ops), from directory-listing sizes (K/M rounding
as displayed by Apache, so bytes are approximate to the K/M unit shown).

| Directory | Files | Total bytes | Total (GiB) |
|---|---|---|---|
| PDS archive `data/ck/` | 7,697 | 53,508,100,788 | 49.8 |
| PDS archive `data/spk/` | 203 | 1,389,471,063 | 1.29 |
| NAIF ops `kernels/ck/` | 1,531 | 46,567,905,779 | 43.4 |
| NAIF ops `kernels/spk/` | 1,455 | 2,856,195,184 | 2.66 |

Reconstructed spacecraft-bus CK (`juno_sc_rec_*.bc`, `.bc` files only):
- PDS archive: **750 files, 28,633,255,117 bytes (26.7 GiB)**. Date range (from filename
  date stamps): 2011-09-15 to 2026-01-24.
- NAIF ops: **1,115 files, 40,450,822,949 bytes (37.7 GiB)**. Date range: 2011-08-19 to
  2026-08-29 (ops is live/ahead of the archive, as expected).

Reconstructed spacecraft SPK (`juno_rec_*.bsp` in the archive; `spk_rec_*.bsp` in ops —
see Task 5):
- PDS archive: 92 files, date range 2011-08-05 to 2026-02-10, **0 gaps >7 days**.
- NAIF ops: 99 files, date range 2011-08-05 to 2026-08-24, **0 gaps >7 days**.

CK weekly-series gaps >7 days (PDS archive `juno_sc_rec_*.bc`, 6 found):
| Gap start (prev end) | Gap end (next start) | Days |
|---|---|---|
| 2014-03-13 | 2014-03-25 | 12 |
| 2022-12-15 | 2022-12-29 | 14 |
| 2024-06-20 | 2024-07-01 | 11 |
| 2025-04-05 | 2025-04-13 | 8 |
| 2025-11-22 | 2025-11-30 | 8 |
| 2025-12-27 | 2026-01-04 | 8 |

Ops tree shows the same gaps plus edge effects (initial 2011-08-19→09-15 mission-start
gap; a 2026-01-23→02-01 gap near the live data horizon) — 8 gaps >7 days total.
Each PDS-archive gap coincides with a `juno_sc_raw_*.bc` (lower-accuracy, non-reconstructed)
CK covering the same span in the corresponding yearly metakernel — e.g. `juno_sc_raw_250405_250414.bc`
covers the 2025-04-05→04-13 gap. Per NAIF's stated priority rule, raw CKs are only used
where reconstructed CKs don't provide coverage, so pointing during these windows is
lower-fidelity than the surrounding reconstructed weeks.

## Task 3 — Ephemeris/fk/ik/sclk/lsk/pck sizes and latest versions

Latest metakernel (`juno_2025_v04.tm`/`juno_2026_v01.tm`) loads:
- `de442s.bsp` — 32,505,856 B (31M as listed)
- `jup380s.bsp` — 54,525,952 B (52M) **and** `jup388s.bsp` — 35,651,584 B (34M), loaded together
- `juno_v12.tf` (fk) — 154,624 B archive / 152,576 B ops (151-149K)
- `juno_jiram_v02.ti` (ik) — 20,480 B archive / 18,432 B ops (20K/18K)
- `jno_sclkscet_00210.tsc` (sclk) — 43,008 B, archive mtime 2026-08-04; ops already has a
  newer `JNO_SCLKSCET.00211.tsc` (also 43,008 B, mtime 2026-08-31) not yet folded into the archive
- `naif0012.tls` (lsk) — 6,554 B archive / 5,222 B ops — **only LSK version present in
  either tree**
- `pck00010.tpc` (pck) — 126,976 B archive / 125,952 B ops — **only PCK version in the
  archive** (ops also still hosts superseded `pck00008.tpc`, `pck00009.tpc`)

Older ephemeris SPKs still on disk in the PDS archive (superseded, not used by latest mk):
`de436s.bsp` (22.0 MB), `de438s.bsp` (22.0 MB), `de440s.bsp` (32.5 MB); `jup310.bsp`
(976,224,256 B — the single largest file in the whole ck+spk collection) and `jup363.bsp`
(54.5 MB).

**Judgment call / gotcha for a mirror plan:** the PDS-archive `ik/`, `fk/`, `lsk/`,
`pck/` and (per spot check) `ck/` directories hold only the **current latest** version of
each file — superseded versions are removed as new ones are archived, they are not kept
side by side. Concretely: the task's own example JIRAM label (2017-02-02) cites
`juno_jiram_v01.ti`, but the archive's `ik/` directory today contains only
`juno_jiram_v02.ti` — v01 is gone. Likewise, spot-checking `juno_sc_rec_250615_250621_*`
and `juno_sc_rec_250629_250705_*` shows only the `_v02.bc`/`.lbl` pair present, no `_v01`.
A mirror built from the current directory listing will not reproduce the exact kernel
file named in an old label; it will only have the current (presumably equal-or-better
accuracy) replacement. This matters for byte-exact reprocessing provenance, not
necessarily for geometric accuracy.

## Task 4 — CSV manifest

Written to `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror/spice/manifest/naif_juno_kernels.csv`
(11,246 data rows + 1 header row, 2.1 MB). Columns: `source, kind, file_name, bytes, mtime, url`
(`source` = `naif_ops` | `pds_archive`; one row per file returned by the corresponding
directory listing — includes non-kernel files such as `aareadme.txt` that live in those
directories).

Row counts per kind/source:

| source | ck | spk | fk | ik | lsk | pck | sclk | total |
|---|---|---|---|---|---|---|---|---|
| naif_ops | 1,531 | 1,455 | 9 | 25 | 5 | 4 | 212 | 3,241 |
| pds_archive | 7,697 | 203 | 5 | 25 | 3 | 3 | 69 | 8,005 |

Grand total: 11,246 rows.

## Task 5 — Operational vs. PDS-archive file naming

Checked directly by grepping both listings for the same date-tagged file:

- **CK: unchanged.** `juno_sc_rec_170129_170204_v01.bc` appears verbatim in both trees
  — `naif_ops/ck/juno_sc_rec_170129_170204_v01.bc` (mtime 2017-02-08) and
  `pds_archive/data/ck/juno_sc_rec_170129_170204_v01.bc` (mtime 2017-08-25, paired with a
  `.lbl`). Same filename, different mtime/size-formatting only.
- **SPK: renamed.** The task's own known-fact kernel `spk_rec_170106_170228_170307.bsp`
  is the **operational** name (`naif_ops/spk/spk_rec_170106_170228_170307.bsp`, mtime
  2017-03-08). In the PDS archive the identical-coverage file is renamed on ingest to
  `pds_archive/data/spk/juno_rec_170106_170228_170307.bsp` (mtime 2017-08-25). Every
  reconstructed-SPK file in the archive uses the `juno_rec_` prefix; every one in ops
  uses `spk_rec_`. A mirror/translation layer must map `spk_rec_* <-> juno_rec_*` by
  date-range suffix, not by filename equality.
- Also observed in passing (not asked, noted for completeness): ops SCLK files use
  uppercase `JNO_SCLKSCET.NNNNN.tsc`; the archive uses lowercase
  `jno_sclkscet_NNNNN.tsc` — another silent rename between the two trees.
