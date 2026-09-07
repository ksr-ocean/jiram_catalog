# JunoCam perijove-4 geometry check

Written 2026-09-07T08:49:54+00:00 by `scripts/junocam_pj4_geometry_check.py`.

Images: 116 mirrored full-resolution RDR products of orbit 4, day 2017-033; 28 carried enough limb to refine the start time.  On-board 2x2 summed products (816 samples per line) are set aside: the instrument kernel's distortion centres are stated on the 1648-sample framelet grid and no summed equivalent is published.

## Timing refinement

| quantity | min / p10 / median / p90 / max |
| --- | --- |
| `dt_refined_s` (absolute, s) | 0.0010 / 0.0014 / 0.0046 / 0.0208 / 0.0259 |
| limb residual before (px) | 0.434 / 0.509 / 1.871 / 6.096 / 7.302 |
| limb residual after (px) | 0.306 / 0.374 / 0.571 / 1.055 / 1.550 |
| limb points per image | 212 / 218 / 495 / 928 / 1034 |
| geometry wall time (s) | 1.0 / 1.3 / 3.7 / 15.1 / 16.9 |

`dt_refined_s` absorbs every constant timing term the label does not carry, on top of the instrument kernel's 61.88 ms `START_TIME_BIAS`, which is applied before the fit starts.  The exposure midpoint is the largest such term in principle -- 102 ms for a 64-stage methane framelet -- but no methane product of this day had enough limb in the field to be fitted, and for the colour products it is only 1.6 to 4.8 ms, so what the fit measures here is mostly the kernel's own stated "jitter of order 20 msec".  Images without a limb in the field are left unrefined and flagged; their geometry still uses the kernel's nominal timing.

## Band registration after reprojection

Local orthographic grids at 10 km/px, 512 x 512 cells, centred on the least-oblique point of each image's middle frame.

| product | native km/px | red-green (row, col) px | red-blue (row, col) px | overlap cells |
| --- | --- | --- | --- | --- |
| JNCR_2017033_04C00105_V01 | 6.3 | (+0.145, +0.572) | (-0.565, +0.606) | 262144 |
| JNCR_2017033_04C00105_V02 | 6.3 | (-0.136, +0.486) | (-0.635, +0.193) | 262144 |
| JNCR_2017033_04C00102_V01 | 5.3 | (-0.189, +0.044) | (-0.315, +0.033) | 262144 |
| JNCR_2017033_04C00102_V02 | 5.3 | (-0.195, +0.055) | (-0.334, +0.035) | 262144 |
| JNCR_2017033_04C00103_V01 | 4.2 | (-0.115, +0.020) | (-0.254, +0.028) | 262144 |

A shift here is *not* a test of the absolute epoch: a common timing error moves all three strips by the same amount on the ground and cancels.  What it does test is the per-band distortion centres, the interframe delay, and the attitude between the frames that saw one ground point in three colours -- the red and green strips are 155 pixel widths apart on the focal plane, about one and a third frames.

## What limits the agreement

Two effects, both measured rather than assumed.

1. *Apparent limb height.*  A scanning camera sees each image's limb as two populations: the edge the planet enters the strip by, in the first frames, and the edge it leaves by, in the last.  Across the refined images the trailing population sits on the 1-bar ellipsoid to a median of 0.16 px, while the leading one is a median of 2.1 px and up to 8.3 px outside it, further out in blue than in red and further out where the limb crosses high latitudes.  Scattering haze above the 1-bar surface is detected as planet; a pointing or timing error would displace both edges alike and cannot produce this.  It is what leaves one image of the set at 1.5 px while the rest are near 0.5.

2. *Frame-to-frame rate.*  Within one population the residual drifts about -0.32 px per frame, which is -1.05 ms per frame at Juno's spin rate.  Sweeping `INTERFRAME_DELTA` and re-measuring the band registration puts the shift's zero crossing near 2 ms rather than the kernel's 1 ms, and the red-blue shift moves about twice as fast with that parameter as the red-green shift does (2.0 and 3.0 on the two images swept) -- the ratio of their focal-plane separations, which is what a per-frame rate predicts and a wrong distortion centre does not.  The kernel's published value is what the engine uses; the discrepancy is recorded here, not corrected.
