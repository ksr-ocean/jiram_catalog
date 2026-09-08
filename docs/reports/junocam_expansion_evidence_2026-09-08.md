# JunoCam multi-pass expansion: instrument and product evidence

This is the pre-acquisition reconnaissance record. See the
[expansion delivery record](junocam_expansion_2026-09-08.md) for subsequent
acquisition, individual clearances, repairs and measured mapped products.

Evidence retrieved **2026-09-08 UTC**. This is reconnaissance under the
[accepted expansion](../specs/2026-09-08_junocam_expansion.md) and
[evidence spec](../specs/2026-09-08_junocam_evidence_recon.md), with no policy,
application, test or existing native-product changes. **No observation is
cleared by this report.** The lead must combine the evidence below with
measured image quality and navigation before identifying any newly eligible ID.

The candidate prime-mission passes have affirmative mission-team evidence
of normal camera operation. A bounded expansion of supported native RGB
observations is therefore worth pursuing. That evidence does not establish
constant radiometric response, perfect navigation, or that every archived
product is complete. In particular, the PDS throughput warning applies to
mission-long optical darkening, not solely the later electronic failures.

## Sources and what they establish

| Primary source, inspected directly | Evidence and limit |
| --- | --- |
| [NASA/JPL mission-team report, 21 July 2025](https://www.nasa.gov/missions/juno/nasa-shares-how-to-save-camera-370-million-miles-away-near-jupiter/) | Says JunoCam “operated normally” through its first 34 orbits and those images supported science papers. This is affirmative instrument-era evidence, stronger than silence in errata; it is not an individual pixel audit. |
| [Cumulative volume 0035 ERRATA](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/ERRATA.TXT) | Release-specific anomalies, old RDR companding, label revisions, timing treatment, methane readout changes and later radiation failures. Its header publication date is historical; its actual contents extend through the 2026 release. |
| [Original volume 0003 ERRATA](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/ERRATA.TXT) | PJ3/PJ4 originally had no new reported errata. Later cumulative corrections demonstrate why that statement cannot certify their original reductions. |
| [Timing description](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/EXTRAS/TIMING_OFFSETS_DESCRIPTION.TXT) and [RDR timing records](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/EXTRAS/RDR_TIMING_OFFSETS.TXT) | A per-product navigation assessment: numerical offset, zero when none was needed, or NULL when it could not be determined. This is not an instrument-health score. |
| [JunoCam EDR/RDR SIS](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/DOCUMENT/JUNO_JNC_EDR_RDR_DPSIS.PDF), §§2.1, 4.3.1.3, 4.3.2 and label dictionary | Native sampling, compression, exposure/TDI, RDR scaling and transmission-loss behavior. |
| [NAIF camera kernel v03](https://naif.jpl.nasa.gov/pub/naif/JUNO/kernels/ik/juno_junocam_v03.ti), Timing and Optical Distortion | Fixed 61.88 ms start bias, 1 ms frame-delay correction, possible approximately 20 ms jitter, and nominal detector geometry. |
| [Ravine et al., EPSC-DPS2025-1226](https://meetingorganizer.copernicus.org/EPSC-DPS2025/EPSC-DPS2025-1226.html), 9 July 2025 | Instrument-team analysis of optical throughput loss, distinct from electronics corruption. The text was inspected; its referenced figure images are absent from the fetched HTML, so no points were digitized. |
| [Native candidate labels](junocam_expansion_inventory_2026-09-08.md) | The independent inventory cached 337 preferred JUPITER RDR labels. This audit parsed every cached label and joined exact product IDs to the timing table, then read flagged and representative label bodies. No native IMG was acquired or measured here. |

Small source documents are preserved under
`<mirror>/junocam/expansion_2026-09-08/evidence/`; `retrievals.json` records
URL, retrieval UTC, byte count and SHA-256. The cumulative ERRATA is
50,524 bytes, SHA-256
`43baa2fa0966ca00a2d895d3a1b52e9eef42756c9554c377f77ed8ee6d30ca48`.
The durable `evidence/candidate_label_timing_audit.json` contains 337 rows
under `observations`, keyed by `product_id`: parsed label fields, direct URL,
label hashes, `timing_state`, `timing_offset_pixels`, `metadata_caveats`,
`full_resolution_rgb` and `methane_distortion_y_override`. Numerical offsets
are detector pixels, times are UTC, and no field asserts eligibility. The
JSON was cross-checked against the inventory's 337 cached labels.
Candidate labels remain in the inventory's separate cache. Web rendering of
PDS text failed, so the source bytes were read directly over HTTPS instead.

## Prime-mission operation and radiometry are separate questions

The NASA report places the onset of conspicuous radiation damage at PJ47.
It supports considering individual PJ3,5,6,8,12,18,24,30,34 observations for
screening; it does not justify making the entire first 34 passes eligible.
The labels and later errata still disqualify some products in that era.
[NASA/JPL report](https://www.nasa.gov/missions/juno/nasa-shares-how-to-save-camera-370-million-miles-away-near-jupiter/).

The throughput abstract analyzes outbound characterization images at roughly
272,000 km, displaying orbits 1–59 and normalizing channel signals to orbit 3.
It summarizes losses through orbit 60 as 23% red, 47% green and 64% blue.
The published response fits are `1.01−0.00391×orbit`,
`1.05−0.00787×orbit`, and `1.07−0.0108×orbit`, respectively. The authors
acknowledge orbit-to-orbit scatter and no correction for changing atmospheric
features or lighting. The evidence concerns mission-long response evolution;
it gives no validated start boundary at PJ47, no hard no-loss interval,
and no per-observation uncertainty. [Instrument-team abstract](https://meetingorganizer.copernicus.org/EPSC-DPS2025/EPSC-DPS2025-1226.html).

ERRATA B.10 says these fits have **not** been applied to RDR pixels. The
current configuration restricts its descriptive throughput entries to PJ47–60;
that lower bound is a repository choice unsupported by B.10 or the inspected
abstract. Do not interpret an earlier missing factor as measured unit response.
No correction is applied or proposed as a certified calibration here.
[ERRATA B.10](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/ERRATA.TXT).

The lead's response is to preserve the existing numeric display domain for
historical table compatibility while correcting its description: PJ47 is a
legacy display boundary, not the onset of optical response evolution. This
wording change neither certifies per-image factors nor treats earlier missing
factors as unit response, and applies no correction to archived pixels.

The SIS describes planetary RDRs as decompanded counts rescaled using solar
distance and commanded exposure, with a white reference assigned 10,000 DN;
its stated processing does not include a flat-field correction. Black-sky
RDRs use a different electron-count interpretation. Target and observing
rationale both need inspection, particularly for auroral/night-sky sequences;
a JUPITER label alone is not a calibration certificate. Retaining DN units
and a processing recipe is appropriate. Cross-pass mean brightness, color,
variance and spectral amplitude require response and illumination control;
clean pixels or an attractive color balance do not provide that control.
[SIS §4.3.1.3](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/DOCUMENT/JUNO_JNC_EDR_RDR_DPSIS.PDF).

## Exact timing-study windows and candidate coverage

These are the **published timing-study swath windows**, in UTC year/day-of-year
notation; they are not declarations that every image in the interval is
healthy, or bounds to reapply to corrected labels. The label audit uses the
inventory's separate ±90-minute window around each pass's minimum positive
JUPITER archive-altitude observation. It is a bounded candidate sample, not
an exhaustive pass census. [Timing description](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/EXTRAS/TIMING_OFFSETS_DESCRIPTION.TXT).

| Pass | Published swath start UTC | Published swath end UTC |
| --- | --- | --- |
| PJ3 | 2016-346T15:45:47.276 | 2016-346T18:17:25.134 |
| PJ5 | 2017-086T07:30:52.604 | 2017-086T10:41:15.757 |
| PJ6 | 2017-139T04:46:46.264 | 2017-139T07:55:59.859 |
| PJ8 | 2017-244T21:05:44.006 | 2017-244T23:06:10.446 |
| PJ12 | 2018-091T08:09:49.991 | 2018-091T11:46:58.421 |
| PJ18 | 2019-043T15:50:06.034 | 2019-043T19:32:41.868 |
| PJ24 | 2019-360T16:56:56.782 | 2019-360T19:59:15.760 |
| PJ30 | 2020-313T01:13:12.715 | 2020-313T04:04:28.760 |
| PJ34 | 2021-159T06:46:52.489 | 2021-159T10:17:19.322 |

All 337 labels parse; 323 describe 1648-column, unsummed 16-bit products,
while 14 describe 816-column products with sampling factor 2. The RGB columns
below refer to three-band BLUE/GREEN/RED native products, not rendered RGB
canvases. “Screenable” removes the old-companding and partial products listed
below; it is only a metadata screen. A finite timing value includes zero.

| Pass | All candidate labels | RGB products | Screenable RGB | Of those, finite timing record | RGB exposure range (ms) | RGB label interframe delay (s) | RGB lossy / Huffman |
| --- | ---: | ---: | ---: | ---: | --- | --- | --- |
| PJ3 | 40 | 22 | 19 | 19 | 3.2–16 | 0.374 | 15 / 7 |
| PJ5 | 17 | 15 | 15 | 12 | 6.4–12.8 | 0.37–0.371 | 13 / 2 |
| PJ6 | 36 | 32 | 32 | 32 | 6.4–12.8 | 0.373 | 30 / 2 |
| PJ8 | 25 | 21 | 21 | 20 | 6.4–6.4 | 0.372 | 18 / 3 |
| PJ12 | 34 | 27 | 27 | 19 | 3.2–9.6 | 0.378 | 22 / 5 |
| PJ18 | 44 | 35 | 35 | 23 | 3.2–28.8 | 0.371 | 30 / 5 |
| PJ24 | 39 | 30 | 29 | 18 | 3.2–28.8 | 0.374 | 28 / 2 |
| PJ30 | 51 | 39 | 39 | 27 | 3.2–28.8 | 0.37 | 36 / 3 |
| PJ34 | 51 | 43 | 43 | 43 | 3.2–19.2 | 0.371 | 38 / 5 |

Source: exact labels in the [inventory audit](junocam_expansion_inventory_2026-09-08.md),
joined to the [current timing table](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/EXTRAS/RDR_TIMING_OFFSETS.TXT). A larger candidate count is
not evidence of more independent realizations or a usable motion triplet.

Representative explicit IDs with supported metadata and finite timing follow.
They are examples for lead review, **not approved acquisition or clearance
lists**, and none has new measured signal/geometry in this reconnaissance.
Times are current label `START_TIME`, not SOURCE_PRODUCT_ID timestamps.

| Pass / linked latest product label | START_TIME UTC | Exposure / TDI | Archive offset (pixels) |
| --- | --- | --- | ---: |
| PJ3: [JNCR_2016346_03C00111_V03](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0012/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03C00111_V03.LBL) | 2016-12-11T17:04:08.180 | 16 ms / 5 | -7 |
| PJ5: [JNCR_2017086_05C00107_V02](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0012/DATA/RDR/JUPITER/ORBIT_05/JNCR_2017086_05C00107_V02.LBL) | 2017-03-27T08:53:09.653 | 12.8 ms / 4 | -5 |
| PJ6: [JNCR_2017139_06C00115_V02](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0012/DATA/RDR/JUPITER/ORBIT_06/JNCR_2017139_06C00115_V02.LBL) | 2017-05-19T06:03:04.775 | 12.8 ms / 4 | -7 |
| PJ8: [JNCR_2017244_08C00114_V02](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0012/DATA/RDR/JUPITER/ORBIT_08/JNCR_2017244_08C00114_V02.LBL) | 2017-09-01T21:49:21.520 | 6.4 ms / 2 | -5 |
| PJ12: [JNCR_2018091_12C00088_V02](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0012/DATA/RDR/JUPITER/ORBIT_12/JNCR_2018091_12C00088_V02.LBL) | 2018-04-01T09:45:55.040 | 3.2 ms / 1 | -4 |
| PJ18: [JNCR_2019043_18C00031_V02](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0012/DATA/RDR/JUPITER/ORBIT_18/JNCR_2019043_18C00031_V02.LBL) | 2019-02-12T17:25:32.936 | 3.2 ms / 1 | -5 |
| PJ24: [JNCR_2019360_24C00027_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0013/DATA/RDR/JUPITER/ORBIT_24/JNCR_2019360_24C00027_V01.LBL) | 2019-12-26T17:29:19.906 | 3.2 ms / 1 | -3 |
| PJ30: [JNCR_2020313_30C00025_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0016/DATA/RDR/JUPITER/ORBIT_30/JNCR_2020313_30C00025_V01.LBL) | 2020-11-08T01:47:45.794 | 3.2 ms / 1 | 4 |
| PJ34: [JNCR_2021159_34C00061_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0018/DATA/RDR/JUPITER/ORBIT_34/JNCR_2021159_34C00061_V01.LBL) | 2021-06-08T07:48:20.461 | 3.2 ms / 1 | -5 |

## Product-specific restrictions that survive normal instrument operation

**Old RDR companding is still present in some latest PJ3 products.** ERRATA
B.1 identifies `JUNOMAKEPDS.PY 0.4` with the earlier mismatched table and
version 0.5 or later with the corrected table. Software/version metadata
is still evidence about processing, not a replacement for tracing the actual
bytes when later releases describe label-only changes. Ten inspected preferred
PJ3 labels still name 0.4. The native bytes must not be granted a corrected
radiometric interpretation merely because the product is the latest available
or its signal looks clean. [ERRATA B.1](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/ERRATA.TXT).

| Latest affected PJ3 product | Label START_TIME UTC | Native bands |
| --- | --- | --- |
| [JNCR_2016346_03R00096_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03R00096_V01.LBL) | 2016-12-11T16:07:11.137 | RED |
| [JNCR_2016346_03R00097_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03R00097_V01.LBL) | 2016-12-11T16:09:12.344 | RED |
| [JNCR_2016346_03R00098_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03R00098_V01.LBL) | 2016-12-11T16:11:13.547 | RED |
| [JNCR_2016346_03C00116_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03C00116_V01.LBL) | 2016-12-11T17:22:17.163 | BLUE/GREEN/RED |
| [JNCR_2016346_03C00124_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03C00124_V01.LBL) | 2016-12-11T18:04:14.351 | BLUE/GREEN/RED |
| [JNCR_2016346_03R00128_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03R00128_V01.LBL) | 2016-12-11T18:12:50.104 | RED |
| [JNCR_2016346_03R00129_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03R00129_V01.LBL) | 2016-12-11T18:14:51.319 | RED |
| [JNCR_2016346_03R00130_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03R00130_V01.LBL) | 2016-12-11T18:16:52.592 | RED |
| [JNCR_2016346_03C00131_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03C00131_V01.LBL) | 2016-12-11T18:30:01.388 | BLUE/GREEN/RED |
| [JNCR_2016346_03M00132_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_03/JNCR_2016346_03M00132_V01.LBL) | 2016-12-11T18:31:02.091 | METHANE |

This is a reduction defect, distinct from damaged instrument electronics.
The conservative expansion choice is to omit these products. Rebuilding a
verified reduction from a native EDR would be separate calibration work.

**Partial data:** [JNCR_2019360_24C00039_V01](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0013/DATA/RDR/JUPITER/ORBIT_24/JNCR_2019360_24C00039_V01.LBL)
is explicitly described as lost/partial, starts at
2019-12-26T18:01:28.240 and stops at 18:01:28.614 UTC, and has 672×1648
samples. Its height is not a complete RGB frame sequence. Omit it even if
aggregate pixel statistics could otherwise appear finite. SIS §4.3.2 also
explains that data loss can produce zero-filled partial lines; finite
file size alone does not prove complete acquired content. [SIS](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/DOCUMENT/JUNO_JNC_EDR_RDR_DPSIS.PDF).

**Summed mode:** the following 14 methane products declare sampling factor 2
and 816 samples per line. The current reader explicitly rejects this shape;
nominal full-resolution geometry must not be treated as evidence of support.
These are mode limitations, not a diagnosis of an instrument malfunction.

- PJ3: `JNCR_2016346_03M00093_V03`, `JNCR_2016346_03M00095_V02`, `JNCR_2016346_03M00100_V03`, `JNCR_2016346_03M00102_V02`, `JNCR_2016346_03M00106_V02`, `JNCR_2016346_03M00108_V02`, `JNCR_2016346_03M00115_V03`, `JNCR_2016346_03M00119_V02`, `JNCR_2016346_03M00123_V02`, `JNCR_2016346_03M00125_V02`, `JNCR_2016346_03M00127_V03`
- PJ5: `JNCR_2017086_05M00103_V02`, `JNCR_2017086_05M00114_V02`
- PJ8: `JNCR_2017244_08M00115_V02`

**Methane readout position:** ERRATA B.5 describes bloom from overexposed
visible strips, worse with more TDI. A mitigation moves the methane readout
region; when `RATIONALE_DESC` supplies `INS-61504_DISTORTION_Y`, it must
replace the IK value for that image. Visible imaging retains the nominal
kernel values. This does not prove that moving the readout removed every
blooming artifact. [ERRATA B.5](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/ERRATA.TXT).

All 18 override-bearing products in this candidate sample specify 405.48,
against the loaded IK's 315.48. Their exact IDs are:

- PJ18: `JNCR_2019043_18M00021_V02`
- PJ24: `JNCR_2019360_24M00014_V01`, `JNCR_2019360_24M00016_V01`, `JNCR_2019360_24M00018_V01`, `JNCR_2019360_24M00020_V01`, `JNCR_2019360_24M00026_V01`
- PJ30: `JNCR_2020313_30M00010_V01`, `JNCR_2020313_30M00012_V01`, `JNCR_2020313_30M00016_V01`, `JNCR_2020313_30M00018_V01`, `JNCR_2020313_30M00020_V01`, `JNCR_2020313_30M00049_V01`, `JNCR_2020313_30M00052_V01`
- PJ34: `JNCR_2021159_34M00045_V01`, `JNCR_2021159_34M00049_V01`, `JNCR_2021159_34M00052_V01`, `JNCR_2021159_34M00081_V01`, `JNCR_2021159_34M00084_V01`

The [pipeline audit](junocam_expansion_pipeline_2026-09-08.md) independently
confirms that `band_cameras` reads only the IK and `image_geometry` does not
consume the label override. Defer these mapped methane products until that
specific behavior has an accepted fix and gate. The label for
[24M00026](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0013/DATA/RDR/JUPITER/ORBIT_24/JNCR_2019360_24M00026_V01.LBL)
also describes a deliberate long-exposure test: 102.4 ms with only one TDI
stage. Exposure duration and TDI count cannot always be inferred from one
another. The longer methane/auroral exposures are not interchangeable with
short RGB texture observations. [SIS exposure and TDI definitions](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/DOCUMENT/JUNO_JNC_EDR_RDR_DPSIS.PDF).

**Lossy compression:** most sampled RGB labels declare Integer Cosine
Transform; the table above gives exact counts. The instrument SIS allows
lossy transform compression or lossless Huffman encoding. Lossy texture
changes are distinct from ECC corruption, but matter near the resolution
limit. Match compression/processing where possible and do not interpret a
native-grid Nyquist scale as a validated optical/processing resolution.
[SIS §2.1](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/DOCUMENT/JUNO_JNC_EDR_RDR_DPSIS.PDF).

**Other label revisions:** release 18 corrected satellite-centric geometry and
times for, among these passes, 08C00092, 12C00071/72/109, 18C00001–06 and
24C00001–06. They must not enter a Jupiter field simply because their pass
number matches. The selected 337 labels target JUPITER. Early releases also
used a fixed 273 K focal-plane placeholder; a placeholder or later missing
temperature is not a measurement of nominal detector temperature.
[ERRATA A.18 and B.3](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/ERRATA.TXT).

## Timing: what the record does and does not mean

The table's offset is a **pixel displacement**, with the description giving
`N/311.2` seconds at nominal spin. A numerical nonzero value records a
correction; zero records that no shift was needed; NULL records inability
to determine one. Absence means no listed assessment, not zero. Single-band
visible images are explicitly outside the timing-table coverage. The source
states that corrected start times are already in the archived product: do
not add the tabulated correction again. Current labels and the IK frame
recipe are separate inputs. [Timing description](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/EXTRAS/TIMING_OFFSETS_DESCRIPTION.TXT).

The method matches the first visible framelet limb to the predicted 1-bar
limb. Shape departures and optical depth leave residual errors. Thus a finite
record supports navigation processing, not a numerical absolute uncertainty.
Its application is distinct from the IK's fixed bias and frame-delay terms,
and neither removes per-frame jitter. [Timing description](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/EXTRAS/TIMING_OFFSETS_DESCRIPTION.TXT),
[NAIF IK](https://naif.jpl.nasa.gov/pub/naif/JUNO/kernels/ik/juno_junocam_v03.ti).

The following are exact **RGB observations in the bounded sample** with
NULL offsets. An envelope spans the first start to the last stop; it does
not assert continuous bad data between the listed observations. All shown
NULL products are V01. NULL is a geometric uncertainty, not evidence that
the instrument's signal failed.

| Pass | Product suffixes with NULL | UTC envelope |
| --- | --- | --- |
| PJ3 | 03C00116 | 2016-12-11T17:22:17.163 – 2016-12-11T17:22:31.375 |
| PJ5 | 05C00099, 05C00101, 05C00102 | 2017-03-27T07:30:10.651 – 2017-03-27T08:04:52.265 |
| PJ12 | 12C00073, 12C00074, 12C00075, 12C00076, 12C00077, 12C00079, 12C00080, 12C00081 | 2018-04-01T08:16:05.770 – 2018-04-01T09:23:12.916 |
| PJ18 | 18C00032, 18C00033, 18C00034, 18C00035, 18C00036, 18C00037, 18C00038, 18C00039, 18C00040, 18C00041, 18C00042, 18C00043 | 2019-02-12T17:28:03.386 – 2019-02-12T18:05:10.760 |
| PJ24 | 24C00028, 24C00029, 24C00030, 24C00031, 24C00032, 24C00033, 24C00034, 24C00035, 24C00036, 24C00037, 24C00038, 24C00039 | 2019-12-26T17:32:22.152 – 2019-12-26T18:01:28.614 |
| PJ30 | 30C00026, 30C00027, 30C00028, 30C00029, 30C00030, 30C00032, 30C00033, 30C00034, 30C00035, 30C00036, 30C00037, 30C00038 | 2020-11-08T01:49:16.407 – 2020-11-08T02:12:43.463 |

PJ6, PJ8 and PJ34 have no NULL RGB result in this sample. The RGB observations
`JNCR_2016346_03C00131_V01` and `JNCR_2017244_08C00103_V01` are absent from
the table. Methane NULL results are 12M00078/83/95/96/97/98 and 30M00031
(all V01 on their respective pass dates). PJ34's two NULL rows in the full
timing file concern day 158 satellite images; they are not the selected
day 159 Jupiter sample. [Timing records](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/EXTRAS/RDR_TIMING_OFFSETS.TXT), native labels.

## Later failure boundaries retained for context

The candidate passes precede these electronic failures. This short audit
supports retaining the existing boundaries, not clearing the intervening or
post-anneal intervals. [Current ERRATA A.24–35 and B.9](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/ERRATA.TXT).

| Documented case | Identified interval/products | Distinction |
| --- | --- | --- |
| PJ47 power-on anomaly | 2022-348, 47C00001 and 47M00002, later label V02; 36-minute event described retrospectively in A.25 | Specific first-two-image failure; later releases additionally discuss heated-camera dark current. |
| PJ48 power-on anomaly | `JNC{E,R}_2023021_48C00001_V01` through `JNC{E,R}_2023022_48C00214_V01`; 23 hours | First 214 planned images invalid. The release reports recovery afterward, but that does not establish every subsequent image's metrics or absolute radiometry. |
| PJ49 ECC failure | `JNC{E,R}_2023060_49C00116_V01` is the explicit example | Local transform-block corruption; a general image average can conceal it. |
| PJ56 | 2023-326 swath; all methane and some RGB timing assessments impossible | Saturation/corruption due to radiation damage, separate from geometric inability to see an otherwise healthy limb. |
| PJ73 | From `73R00013_V01` on 2025-159 | Increased radiation noise; buffer overflow also prevented planned acquisitions. |
| PJ74 | From `74G00008_V01` on 2025-192 | Companding wraparound/content-free images; RDR numerical mapping invalid. |
| PJ75–80 releases | Release 34/35 content-free images; PJ79 separately listed as safe mode/no swath | Do not manufacture observations for absent acquisitions or treat black pixels as valid low radiance. |

The cumulative release 35 heading itself says orbits 78,76,80, while its body
says 78,79,80. The timing description says PJ79 had no swath because of safe
mode. Preserve these source inconsistencies instead of inferring a missing
healthy interval. They do not affect the requested prime-mission sample.

## Lead-selected acquisition scope and evidence still required

After reviewing the reconnaissance, the lead selected the 194 full-resolution
RGB products with finite timing records and no metadata caveats from
PJ5, PJ6, PJ8, PJ12, PJ18, PJ24, PJ30 and PJ34: respectively 12, 32, 20,
19, 23, 18, 27 and 43 observations.
This is an acquisition/measurement scope, not 194 clearances. The durable
JSON reproduces that selection by pass, `full_resolution_rgb=True` and empty
`metadata_caveats`. Native metrics and geometry remain to be measured.

### Evidence needed for an individual clearance

A defensible lead decision would record the exact observation stem and
latest processing product, its direct label URL and checksum, acquisition
mode, corrected metadata/software version, and the source evidence reviewed.
The rationale can combine the affirmative first 34-orbit mission statement
with an individual label/errata audit, but must not claim that the broad
statement alone certifies the ID.

After authorized acquisition, verify native byte integrity, meaningful signal,
absence of corruption/bloom/content-free flags, and the current complete
finite metric requirements: `metrics_ok=True`, nonnegative streak index below
0.3, saturation fraction below 0.02, zero fraction below 0.999, and positive
maximum DN. Inspect per-band/per-framelet behavior as needed: the existing
quality summary uses medians across framelets and saturation at the declared
integer maximum, so passing those summaries alone is not proof against local
packet damage, clipping at another stage, or response drift. A label/errata
failure must remain disqualifying regardless of aggregate metrics.
[Current policy](../../src/jiram_catalog/junocam/policy.py),
[quality implementation](../../src/jiram_catalog/junocam/quality.py).

Then establish supported detector geometry, available kernels, actual
on-planet mapped support and useful spatial scale. A timing-table NULL needs
an explicit navigation treatment or an unassessed result; a successful SPICE
call does not settle that accuracy. Retain units, band, normalization, source
versions and uncertainty in the derived products. Assess cadence and common
coverage separately before promising motion inputs. The present report has
not measured any new native signal, navigation residual or usable triplet.

On evidence grounds, begin with supported, corrected **RGB native products**
from more than one pass; PJ5/PJ6/PJ8 have no old-companding RGB in the sampled
latest labels, and PJ6 has finite timing records for all 32 sampled RGB
observations. PJ12/PJ24 remain useful candidates where supported records and
geometry exist; their NULL subsets need particular care. PJ18/PJ30/PJ34 add
longer time baselines while increasing the importance of response evolution.
The lead selects actual passes/IDs using the separate overlap/cost inventory
and measured results. No ML-derived product substitutes for failed native
signal, nor is a post-anneal interval assumed restored.

Judgment calls: treated normal operation as positive screening evidence,
not a blanket whitelist; separated gradual optical response loss from acute
electronics failures without declaring prime-mission absolute calibration;
used exact latest labels and timing records rather than gallery renderings;
deferred old reductions, partial/summed inputs and unsupported methane
geometry; treated NULL navigation as uncertainty rather than instrument
failure. Acquisition and clearance decisions remain with the lead.
