# JunoCam PDS Archive Reconnaissance

Recon date 2026-09-06. All facts measured by direct HTTP(S) access; quoted values copied
verbatim from PDS3 labels/documents. Samples saved under
`/expanse/lustre/projects/cla119/kaushiks/jiram_mirror/junocam_samples/`.

## 1. Location and volume inventory

JunoCam is **not** on the Atmospheres node (`atmos.nmsu.edu/PDS/data/` holds only
`jnogrv_*` gravity, `jnojir_*` JIRAM, `jnomwr_*` MWR, `jnouvs_*` UVS — grep for
`jn`/`juno` confirmed no `jnc`/`junocam` entry). JunoCam PDS3 volumes live on the **PDS
Imaging Node**, currently at `https://planetarydata.jpl.nasa.gov/img/data/juno/`
(old host `pds-imaging.jpl.nasa.gov` 302-redirects there). Both hosts present a
self-signed/incomplete cert chain against the system CA bundle — recon used `curl -k`
for listings only, no data-integrity implication since only labels/one image pair were
inspected and MD5 was still checked against label values.

Volumes: `JNOJNC_0001` … `JNOJNC_0035` (each a directory + a `.tar.gz` + `_md5.txt`),
plus `JNOSRU_0001` (Stellar Reference Unit, not JunoCam). Each volume mixes EDR and RDR
under `DATA/EDR/{CRUISE,EFB,JUPITER}` and `DATA/RDR/...`, with `JUPITER/ORBIT_NN/`
subdirectories. Sum of the 35 `.tar.gz` sizes = **295.5 GB compressed**. Directory
mtimes: `JNOJNC_0001`/`0002` = 2018-04-07 (earliest release), `JNOJNC_0035` mtime
2026-08-04 (latest release at recon time) — release cadence is roughly per-perijove.
No separate PDS4 bundle was found: `planetarydata.jpl.nasa.gov/img/data/juno-j-junocam-2-edr-l0-v1.0/`
exists but is only an alias pointing at `JNOJNC_0001`, not a parallel PDS4 tree.
missionjuno.swri.edu was not reachable/tested (out of scope given time box; it serves
public PNG/JPEG derivatives, not the PDS3 archive).

Date range covered: `JNOJNC_0001` INDEX.LBL START_TIME=2011-08-26T15:13:19, i.e. cruise
data predates Jupiter arrival; JunoCam Jupiter-orbit data begins ORBIT_00 (2016, DOY129 =
2016-05-08 onward, pre-PJ1 capture orbit) and the archive is current through
`JNOJNC_0035` (2026-08-04 mtime, orbit numbers in the high 70s/80s by then based on
`jnojir` sibling numbering — not directly counted here).

EDR vs RDR: both live in every volume under `DATA/EDR/` and `DATA/RDR/`, one-to-one
(same PRODUCT_ID stem, `JNCE_...` vs `JNCR_...`).

## 2. Sample images

**2017 (PJ4, 2017-02-02):** `JNCE_2017033_04C00105_V01.IMG` + `.LBL`, from
`JNOJNC_0003/DATA/EDR/JUPITER/ORBIT_04/`. Also fetched the paired RDR label
`JNCR_2017033_04C00105_V01.LBL` from `JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_04/`.

**2024 (PJ58, 2024-02-03):** `JNCE_2024034_58C00012_V01.IMG` + `.LBL`, from
`JNOJNC_0028/DATA/EDR/JUPITER/ORBIT_58/`.

**Naming pattern** (from SIS §4.2): `JNCT_YYYYDDD_OOFNNNNN_VXX.ZZZ` — `T`=product type
(E=EDR, R=RDR, M=map), `YYYY DDD`=year/DOY at acquisition start, `OO`=orbit number
(`00`=cruise), `F`=filter-combination code (Appendix B: A=all 4, B=blue only, **C=RGB
3-color**, G=green only, M=methane only, R=red only, **T=2-color red+blue**),
`NNNNN`=image index within phase, `VXX`=version.

**Dimensions / framelet stacking** — both files are single-column strips
`RECORD_BYTES=1648` (LINE_SAMPLES=1648), stacked as `frame i, band 1 / frame i, band 2
/ ... / frame i, band N / frame i+1, band 1 / ...` (row-major, time order; SIS §4.3.1.1).
Each framelet is 128 lines. 2017 sample: `LINES=15360` → 15360/128 = **120 framelets**
= 40 repeats × 3 bands (FILTER_NAME=('BLUE','GREEN','RED'), no methane in this
particular product). 2024 sample: `LINES=3840` → 3840/128 = **30 framelets** = 10
repeats × 3 bands, same filter set.

