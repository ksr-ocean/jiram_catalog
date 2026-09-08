# Accepted new-pass strip execution

Executor owns only new mirror strip products/index and its operational script,
logs and results under junocam/expansion_2026-09-08, following the parent mapping
spec. No source, policy, specs, gates or existing PJ4/native-data changes.
Root will explicitly signal when geometry/individual clearance and assessment.csv
are complete. Before that signal, prepare the script but do not build products.

After signal, read assessment.csv and verify every select_images result belongs
to its cleared product_id set. Run existing build_library sequentially by pass
5,6,8,12,18,24,30,34 with bands RED,GREEN,BLUE, quality_min A, refine True and
max_pixel_km30. Use jobs1 for the first pass as the measured strip pilot; after
that use at most12 isolated workers under the revised112GiB product-process
budget below.
Root may concurrently build stacks only for passes whose strips have completed,
so same-ID geometry-fit cache writers never overlap between commands.

Save the entire per-image result table under the expansion directory. After
each build has fully returned, atomically write strip_passNN.json with orbit,
selected IDs, successes/failures, skipped-coarse count, time, bytes and peakRSS.
Write this completion marker even for an empty/failed selection, with actual
failure reasons. Signal the first-pass result promptly. Root's polar builder
waits for these markers. Never run two shared strips-index writers concurrently.

Preserve old rows/files and fail-band collisions per the repaired implementation.
Measured resource amendment by lead, before scaling: the active first-strip
pilot has reached roughly5.2GiB RSS and more than five CPU minutes. Host memory
is251GiB, with156GiB currently available; keeping a48GiB process budget would
unnecessarily serialize this dense reprojection. Use a112GiB total product
budget, reserve28GiB for the root's polar process/children and overhead, and
budget each strip worker at max(6GiB,1.5 times observed pilot peakRSS). Choose
floor(84GiB/per-worker budget), capped at12, and reduce if live total RSS or
available memory warrants. This preserves substantial host headroom and changes
no scientific sampling, algorithms or storage limit. Root geometry survey stays
at its original four-worker bound.

Measure actual new strip storage after each pass; root tracks combined100GiB
derived budget. Do not invoke statistics or delete products/caches. Report all
eight outcomes, physical band metadata, resource measurements and failure IDs;
root runs fixed integration gates and final API/visual checks.

Operational concurrency amendment: after the largest-strip pilot revealed
multi-minute reprojection costs, root polar processing starts from PJ34 and
works backwards while strip processing proceeds forwards. Both commands use
atomic mkdir locks at expansion/pass_locks/PJNN, held through all worker/cache
writes for a pass. The initial unmodified PJ5 pilot is protected by its done
marker; polar PJ6 additionally waits for strip_resume_ready.json proving that
the old parent and its children have exited. The resumed strip runner uses the
same locks. Different passes may overlap; same-ID fit-cache writers may not.
Product resource scaling follows the measured112GiB bound in the strip spec.

Measured PJ6 amendment: all12 selected strips succeeded in877.50s with eight
workers. Total tree peak was33.47GiB; maximum observed individual VmHWM was
6.24553GiB. After preserving the completed PJ6 marker, stop only the owned
strip parent/children and resume remaining passes with a per-worker projection
of max(6GiB,1.25 times the larger of pilot high-water and observed worker
high-water). Keep the112GiB total,28GiB polar reserve,84GiB strip budget,
32GiB host headroom and12-worker ceiling. Retain all completed files/markers
and per-pass locks; no parallel shared-index writers. Launch with explicit
OPENBLAS_NUM_THREADS=OMP_NUM_THREADS=MKL_NUM_THREADS=1. The first PJ6 pool
unintentionally inherited library defaults; record that deviation and verify
the corrected values in future spawned workers. This scheduling adjustment
does not change the scientific calculation or source selection.

Judgment calls: serialize by pass; expose completed-pass markers for safe
concurrent work on distinct products; retain measured30km/px library cutoff.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
