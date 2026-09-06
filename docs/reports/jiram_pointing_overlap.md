# JIRAM Imager Pointing & Frame-Overlap Notes
Compiled 2026-09-03. Builds on juno_mission_facts.md and jiram_pds_research.md (already-verified: perijove altitude 4,200-7,900 km; IFOV ~250 urad; frame 128x432/256x432; spin 2 rpm/30 s).

## Sources Table

| # | URL | Title as fetched | Note |
|---|---|---|---|
| P1 | https://openaccess.inaf.it/bitstreams/e55a7621-f8e9-44fb-b3ba-08d74a2a0323/download | Adriani et al. 2017, "JIRAM, the Jovian Infrared Auroral Mapper," Space Sci. Rev. 213:393, DOI 10.1007/s11214-014-0094-y | Downloaded PDF, parsed with pdftotext -layout, quoted directly below (primary instrument paper) |
| P2 | https://pds-atmospheres.nmsu.edu/data_and_services/atmospheres_data/JUNO/logs/JIRAM_SIS_V7.0.pdf | "Juno JIRAM JIRAM Standard Product Data Record and Archive Volume" SIS, Issue 7.0, INAF/IAPS-2013-12 | Downloaded PDF (2.58 MB), parsed with pdftotext, quoted directly (primary SIS) |
| P3 | https://naif.jpl.nasa.gov/pub/naif/JUNO/kernels/ik/juno_jiram_v02.ti | "juno_jiram_v02.ti" NAIF JIRAM instrument kernel | Fetched via WebFetch (raw curl blocked to naif.jpl.nasa.gov from this sandbox); keyword values reproduced verbatim below |
| P4 | https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2018JE005555 (text obtained via mirror https://websites.umich.edu/~atreya/Articles/2018JupiterPolarWinds.pdf) | Grassi, D. et al. 2018, "First Estimate of Wind Fields in the Jupiter Polar Regions From JIRAM-Juno Images," JGR Planets 123, 1511-1524, DOI 10.1029/2018JE005555 | Downloaded PDF, parsed with pdftotext, quoted directly (primary, peer-reviewed) |
| P5 | https://iopscience.iop.org/article/10.3847/1538-3881/aae525 | Adriani, A., Moriconi, M.L., Altieri, F., Sindoni, G., Ingersoll, A.P., Grassi, D., Mura, A., et al. 2018, "Characterization of Mesoscale Waves in the Jupiter NEB by Jupiter InfraRed Auroral Mapper on board Juno," Astron. J. 156(5):246, DOI 10.3847/1538-3881/aae525 | Fetched via WebFetch; full abstract returned and quoted verbatim below |
| P6 | https://arxiv.org/pdf/1807.10484 | Fletcher, L.N., Melin, H., Adriani, A., et al. 2018, "Jupiter's Mesoscale Waves Observed at 5 um by Ground-Based Observations and Juno JIRAM," AJ (companion paper to P5) | Downloaded PDF, parsed with pdftotext, quoted directly. Independent (non-JIRAM-team-led) description of scan strategy |
| P7 | https://pds-atmospheres.nmsu.edu/data_and_services/atmospheres_data/JUNO/jiram/document/JIRAM_REPORT_JM0231_V1.0.pdf | "Juno JIRAM Report JM0231 JIR-IAPS-SY-007-2020" v1.0, 06/08/2020 | Downloaded PDF, parsed with pdftotext; PDS-archived JIRAM-team orbit-planning report (not peer-reviewed but primary/official) |
| P8 | https://d2pn8kiwq2w21t.cloudfront.net/documents/piday2016_juno_handout.pdf | JPL "Pi Day 2016" Juno educational handout | Downloaded PDF, parsed with pdftotext; NASA/JPL primary source for perijove speed |
| P9 | https://www.sciencedirect.com/science/article/abs/pii/S0273117719307306 and https://ui.adsabs.harvard.edu/abs/2020AdSpR..65..598N/abstract | Noschese, R. et al. 2020, "Juno/JIRAM: Planning and commanding activities," Adv. Space Res. 65, 598, DOI 10.1016/j.asr.2019.09.052 | Both URLs returned HTTP 405 to WebFetch — **UNVERIFIED by direct fetch**; authors/DOI/venue from converging WebSearch snippets only, abstract text NOT independently quote-verified |
| P10 | WebSearch snippets (INAF/JIRAM pointing precision discussion) | n/a | UNVERIFIED — search-engine synthesis only, cited for context (0.1-deg pointing-precision issue), not a fetched primary page |

---

## 1. De-spinning mirror mechanism

Adriani et al. 2017 (P1), verbatim:
> "In first place, Juno is not a three-axis stabilized platform like the previous mentioned missions but it is a spinning spacecraft. This architecture made necessary to develop a different concept of the instrument that needs to operate while the spacecraft is rotating around its axis. To adapt the instrument to such platform we have introduced a de-spinning flat mirror at the telescope's entrance pupil; this mirror uses a mechanism that allows to rotate it synchronously with the rotation of the spacecraft in order to counter-compensate the spin motion."

> "Due to the relative fast angular speed of the spacecraft, the action of the de-spinning mirror had to be limited, heading to a maximum time in which the target scene can be kept still: this time is 1.1s."

> "The JIRAM maximum allowed integration time is 1 s. The limit is imposed by the fact that Juno is a spinning spacecraft and the JIRAM's despinning mirror can only compensate for a limited time for mechanical reasons."

**Freezes LOS during exposure: VERIFIED** (mirror counter-rotates to hold the line of sight still for up to ~1.1 s; max integration time set to 1 s).

**Confined to the plane perpendicular to spin axis: VERIFIED**, independently, by Grassi et al. 2018 (P4), verbatim:
> "Juno is a spin-stabilized spacecraft and JIRAM acquires one image at each Juno spacecraft rotation (2 rpm). JIRAM pointing has only 1 degree of freedom, and the pointing can be set only along the maximum circle orthogonal to the spin axis."

This is the clearest primary statement: the mirror gives exactly **1 DOF**, and that DOF sweeps the great circle (plane) perpendicular to the spin axis — so yes, LOS constrained to that plane, and yes, the mirror position **can be offset from instantaneous nadir** by commanding an angle within that plane (see §6 for the numeric field that records this per frame).

**Mirror's own physical rotation axis relative to spacecraft spin axis (explicit geometric statement): UNVERIFIED** — not found stated as an explicit angle/vector in the text fetched from P1 or P2; only the functional behavior above is documented.

**Numeric max offset/deflection range in degrees: UNVERIFIED** as a stated mechanical spec. The closest quantitative handle found is the JIR_LOG field's numeric encoding range (§6), which is an engineering/telemetry encoding limit, not confirmed to equal the true mechanical range.

## 2. Focal-plane orientation (which pixel axis is along spin/scan direction)

Adriani et al. 2017 (P1), verbatim:
> "each channel has a Field of View (FOV) of 1.75°x5.94° (128x432 pixels corresponding to the along by across track directions)"

So: **128 pixels / 1.75° = along-track direction; 432 pixels / 5.94° = across-track direction** (per-band, L or M). VERIFIED, primary source, exact quote.

NAIF instrument kernel juno_jiram_v02.ti (P3), reproduced verbatim as fetched:
```
JUNO_JIRAM_I (-61410, full imager, 256x432 combined L+M):
INS-61410_FOV_SHAPE       = 'RECTANGLE'
INS-61410_BORESIGHT       = ( 0.0, 0.0, 1.0 )
INS-61410_FOV_CLASS_SPEC  = 'ANGLES'
INS-61410_FOV_REF_VECTOR  = ( 1.0, 0.0, 0.0 )
INS-61410_FOV_REF_ANGLE   = ( 1.8121 )   [deg, half-angle -> full 3.6242 deg, along-track/256-line axis]
INS-61410_FOV_CROSS_ANGLE = ( 2.943 )    [deg, half-angle -> full 5.886 deg, across-track/432-sample axis]

JUNO_JIRAM_I_LBAND (-61411) and JUNO_JIRAM_I_MBAND (-61412), each 128x432:
INS-6141x_BORESIGHT       = ( 0.0, 0.0, 1.0 )
INS-6141x_FOV_REF_VECTOR  = ( 1.0, 0.0, 0.0 )
INS-6141x_FOV_REF_ANGLE   = ( 0.872 )    [half -> full 1.744 deg, matches Adriani's 1.75 deg]
INS-6141x_FOV_CROSS_ANGLE = ( 2.943 )    [half -> full 5.886 deg, matches Adriani's 5.94 deg]

JUNO_JIRAM_S (-61420, spectrometer slit):
INS-61420_BORESIGHT       = ( 0.0, 0.0, 1.0 )
INS-61420_FOV_REF_VECTOR  = ( 1.0, 0.0, 0.0 )
INS-61420_FOV_REF_ANGLE   = ( 0.0068125 )  [half, slit-width axis]
INS-61420_FOV_CROSS_ANGLE = ( 1.744 )      [half -> full 3.488 deg, close to Adriani's 3.52 deg]
```
VERIFIED — fetched directly, angles cross-check against Adriani et al. 2017's quoted FOV numbers to <1%.

**Connecting to spin geometry (my inference, not verbatim):** Since the mirror's 1 DOF sweeps the plane perpendicular to the spin axis (§1, Grassi 2018), and the paper's own words tie the 128-pixel/1.75° axis to "along track," the narrow (128-pixel) axis is oriented along the sweep/spin direction, while the wide 432-pixel/5.94° axis lies roughly parallel to the spin axis itself (perpendicular to the sweep). This inference is internally consistent with both quotes but is not itself a verbatim statement from either source — flagged as inference.

## 3. Spacecraft attitude at perijove (MWR vs GRAV)

JIRAM SIS v7.0 (P2), verbatim — the cleanest primary statement found:
> "For the radiometry orbits the spin axis is precisely perpendicular to the orbit plane so that the radiometer fields of view pass through the nadir. For gravity passes, the spin axis is aligned to the Earth direction, allowing for Doppler measurements through the periapsis portion of the orbit."

Adriani et al. 2017 (P1), verbatim:
> "The baseline for the JIRAM operations is during those orbits tagged as MWR, where the Juno's Microwave Radiometer is considered as prime instrument. During MWR orbits the spin axis of the spacecraft will be orthogonal to the orbit plane, then the Juno's orbit plane will pass through the center of the planet. Consequently, the JIRAM optical axis will be contained in the orbital plane and the instrument will have the most favorable view of the planet. ... MWR orbits are the 3rd, 5th, 6th, and 7th orbits."
> "the other orbits are planned for gravitational measurements that require a spacecraft orientation unfavorable to JIRAM."

**VERIFIED, primary, two independent documents agree**: in MWR attitude, spin axis ⊥ orbit plane ⟹ the mirror's sweep plane (⊥ spin axis) coincides with the orbital plane ⟹ nadir passes through the sweep, favorable geometry. In GRAV attitude, spin axis points at Earth instead ⟹ sweep plane generally does NOT contain nadir ⟹ unfavorable/limited JIRAM viewing.

**Nuance flagged**: Grassi et al. 2018 (P4), describing the actual PJ4 (Feb 2017) geometry used for their polar-wind analysis, states: "The Juno spin axis is kept pointed toward the Earth and, therefore, roughly toward the Sun. Moreover, the Juno orbital plane is approximately orthogonal to the spacecraft's spin axis." This reads as an Earth-pointed (GRAV-like) attitude that also happened to be close to orbit-normal early in the mission (Earth direction and orbit-normal nearly coincided at that epoch) — not a contradiction of the MWR/GRAV dichotomy above, but a reminder that the two nominal categories can partially overlap depending on mission phase. Not independently resolved further — UNVERIFIED how general this coincidence is across the mission.

## 4. Quantitative overlap estimate

**Speed at closest approach**: JPL Pi Day 2016 Juno handout (P8), verbatim: *"At closest approach, it will reach a velocity of 57.98 km per second relative to the planet."* VERIFIED, primary NASA/JPL source. (Matches the question's ~58 km/s hint essentially exactly.)

**Perijove altitude range**: 4,200-7,900 km (carried over, VERIFIED in juno_mission_facts.md, JPL press kit).

**IFOV**: 237.767 urad/pixel exact (from IK P3, `0.000237767 rad/pixel`, per prior session's notes) vs. Adriani 2017's rounded "about 250x250 urad." Frame size per band: 128x432 pixels; full imager (both bands combined in one readout): 256x432 pixels.

**Footprint computed** (nadir, flat-ground approximation using the IK's exact half-angles, formula = 2 x altitude x tan(half-angle); ignores Jupiter's curvature, which is a small correction at these altitudes vs. Jupiter's ~70,000 km radius):

| Altitude | Single-band frame (128x432 px) along-track x across-track | Full imager (256x432 px) along-track x across-track |
|---|---|---|
| 5,000 km | **152 km x 514 km** | 316 km x 514 km |
| 20,000 km | **609 km x 2,056 km** | 1,266 km x 2,056 km |

**Along-track ground motion in 30 s** (one spin), using the given 57.98 km/s uniformly at both altitudes per the task instruction:
> 57.98 km/s x 30 s = **1,739 km**

Caveat (my own estimate, not sourced numerically): this overstates the true value at 20,000 km, because (a) 57.98 km/s is specifically the speed AT closest approach (~4,200-7,900 km altitude); by vis-viva the spacecraft's true orbital speed is measurably lower away from periapsis, and I could not find/verify a specific quoted speed at 20,000 km altitude (UNVERIFIED, flagged); (b) the sub-spacecraft ground-track linear speed is further reduced relative to the spacecraft's own orbital speed by a purely geometric factor of roughly R_Jupiter/(R_Jupiter+altitude) (~0.93-0.94 at these altitudes, using R_Jupiter ~71,492 km) — a comparatively small correction at these altitudes.

**Do consecutive nadir frames overlap? Answer: No, not from straight nadir-pointing alone, at either altitude.**
- At 5,000 km: along-track footprint (152 km) << ground motion in 30 s (~1,700+ km) — a >10x gap.
- At 20,000 km: along-track footprint (609 km) << ground motion in 30 s (~1,700+ km, likely somewhat less after the caveats above, but still much larger than 609 km).

This means: if JIRAM simply pointed straight down (mirror always at zero nadir-offset) once per spin, consecutive frames would leave large gaps along the ground track, not overlap. This matches and explains why the team instead uses the mirror's programmable nadir-offset (§1) to deliberately point ahead of / behind the instantaneous sub-spacecraft point on successive spins, tiling contiguous or overlapping footprints — see §5 for direct documentary evidence of this strategy (mosaicking, "overlapping yellow strips").

## 5. Documented overlapping-view strategies for wind/wave derivation

**(a) Grassi, D., Adriani, A., Moriconi, M.L., Mura, A., Tabataba-Vakili, F., Ingersoll, A., et al. 2018, "First Estimate of Wind Fields in the Jupiter Polar Regions From JIRAM-Juno Images," JGR Planets 123, 1511-1524, DOI 10.1029/2018JE005555 (P4).** Polar (far-from-perijove) strategy — VERIFIED, direct quote:
> "In most circumstances, JIRAM is operated to acquire a set of consecutive images (a "sequence"), spaced as to create an almost spatially continuous mosaic over a given region."
> "During the fourth periaxis passage (PJ4), JIRAM acquired 21 sequences of 13 images each over the north pole and 6 sequences of 19 images each over the south pole. The time interval between two consecutive sequences was 16 min over the north pole and 20 min over the south pole."
> "We adopted the criterion of minimum mean absolute distortion ... to quantify the motion of cloud features between pairs of images."

**(b) Adriani, A., Moriconi, M.L., Altieri, F., Sindoni, G., Ingersoll, A.P., Grassi, D., Mura, A., et al. 2018, "Characterization of Mesoscale Waves in the Jupiter NEB by Jupiter InfraRed Auroral Mapper on board Juno," Astron. J. 156(5):246, DOI 10.3847/1538-3881/aae525 (P5).** **This is the mid-latitude example** (15°N, in the NEB — matches the question's framing directly). VERIFIED, full abstract fetched and quoted:
> "we observed a wide longitude region (50° W-80° E in System III) that was perturbed by a wave pattern centered at 15° N in the Jupiter's North Equatorial Belt (NEB) ... acquired ... using the M-channel and ... with the spectrometer."
> "three successive sequences of measurements, acquired at time intervals of approximately 70 minutes"

**(c) Fletcher, L.N., Melin, H., Adriani, A., et al. 2018, "Jupiter's Mesoscale Waves Observed at 5 um by Ground-Based Observations and Juno JIRAM," AJ (companion paper), arXiv:1807.10484 (P6).** Independent (non-JIRAM-team-led) description — VERIFIED, direct quote, fetched full text:
> "When the spacecraft's spinning plane intersects the planet, JIRAM can make scans from south to north, changing pointing approximately every 30 s (spacecraft spinning period). Maps of a limited range of latitudes can be built by mosaicking images from different observing sequences, although some artefacts occur because the atmosphere has evolved during the interval between sequences."

**(d) Noschese, R., et al. 2020, "Juno/JIRAM: Planning and commanding activities," Adv. Space Res. 65, 598, DOI 10.1016/j.asr.2019.09.052 (P9).** The planning paper named in the task. **UNVERIFIED by direct fetch** — both ScienceDirect and ADS blocked WebFetch (HTTP 405). Author list and DOI are consistent across multiple independent WebSearch snippets (moderate confidence) but no sentence from the actual abstract could be independently quote-verified in this session.

**(e) JIRAM_REPORT_JM0231 (JIR-IAPS-SY-007-2020, 06/08/2020), PDS-archived JIRAM team orbit-planning report (P7).** VERIFIED, direct quote, fetched full text — direct documentary evidence of the deliberate-overlap planning strategy at the poles ("far away" case named in the task):
> "Simulation with JSPT (JIRAM Software Planning Tool), coverage of the South Pole with filter M. The overlapping yellow strips represent the predicted FOVs."

## 6. Does JIR_LOG record the mirror nadir-offset angle per frame?

**Answer: Yes.** JIRAM SIS v7.0 (P2), Table defining the JIR_LOG auxiliary-data record, verbatim:
> "21  NADIR_OFFSET  16  Iint16  Nadir offset angle related to the current frame, calculated by the sw (For example NADIR_OFFSET_1 + NADIR_DELTA) Min=0; Max=57343; Res=0.003138951 deg (optical) NB: Used with the sign bit in the word 26 (bit 0) to generate negative angles."
> "26  NADIR_OFFSET_SIGN  ... Field used to generate negative angles of Nadir Offset. Used with the word 21. 0= positive or 0  1=negative"

Also present as a labeled field in the science-product (EDR/RDR) labels themselves, not just the log:
> "NAME = "NADIR_OFFSET" ... DESCRIPTION = "Nadir offset angle (Deg/100) related to the current frame , calculated by the sw (For example NADIR_OFFSET_1 + NADIR_DELTA)""

And the mirror's operating mode is separately logged (word 26, bit 10):
> "M_STATUS_MOTOR_MODE  1  Enum  Despinning mode, acquired from FPA  bit 10  0=POINT  1=DE-SPIN"

So a cataloging tool CAN read the commanded mirror angle (within the 1-DOF sweep plane) per frame directly from the archive without invoking SPICE for that one angle — **but** it would still need the spacecraft's 3-D orientation (spin-axis pointing, from CK attitude kernels) to convert that single in-plane angle into an absolute nadir/off-nadir 3-D look direction; NADIR_OFFSET alone is a 1-D quantity.

**Max representable angle** (0 to 57343 counts x 0.003138951 deg/count) = **~180.0 deg**, plus a separate sign bit for negative angles — i.e., the field's numeric *encoding* spans roughly +-180 deg. **UNVERIFIED** whether this equals the mirror's true achievable mechanical range (no separate mechanical-range spec was found quoted anywhere).

---

## Verification counts
- **VERIFIED (directly quoted from a fetched primary source):** 18 discrete claims/quotes across items 1-6 (de-spin mirror description and timing x3, MWR/GRAV attitude x3 independent sources, IK FOV/boresight values for all 4 instrument IDs, along/across-track pixel identification, perijove speed, footprint/motion computation inputs, Grassi 2018 wind paper + quotes, Adriani 2018 AJ mesoscale-wave paper + abstract, Fletcher 2018 companion paper + quote, JM0231 report + quote, SIS JIR_LOG NADIR_OFFSET/SIGN/motor-mode fields).
- **UNVERIFIED:** 5 items — (1) mirror's own physical rotation-axis orientation relative to spin axis as an explicit geometric statement; (2) true mechanical maximum nadir-offset range in degrees (only the log field's numeric encoding capacity, ~180 deg, was found); (3) Noschese et al. 2020 abstract text (DOI/authors only, both target pages blocked WebFetch with HTTP 405); (4) exact spacecraft/ground-track speed specifically at 20,000 km altitude (used the closest-approach 57.98 km/s uniformly per task instruction, flagged as an overestimate there); (5) generality of Grassi et al. 2018's "spin axis toward Earth ~ also near orbit-normal" PJ4-specific geometry across the rest of the mission.

## Uncertainties
1. No source found gives the de-spinning mirror's own rotation-axis direction as an explicit vector/angle relative to the spacecraft spin axis — only its function (counter-rotate to compensate spin; 1 DOF within the plane perpendicular to spin axis) is documented.
2. No source found states a numeric mechanical maximum nadir-offset/deflection angle in degrees for the mirror; the JIR_LOG NADIR_OFFSET field's numeric encoding tops out at ~180 deg (with a separate sign bit), but this is a telemetry-field capacity, not confirmed as the physical limit.
3. Noschese et al. 2020 (the explicitly-named JIRAM planning paper) could not be fetched directly in this session (ScienceDirect and ADS both returned HTTP 405 to WebFetch); its DOI/venue/authors are reported with moderate confidence from converging search snippets only.
4. The along-track ground-motion-in-30s figure (1,739 km) was computed uniformly at both 5,000 km and 20,000 km altitude using the single closest-approach speed value (57.98 km/s) per the task's explicit instruction; the true value at 20,000 km altitude is somewhat lower (both from vis-viva orbital-speed decrease away from periapsis and from the R_Jupiter/(R_Jupiter+altitude) ground-track projection factor), but no directly sourced number for speed specifically at 20,000 km altitude was found — the qualitative conclusion (large gap between footprint and motion, hence no natural overlap) is robust to this uncertainty by a wide margin (>10x at 5,000 km).
5. Footprint calculation used a flat-ground/no-curvature approximation, reasonable at these altitudes relative to Jupiter's ~70,000 km radius but not exact, especially toward 20,000 km.
