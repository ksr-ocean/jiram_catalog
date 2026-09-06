/**
 * The machine-readable mirror of the state the end-to-end tests care about.
 *
 * v1 could not be tested from the outside: everything it did was inside a
 * Bokeh canvas.  This hidden `<pre>` is the contract with the test suite --
 * it re-renders whenever the store changes, so a test can wait on a number
 * instead of on a screenshot.
 */
import { useStore } from '../store/store';

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
  };
  return (
    <pre id="debug-state" style={{ display: 'none' }}>
      {JSON.stringify(state)}
    </pre>
  );
}