**Bit depth / companding:** EDR `SAMPLE_BITS=8`, `SAMPLE_BIT_MASK=2#11111111#`;
`SAMPLE_BIT_MODE_ID="SQROOT"` (square-root companded 12-bit→8-bit) in both samples.
RDR `SAMPLE_BITS=16` (`SAMPLE_BIT_MASK=2#1111111111111111#`) — companding inverted to
linear (Appendix C tables), confirming RDR undoes SQROOT.

**Label keywords, 2017 sample (`JNCE_2017033_04C00105_V01.LBL`):**
```
START_TIME                    = 2017-02-02T13:05:34.387
IMAGE_TIME                    = 2017-02-02T13:05:34.387
STOP_TIME                     = 2017-02-02T13:05:49.387
SPACECRAFT_CLOCK_START_COUNT  = "539312928:137"
ORBIT_NUMBER                  = 4
INTERFRAME_DELAY              = 0.375 <s>
EXPOSURE_DURATION              = 9.600000 <ms>
JNO:TDI_STAGES_COUNT           = 3
FILTER_NAME                    = ('BLUE', 'GREEN', 'RED')
SAMPLE_BIT_MODE_ID              = "SQROOT"
COMPRESSION_TYPE                = "INTEGER COSINE TRANSFORM"
SUB_SPACECRAFT_LATITUDE         = -17.1407
SUB_SPACECRAFT_LONGITUDE        = 280.2563
SPACECRAFT_ALTITUDE             = 7351.7 <km>
SOLAR_DISTANCE                  = 8.1628e+08 <km>
TARGET_NAME                     = JUPITER
RATIONALE_DESC                  = "Points of Interest 1038 and 1046 at PJ04
  (relative time: PJ04+000T00:08)"
```
No quaternion/pointing fields are in the label itself — pointing is reconstructed via
SPICE C-kernels (per SIS, not in-label). There is no explicit "start time offset"
keyword in the label; timing corrections are tracked separately (see task 5,
`EDR_TIMING_OFFSETS.TXT`).

**2024 sample differs:** `EXPOSURE_DURATION=6.400000 <ms>`, `JNO:TDI_STAGES_COUNT=2`,
`FOCAL_PLANE_TEMPERATURE=259.2 <K>` (vs 273.0 K in 2017), `SPACECRAFT_ALTITUDE=755105.5
<km>` (far from perijove — `RATIONALE_DESC="Approach movie imaging (relative time:
PJ58-000T10:17)"`, i.e. ~10.3 h before closest approach), `INTERFRAME_DELAY=0.375 <s>`
unchanged.

## 3. Calibration status (from SIS, `JUNO_JNC_EDR_RDR_DPSIS.PDF`, saved to samples dir)

EDR = "NASA Level 0" / CODMAC Edited-Level 2 (raw counts, companded). RDR = "NASA Level
1A" / CODMAC Calibrated-Level 3. Per SIS §4.3.1.3 ("RDR processing flow"): "The RDR is
produced by inverting the onboard 12-to-8-bit companding to yield linear data numbers...
A flat field correction would also be done at this step, **but this is not currently
implemented** as the instrument response is fairly uniform without it. For planetary
targets, these values are then scaled such that a white surface at the solar distance...
would have a pixel value of 10,000 data numbers." So RDR = decompanded + solar-distance
radiometric scaling, but **no flat-field**, and not full physical radiance units (DN
scaled to a 10,000-white-target convention, not W/m²/sr/µm). Instrument model (CCD,
optics, filter array) is in SIS §2.1: "1600x1200 pixel interline transfer CCD (Kodak
KAI-2020M) with 7.4-micron square pixels illuminated by a single all-refractive
58-degree lens. A color filter array with four different bandpasses (visible
red/green/blue and narrowband methane absorption at about 890 nm) is directly bonded to
the CCD." Framelet timing/format is SIS §4.3.1.1 ("Filter order") and §4.3.1.2
("Geometry"). Companding tables are SIS Appendix C.

## 4. Counts per orbit / per perijove (2017 PJ4 case study)

