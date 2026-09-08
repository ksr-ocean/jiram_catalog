# Accepted measured eligibility and multi-pass products

Parent: `2026-09-08_junocam_expansion.md`; acquisition oracle and fixed gates
are already written in `tests/test_gate_junocam_expansion.py`.
Lead owns clearance adjudication, mapping operations and final verification.
No scientific algorithms, coordinates or region definitions change.

## Scope and resource bound

Use only the 194 checksummed RGB acquisitions from the companion acquisition
spec. Native and derived writes stay under the mirror's normal JunoCam index,
geometry cache, strips/junocam/orbitNN and regions directories. Logs, scripts
and measured outcomes go in junocam/expansion_2026-09-08. Existing products and
PJ4 indexes are preserved. Serialize writes of each shared index. Start a
full-geometry pilot with one largest native product (53,157,888 bytes), measure
wall time and peak RSS, then use up to four isolated SPICE worker processes if
the projected total is below 48 GiB. Geometry surveys use existing refine=False;
derived maps use existing refine=True. Bound new derived storage to 100 GiB;
measure actual growth after the first pass and avoid uncontrolled all-pass jobs.

## Individual assessment

Run existing build_geo for the eight selected passes. Save all results, including
failures, and verify untouched old geo rows against backup. Combine: (a) NASA's
affirmative normal-operation first34 evidence; (b) each exact latest product's
inspected label/ERRATA audit and determined timing; (c) matched checksum and
supported full RGB native image; (d) complete measured signal; (e) successful
finite geometry. Do not turn the nominal epoch into a whitelist.

New eligibility requires existing fail-closed assessment to have no exclusion
reason, clean finite overall metrics, and each red/green/blue band's finite
median streak<0.3, saturation<0.02 and zero fraction<0.999. A failing or missing
band is deferred even if an overall median looks clean. Source products stay
preserved. Lead may request independent read-only triage of these measurements.
Record every acquired ID's measurements and final reason in a committed small
CSV and linked completion report. Add only qualifying exact product_ids to
configs/junocam_quality.yaml with reason and report/source citations. Preserve
legacy_measured_orbits=[4] and all failure rules, and verify PJ58 remains withheld.

Correct the YAML's descriptive throughput notes: the current47–60 numerical
display domain is a legacy compatibility choice, not the onset of optical
darkening. The primary instrument-team analysis describes mission-long changes
normalized to PJ3; earlier unknown factors do not mean unity. Keep the existing
numeric fields and archived/derived pixels unchanged. No automatic correction
or radiometric calibration claim; native planetary RDR already includes the
archive's commanded-exposure/solar-distance scaling.

## Products and completion

After the incremental strip repair passes its fixed gate, build all eligible
new RGB strips with measured median scale<=30km/px, original IDs and all three
physical bands. Process one pass per command, starting jobs=1 and scaling within
the measured bound. Log every failed/skipped image; no deletion of prior data.

For each new pass, use existing stack selectors on north_pole_paper and south_pole
at their existing15km grids, existing maximum native/grid sample ratio3 and
quality A. Build cropped stacks where at least two eligible overlapping images
exist. Preserve physical bands, masks and individual times, one pass per file.
Do not claim consecutive equally spaced motion triplets across gaps or passes.
Measure cadence and shared valid coverage from resulting stacks. Skip empty/
unusable selections with an explicit recorded reason. No duplicated alternate
north grid is required in this first expansion.

Root verification: fixed integration gates (acquisition, multiple mapped passes,
physical signals and API access), existing JunoCam product/geometry gates as
applicable, offline suite for source repair, representative current API image
and statistics requests and visual inspection. Preserve old320NetCDF path/size/
mtime records and prior index rows. Target eight new passes; retain the parent's
at-least-three useful passes acceptance only for actual evidence constraints.
Report per-pass raw/eligible/mapped/stack counts, bytes and navigation/cadence/
radiometric limitations. Update gui_guide's dated snapshot, docs map/data-products/
open-items/build-log and relevant pedagogical review notes. Push code/docs only.

Operational concurrency amendment: after the largest-strip pilot revealed
multi-minute reprojection costs, root polar processing starts from PJ34 and
works backwards while strip processing proceeds forwards. Both commands use
atomic mkdir locks at expansion/pass_locks/PJNN, held through all worker/cache
writes for a pass. The initial unmodified PJ5 pilot is protected by its done
marker; polar PJ6 additionally waits for strip_resume_ready.json proving that
the old parent and its children have exited. The resumed strip runner uses the
same locks. Different passes may overlap; same-ID fit-cache writers may not.
Product resource scaling follows the measured112GiB bound in the strip spec.

Polar resource amendment after observing parent4.67GiB and maximum child4.82GiB
high-water memory in PJ34: complete PJ34 with its existing two workers, then
resume remaining recorded-not-yet-built stacks with at most four isolated
workers. Preserve completed outcomes and files; use the same per-pass locks.
The total product-process budget remains112GiB, with28GiB reserved for polar
processing/overhead and84GiB for the strip-worker projection. No scientific
algorithm, source selection or grid change accompanies this scheduling change.

Judgment calls: distinguish ordinary response evolution from documented failure
intervals; retain native radiometry with explicit limits; require clean per-band
metrics; add two existing polar grids without claiming flow-ready triples.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
