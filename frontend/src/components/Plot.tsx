/**
 * A Plotly figure as a React component.
 *
 * Plotly is loaded lazily (a dynamic `import`), so the two megabytes of
 * charting code are parsed only once a view that draws curves is opened, and
 * the catalog is interactive before that happens.  `Plotly.react` is used for
 * updates rather than `newPlot`, which is what makes a filter change repaint
 * a chart in place instead of rebuilding it.
 */
import { useEffect, useRef } from 'react';

type Data = Record<string, unknown>;
type Layout = Record<string, unknown>;

const DARK_LAYOUT: Layout = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { color: '#93a0b4', size: 10 },
  margin: { l: 48, r: 12, t: 26, b: 38 },
  showlegend: false,
};

export function Plot({
  data,
  layout,
  height = 220,
  testId,
}: {
  data: Data[];
  layout?: Layout;
  height?: number;
  testId?: string;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const node = ref.current;
    if (!node) return;
    void import('plotly.js-dist-min').then((Plotly) => {
      if (cancelled || !ref.current) return;
      void Plotly.react(
        ref.current,
        data,
        { ...DARK_LAYOUT, ...layout, height },
        { displayModeBar: false, responsive: true },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [data, layout, height]);

  useEffect(() => {
    const node = ref.current;
    return () => {
      if (!node) return;
      void import('plotly.js-dist-min').then((Plotly) => Plotly.purge(node));
    };
  }, []);

  // Plotly puts its `js-plotly-plot` class on the element it is given, so the
  // test id lives on a wrapper: `[data-testid=...] .js-plotly-plot` then means
  // "this figure has actually been drawn".
  return (
    <div data-testid={testId} style={{ width: '100%', height }}>
      <div ref={ref} style={{ width: '100%', height }} />
    </div>
  );
}
