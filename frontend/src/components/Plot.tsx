/**
 * A Plotly figure as a React component.
 *
 * Plotly is loaded lazily (a dynamic `import`), so the two megabytes of
 * charting code are parsed only once a view that draws curves is opened, and
 * the catalog is interactive before that happens.  `Plotly.react` is used for
 * updates rather than `newPlot`, which is what makes a filter change repaint
 * a chart in place instead of rebuilding it.
 */
import { useEffect, useRef, useState } from 'react';

type Data = Record<string, unknown>;
type Layout = Record<string, unknown>;

const DARK_LAYOUT: Layout = {
  paper_bgcolor: 'rgba(0,0,0,0)',
  plot_bgcolor: 'rgba(0,0,0,0)',
  font: { color: '#a6b3c5', size: 12 },
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
  const [error, setError] = useState('');
  const save = async (format: 'svg' | 'png') => {
    try {
      if (!ref.current) return;
      const Plotly = await import('plotly.js-dist-min');
      const href = await Plotly.toImage(ref.current, {
        format,
        width: Math.max(ref.current.clientWidth, 800),
        height: Math.max(height, 450),
        scale: 2,
      });
      const link = document.createElement('a');
      link.href = href;
      link.download = `${testId ?? 'science_figure'}.${format}`;
      link.click();
    } catch (e) {
      setError((e as Error).message);
    }
  };

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
    <div data-testid={testId} style={{ width: '100%', minHeight: height + 34 }}>
      <div ref={ref} style={{ width: '100%', height }} />
      <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
        <button onClick={() => void save('svg')} aria-label={`Download ${testId ?? 'figure'} SVG`}>
          SVG
        </button>
        <button onClick={() => void save('png')} aria-label={`Download ${testId ?? 'figure'} PNG`}>
          PNG
        </button>
      </div>
      {error && <p role="alert">{error}</p>}
    </div>
  );
}
