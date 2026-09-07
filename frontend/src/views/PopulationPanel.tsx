import { useState } from 'react';
import { researchApi, type Population } from '../api/research';
import type { StripRow } from '../lib/stripsTable';
import { Plot } from '../components/Plot';
import { downloadText, toCsv } from '../lib/csv';
import { fmt } from '../lib/format';
import styles from './Views.module.css';
export function PopulationPanel({
  rows,
  band,
  norm,
  active,
}: {
  rows: StripRow[];
  band: string | null;
  norm: string;
  active: boolean;
}) {
  const [selected, setSelected] = useState<string[]>([]),
    [result, setResult] = useState<Population | null>(null),
    [sensitivity, setSensitivity] = useState<Population | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const [min, setMin] = useState(''),
    [max, setMax] = useState(''),
    [compareNorm, setCompareNorm] = useState(false),
    [analysisBand, setAnalysisBand] = useState('');
  const chosenBand = analysisBand || band;
  const run = async () => {
    setBusy(true);
    setError('');
    setResult(null);
    setSensitivity(null);
    try {
      const primary = await researchApi.population(
        selected,
        chosenBand,
        norm,
        min ? Number(min) : undefined,
        max ? Number(max) : undefined,
      );
      setResult(primary);
      if (compareNorm)
        setSensitivity(
          await researchApi.population(
            selected,
            chosenBand,
            norm === 'none' ? 'flat:64' : 'none',
            min ? Number(min) : undefined,
            max ? Number(max) : undefined,
          ),
        );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const availableBands = [...new Set(rows.flatMap((r) => (r.bands.length ? r.bands : [r.band])))];
  return (
    <details className={styles.card} data-testid="population-panel">
      <summary>Population statistics and reproducible figures</summary>
      <p>
        Texture spectra of independent observations, grouped by instrument, physical band,
        normalization and resolution. Uncertainty uses independent per-pass means.
      </p>
      <div className={styles.toolbar}>
        <button
          data-testid="population-select-filtered"
          disabled={!rows.length || rows.length > 100}
          onClick={() => setSelected(rows.map((r) => r.strip_id))}
        >
          Use {rows.length} filtered images
        </button>
        <button onClick={() => setSelected([])}>Clear population</button>
        <span>{selected.length} selected (limit 100)</span>
        <label>
          Physical band{' '}
          <select value={analysisBand} onChange={(e) => setAnalysisBand(e.target.value)}>
            <option value="">
              {band ? `Current band: ${band}` : 'Choose for multi-band images'}
            </option>
            {availableBands.map((b) => (
              <option key={b}>{b}</option>
            ))}
          </select>
        </label>
        <label>
          Fit k minimum (rad/m){' '}
          <input
            type="number"
            step="any"
            min="0"
            value={min}
            onChange={(e) => setMin(e.target.value)}
            style={{ width: 110 }}
          />
        </label>
        <label>
          Fit k maximum (rad/m){' '}
          <input
            type="number"
            step="any"
            min="0"
            value={max}
            onChange={(e) => setMax(e.target.value)}
            placeholder="Nyquist"
            style={{ width: 110 }}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={compareNorm}
            onChange={(e) => setCompareNorm(e.target.checked)}
          />
          Compare {norm === 'none' ? 'flattened' : 'native'} normalization
        </label>
        <button
          data-testid="population-compute"
          disabled={!selected.length || busy}
          onClick={() => void run()}
        >
          {busy ? 'Computing…' : 'Compute population'}
        </button>
      </div>
      {rows.length > 100 && (
        <p className={styles.muted}>
          Narrow the library filters to at most 100 images, or select individual members below.
        </p>
      )}
      <details>
        <summary>Choose individual population members</summary>
        <div className={styles.listWrap}>
          {rows.map((row) => (
            <label key={row.strip_id} style={{ display: 'flex', padding: 5 }}>
              <input
                type="checkbox"
                checked={selected.includes(row.strip_id)}
                disabled={selected.length >= 100 && !selected.includes(row.strip_id)}
                onChange={(e) =>
                  setSelected(
                    e.target.checked
                      ? [...selected, row.strip_id]
                      : selected.filter((id) => id !== row.strip_id),
                  )
                }
              />
              {row.instrument} PJ{row.orbit} · {row.strip_id}
            </label>
          ))}
        </div>
      </details>
      {error && <p role="alert">{error}</p>}
      {result && (
        <>
          <p className={styles.muted}>
            Computed recipe settings are retained with this result. Recompute after changing
            members, fit range, band or normalization.
          </p>
          {result.groups.length === 0 && (
            <p>No supported population group remains. See exclusions below.</p>
          )}
          {result.groups.map((group, i) => {
            const alternative = sensitivity?.groups.find(
              (g) =>
                g.instrument === group.instrument &&
                g.band === group.band &&
                g.resolution_class === group.resolution_class,
            );
            const data: Record<string, unknown>[] = [
              {
                type: 'scatter',
                mode: 'lines',
                name: group.norm,
                x: group.k,
                y: group.E,
                error_y: group.E_stderr
                  ? { type: 'data', array: group.E_stderr, visible: true }
                  : undefined,
                line: { color: '#79b9ed' },
              },
            ];
            if (alternative)
              data.push({
                type: 'scatter',
                mode: 'lines',
                name: `Sensitivity: ${alternative.norm}`,
                x: alternative.k,
                y: alternative.E,
                line: { color: '#efbd74', dash: 'dot' },
              });
            return (
              <section
                className={styles.card}
                key={`${group.instrument}-${group.band}-${group.resolution_class}-${i}`}
              >
                <h3>
                  {group.instrument} · {group.band} · {group.resolution_class} · {group.norm}
                </h3>
                <p>
                  {group.n_observations} independent observations · {group.n_passes} independent
                  passes ·{' '}
                  {group.E_stderr
                    ? 'Standard error across passes'
                    : 'Uncertainty unavailable: fewer than two independent passes'}
                </p>
                <div className={styles.compareGrid}>
                  {active && (
                    <Plot
                      testId={`population-spectrum-${i}`}
                      data={data}
                      height={300}
                      layout={{
                        showlegend: true,
                        legend: { orientation: 'h' },
                        xaxis: { type: 'log', title: { text: 'k (rad/m)' } },
                        yaxis: {
                          type: 'log',
                          title: { text: `E(k) [(${group.units ?? 'native units'})² m rad⁻¹]` },
                        },
                      }}
                    />
                  )}
                  {active && (
                    <Plot
                      testId={`population-structure-${i}`}
                      data={[{ type: 'scatter', mode: 'lines', x: group.r_m, y: group.S2 }]}
                      height={300}
                      layout={{
                        xaxis: { type: 'log', title: { text: 'Separation (m)' } },
                        yaxis: {
                          type: 'log',
                          title: { text: `S₂ [(${group.units ?? 'native units'})²]` },
                        },
                      }}
                    />
                  )}
                </div>
                <p>
                  Fitted slope {fmt(group.fit.slope as number, 3)} · {String(group.fit.n_bins ?? 0)}{' '}
                  bins · standard error {fmt(group.fit.standard_error as number, 3)}
                </p>
                <details>
                  <summary>Mask, seam and fit diagnostics</summary>
                  <pre>
                    {JSON.stringify({ fit: group.fit, diagnostics: group.diagnostics }, null, 2)}
                  </pre>
                </details>
                <details>
                  <summary>Sources, units and processing provenance</summary>
                  <pre>{JSON.stringify(group.provenance, null, 2)}</pre>
                </details>
                <button
                  onClick={() =>
                    downloadText(
                      `population_${group.instrument}_${group.band}_${i}.csv`,
                      toCsv(
                        ['k_rad_per_m', 'E', 'E_stderr'],
                        group.k.map((k, j) => [k, group.E[j], group.E_stderr?.[j] ?? '']),
                      ),
                    )
                  }
                >
                  Download numeric CSV
                </button>
              </section>
            );
          })}
          <button
            data-testid="population-recipe"
            onClick={() =>
              downloadText(
                'population_recipe.json',
                JSON.stringify({ result, sensitivity }, null, 2),
                'application/json',
              )
            }
          >
            Download results and recipe (JSON)
          </button>
          {result.excluded.length > 0 && (
            <div>
              <h3>Excluded members</h3>
              {result.excluded.map((e, i) => (
                <p key={i}>
                  {e.id}: {e.reason}
                </p>
              ))}
            </div>
          )}
          {sensitivity?.excluded.length ? (
            <p>
              Normalization comparison exclusions:{' '}
              {sensitivity.excluded.map((e) => `${e.id}: ${e.reason}`).join('; ')}
            </p>
          ) : null}
        </>
      )}
    </details>
  );
}