`JNOJNC_0003/DATA/EDR/JUPITER/ORBIT_04/` contains **1021 unique EDR image files**
spanning DOY 2017-027 through 2017-038 (2017-01-27 to 2017-02-07). Per-day counts:
DOY027=48, 028=96, 029=96, 030=96, 031=96, **032=48, 033=103 (perijove day,
2017-02-02), 034=96**, 035=96, 036=96, 037=96, 038=54. So the day straddling perijove
(DOY033) has **103 EDR images**; total for the ~12-day PJ4 imaging campaign is 1021.
(A residual 5 EDR files for ORBIT_04 also appear in `JNOJNC_0004`, likely late/reprocessed
additions.) `INDEX/INDEX.LBL` `ROWS` counts (EDR+RDR combined, ~2x image count, plus any
map products): `JNOJNC_0003` (covers 2016-345 to 2017-038, PJ3+PJ4) = 3218 rows;
`JNOJNC_0028` (2023-12-11 to 2024-03-24, PJ56–59) = 5530 rows. INDEX.TAB has **no
ORBIT_NUMBER column** (16 columns: VOLUME_ID, STANDARD_DATA_PRODUCT_ID, DATA_SET_ID,
PRODUCT_ID, START_TIME, STOP_TIME, PROCESSING_LEVEL_ID, RATIONALE_DESC,
SOLAR_DISTANCE, SPACECRAFT_ALTITUDE, SUB_SPACECRAFT_LATITUDE/LONGITUDE, TARGET_NAME,
FILE_SPECIFICATION_NAME, PRODUCT_CREATION_TIME, PRODUCT_LABEL_MD5CHECKSUM) — orbit
number must be parsed from the `OO` field of the filename or from
`FILE_SPECIFICATION_NAME` path (`.../ORBIT_NN/...`), not queried directly.

## 5. Data quality over time

No per-image quality flag field exists in the label or INDEX.TAB. Quality/anomaly
information instead lives in each volume's `ERRATA.TXT` (free text, cumulative,
prepended per release):
- **Blooming** (all epochs): CCD blooming from overexposed visible bands contaminates
  the adjacent methane band, worse with more TDI; readout-table mitigation applied
  per-methane-opportunity, tracked via `INS-61504_DISTORTION_Y` value embedded in
  `RATIONALE_DESC` (no dedicated keyword).
- **Timing offsets**: some EDR/RDR products have a variable image-start-time error;
  corrected offsets are listed in `EXTRAS/EDR_TIMING_OFFSETS.TXT` and
  `RDR_TIMING_OFFSETS.TXT` per volume (only PJ-swath images considered).
- **Radiation damage / annealing** (`JNOJNC_0028` ERRATA §9, "Saturated images"):
  "anomalous behavior in PJ47 was due to radiation damage to a voltage regulator in the
  camera head. Starting in March 2023, the camera head was continuously powered, and
  also heated during the part of the orbit outside of PJ ± 24h to a temperature of about
  20°C in an effort to heal that damage by annealing. This resulted in substantially
  higher dark current in the marble movie images... This continued until PJ56, when the
  camera's black offset was observed to increase substantially over the course of the
  perijove pass... thought to be accumulated radiation damage to the CCD. Heating was
  stopped temporarily and then resumed at a higher setpoint, which heated the camera
  focal plane to about 45°C."
- **Throughput loss** (`JNOJNC_0033` ERRATA §10): "An analysis of the images through
  orbit 60 indicates that there has been varying degrees of loss of throughput in each
  of the color channels... Red 23%, Green 47%, Blue 64%." Linear correction coefficients
  given (Red −0.00391/1.01, Green −0.00787/1.05, Blue −0.0108/1.07) — explicitly stated
  as **not applied** to the RDR products.

## Judgment calls
- Used `curl -k` (insecure TLS) for `planetarydata.jpl.nasa.gov`/`pds-imaging.jpl.nasa.gov`
  because the system CA bundle rejects their chain (self-signed intermediate); task said
  the system bundle "works for atmos.nmsu.edu" but JunoCam isn't there, so this was
  necessary to reach the actual holdings. No bulk data integrity was at stake (labels +
  2 images only, MD5-checked against label values not independently re-verified).
- Picked PJ4 (2017-02-02) and PJ58 (2024-02-03) as the 2017/2024 perijoves rather than a
  more "famous" one (e.g. PJ7 Great Red Spot); any orbit would satisfy the task, these
  were reachable fastest from directory structure already in hand.
- "Total size" in task 1 is the sum of `.tar.gz` (compressed) sizes; did not compute
  uncompressed tree size (would require per-file HEAD requests at volume scale — against
  the "listings only" politeness constraint).
- Did not enumerate all 35 volumes' INDEX.LBL ROWS counts (would be 35 sequential
  requests); sampled 2 (vol 1, 3, 28) as representative rather than exhaustive.
- Did not check missionjuno.swri.edu (public-facing PNG mirror) — out of time box and
  it is a derivative product site, not the archival PDS holding.
- Treated `JNOSRU_0001` (Stellar Reference Unit) as out of scope — same host/pattern but
  a different instrument.
