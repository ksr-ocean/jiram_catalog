import { useEffect, useState } from 'react';
import { researchApi, type Coverage, type ArchiveItem } from '../api/research';
import { useStore } from '../store/store';
import { downloadText } from '../lib/csv';
import styles from './Views.module.css';
const columns = [
  ['archive_known', 'Archive'],
  ['labels_indexed', 'Labels'],
  ['pixels_local', 'Local pixels'],
  ['geometry_available', 'Geometry'],
  ['quality_assessed', 'Assessed'],
  ['eligible', 'Eligible'],
  ['excluded', 'Excluded'],
  ['unassessed', 'Unassessed'],
  ['stacks', 'Time series'],
  ['strips', 'Images'],
] as const;
export function CoverageView({ active }: { active: boolean }) {
  const [coverage, setCoverage] = useState<Coverage | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  const [instrument, setInstrument] = useState('JunoCam'),
    [orbit, setOrbit] = useState(''),
    [query, setQuery] = useState(''),
    [offset, setOffset] = useState(0);
  const [archive, setArchive] = useState<{ items: ArchiveItem[]; total: number } | null>(null),
    [archiveError, setArchiveError] = useState('');
  const openDetail = useStore((s) => s.openDetail);
  useEffect(() => {
    if (!active || coverage) return;
    let current = true;
    setBusy(true);
    researchApi
      .coverage()
      .then((data) => {
        if (current) setCoverage(data);
      })
      .catch((e) => {
        if (current) setError(e.message);
      })
      .finally(() => {
        if (current) setBusy(false);
      });
    return () => {
      current = false;
    };
  }, [active, coverage]);
  useEffect(() => {
    if (!active) return;
    let current = true;
    const timer = setTimeout(() => {
      setArchiveError('');
      researchApi
        .archive(instrument, orbit, query, offset)
        .then((data) => {
          if (current) setArchive(data);
        })
        .catch((e) => {
          if (current) setArchiveError(e.message);
        });
    }, 250);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [active, instrument, orbit, query, offset]);
  return (
    <div className={styles.view} data-testid="coverage-view">
      <div className={styles.heading}>
        <h2>Archive coverage and scientific eligibility</h2>
        <p>
          Find what the archive contains, what is available locally, and what can be used for
          analysis. Counts refer to independent observations; processing versions are retained in
          provenance.
        </p>
      </div>
      {busy && <p role="status">Loading coverage…</p>}
      {error && <p role="alert">{error}</p>}
      {coverage && (
        <>
          <div className={styles.card}>
            <h3>JunoCam instrument quality policy</h3>
            <p>{coverage.policy.summary}</p>
            <p className={styles.muted}>
              Excluded and unassessed JunoCam observations are metadata only. Instrument failures
              cannot be enabled in the viewer.
            </p>
            <details>
              <summary>Exclusion evidence and rules</summary>
              <pre>{JSON.stringify(coverage.policy.exclusions, null, 2)}</pre>
            </details>
            <p className={styles.muted}>
              {coverage.policy_version} · Generated {coverage.generated_utc}
            </p>
          </div>
          <div className={styles.tableWrap} style={{ maxHeight: 420 }}>
            <table data-testid="coverage-table">
              <thead>
                <tr>
                  <th>Instrument</th>
                  <th>Pass</th>
                  {columns.map(([key, title]) => (
                    <th key={key}>{title}</th>
                  ))}
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {coverage.rows.map((row) => (
                  <tr key={`${row.instrument}-${row.orbit}`}>
                    <td>{row.instrument}</td>
                    <td>
                      <button
                        onClick={() => {
                          setInstrument(row.instrument);
                          setOrbit(String(row.orbit));
                          setOffset(0);
                        }}
                      >
                        PJ{row.orbit}
                      </button>
                    </td>
                    {columns.map(([key]) => (
                      <td key={key}>{row[key]?.toLocaleString() ?? '—'}</td>
                    ))}
                    <td>{row.status}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <details className={styles.card}>
            <summary>Source timestamps and counting notes</summary>
            <p>
              Local-pixel counts may reflect the image-presence snapshot stored in the index, not a
              fresh filesystem scan.
            </p>
            {coverage.sources.map((source, i) => (
              <p key={i}>
                {source.name}: {source.updated_utc ?? 'timestamp unavailable'}
              </p>
            ))}
          </details>
          <div className={styles.card}>
            <h3>External reference collections</h3>
            {coverage.references.map((ref) => (
              <article key={ref.id}>
                <h4>
                  <a href={ref.url} target="_blank" rel="noreferrer">
                    {ref.title}
                  </a>
                </h4>
                <p>{ref.description}</p>
                <p className={styles.muted}>
                  {ref.kind} ·{' '}
                  {ref.quantitative_ready
                    ? 'Quantitative use documented by this source'
                    : 'Reference / derived product; quantitative suitability requires validation'}
                  . {ref.coordinate_note}
                </p>
              </article>
            ))}
          </div>
          <button
            onClick={() =>
              downloadText(
                'juno_coverage.json',
                JSON.stringify(coverage, null, 2),
                'application/json',
              )
            }
          >
            Download coverage and policy
          </button>
        </>
      )}
      <div className={styles.card}>
        <h3>Search archive metadata</h3>
        <div className={styles.toolbar}>
          <label>
            Instrument{' '}
            <select
              data-testid="archive-instrument"
              value={instrument}
              onChange={(e) => {
                setInstrument(e.target.value);
                setOffset(0);
              }}
            >
              <option>JunoCam</option>
              <option>JIRAM</option>
            </select>
          </label>
          <label>
            Pass{' '}
            <input
              aria-label="Archive pass"
              type="number"
              min="1"
              placeholder="All"
              value={orbit}
              onChange={(e) => {
                setOrbit(e.target.value);
                setOffset(0);
              }}
              style={{ width: 80 }}
            />
          </label>
          <label>
            Identifier{' '}
            <input
              data-testid="archive-query"
              value={query}
              placeholder="Observation or product ID"
              onChange={(e) => {
                setQuery(e.target.value);
                setOffset(0);
              }}
            />
          </label>
        </div>
        {archiveError && <p role="alert">{archiveError}</p>}
        {archive && (
          <>
            <p>
              {archive.total.toLocaleString()} archive records · Metadata does not imply usable
              local pixels.
            </p>
            <div className={styles.tableWrap}>
              <table data-testid="archive-table">
                <thead>
                  <tr>
                    <th>Product</th>
                    <th>Pass</th>
                    <th>UTC</th>
                    <th>Band</th>
                    <th>Status / reason</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {archive.items.map((row) => (
                    <tr key={row.product_id}>
                      <td>
                        <button onClick={() => void openDetail(row.product_id)}>
                          {row.product_id}
                        </button>
                      </td>
                      <td>{row.orbit}</td>
                      <td>{row.start_time ?? '—'}</td>
                      <td>{Array.isArray(row.bands) ? row.bands.join(', ') : row.bands}</td>
                      <td>
                        {row.status}: {row.reason}
                      </td>
                      <td>
                        <a href={row.label_url || row.source_url} target="_blank" rel="noreferrer">
                          PDS metadata
                        </a>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className={styles.toolbar}>
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>
                Previous
              </button>
              <span>
                {offset + 1}–{Math.min(offset + 100, archive.total)}
              </span>
              <button
                disabled={offset + 100 >= archive.total}
                onClick={() => setOffset(offset + 100)}
              >
                Next
              </button>
            </div>
            {archive.items.length === 0 && <p>No archive records match these filters.</p>}
          </>
        )}
      </div>
    </div>
  );
}
