/**
 * The machine-readable mirror of the state the end-to-end tests care about.
 *
 * v1 could not be tested from the outside: everything it did was inside a
 * Bokeh canvas.  This hidden `<pre>` is the contract with the test suite --
 * it re-renders whenever the store changes, so a test can wait on a number
 * instead of on a screenshot.
 */
import { currentNorm, currentStripNorm, useStore } from '../store/store';

export function DebugState() {
  const columns = useStore((s) => s.columns);
  const filtered = useStore((s) => s.filtered);
  const selectionKeys = useStore((s) => s.selectionKeys);
  const viewMode = useStore((s) => s.viewMode);
  const stackId = useStore((s) => s.stackId);
  const stackLevel = useStore((s) => s.stackMeta?.level ?? null);
  const t = useStore((s) => s.t);
  const cmap = useStore((s) => s.cmap);
  const statsVisible = useStore((s) => s.statsVisible);
  const instrumentFilter = useStore((s) => s.filters.instrument);
  const band = useStore((s) => s.band);
  const composite = useStore((s) => s.composite);
  const nFootprints = useStore((s) => s.footprints.n);
  const stripBand = useStore((s) => s.stripBand);
  const stripComposite = useStore((s) => s.stripComposite);
  const norm = useStore((s) => currentNorm(s));
  const stretchMode = useStore((s) => s.stretchMode);
  const stripNorm = useStore((s) => currentStripNorm(s));
  const stripStretchMode = useStore((s) => s.stripStretchMode);

  // `band`, `composite`, `norm` and `stretch_mode` are the Poles viewer's,
  // which is what the amendments' tests name; the strips viewer has its own
  // beside them rather than sharing one field whose meaning would depend on
  // the open tab.  The norms are the wire spelling -- `minnaert:0.8` -- so a
  // test can compare what the viewer thinks with what it asked the server for.
  const state = {
    n_points: columns.n,
    n_filtered: filtered.length,
    selection_n: selectionKeys.size,
    view: viewMode,
    stack_id: stackId,
    level: stackLevel,
    t,
    cmap,
    stats_visible: statsVisible,
    instrument_filter: instrumentFilter,
    band,
    composite,
    n_footprints: nFootprints,
    strip_band: stripBand,
    strip_composite: stripComposite,
    norm,
    stretch_mode: stretchMode,
    strip_norm: stripNorm,
    strip_stretch_mode: stripStretchMode,
  };
  return (
    <pre id="debug-state" style={{ display: 'none' }}>
      {JSON.stringify(state)}
    </pre>
  );
}
