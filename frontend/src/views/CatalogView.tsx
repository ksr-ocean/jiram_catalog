/**
 * The catalog view: map, coverage charts, table.
 *
 * The map is filtered in the browser and the charts by the server, using the
 * same parameter names, so the two cannot disagree about what "the current
 * set" is -- the failure that made v1's panels untrustworthy.
 */
import { useMemo } from 'react';
import styles from './Views.module.css';
import { CatalogFilters } from './CatalogFilters';
import { CatalogMap } from './CatalogMap';
import { Plot } from '../components/Plot';
import { Modal } from '../components/Modal';
import { frameKey, useStore } from '../store/store';
import { fmt, fmtTime } from '../lib/format';
import { downloadText, toCsv } from '../lib/csv';
import { LAT_BAND_NAMES } from '../lib/latbands';

const PAGE_SIZE = 200;

const TABLE_COLUMNS = [
  'product_id',
  'instrument',
  'half',
  'bands',
  'quality_tier',
  'orbit',
  'seq_id',
  'start_time',
  'bore_lat',
  'bore_lon_east',
  'bore_emission',
  'median_pixel_km',
  'on_planet_frac',
  'dayside_frac',
];

export function CatalogView({ active }: { active: boolean }) {
  const columns = useStore((s) => s.columns);
  const filtered = useStore((s) => s.filtered);
  const summary = useStore((s) => s.summary);
  const page = useStore((s) => s.page);
  const setPage = useStore((s) => s.setPage);
  const selectionKeys = useStore((s) => s.selectionKeys);
  const addToSelection = useStore((s) => s.addToSelection);
  const removeFromSelection = useStore((s) => s.removeFromSelection);
  const detail = useStore((s) => s.detail);
  const openDetail = useStore((s) => s.openDetail);

  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const rows = useMemo(
    () => Array.from(filtered.subarray(current * PAGE_SIZE, (current + 1) * PAGE_SIZE)),
    [filtered, current],
  );

  const latBandChart = useMemo(() => {
    const counts = new Map((summary?.by_lat_band ?? []).map((row) => [row.band, row.n]));
    return [
      {
        type: 'bar',
        x: LAT_BAND_NAMES as unknown as string[],
        y: LAT_BAND_NAMES.map((name) => counts.get(name) ?? 0),
        marker: { color: '#6ab0f3' },
      },
    ];
  }, [summary]);

  const orbitChart = useMemo(
    () => [
      {
        type: 'bar',
        x: (summary?.by_orbit ?? []).map((row) => row.orbit),
        y: (summary?.by_orbit ?? []).map((row) => row.n),
        marker: { color: '#7fd1a6' },
      },
    ],
    [summary],
  );

  const monthChart = useMemo(
    () => [
      {
        type: 'bar',
        x: (summary?.by_month ?? []).map((row) => row.month),
        y: (summary?.by_month ?? []).map((row) => row.n),
        marker: { color: '#f0a35e' },
      },
    ],
    [summary],
  );

  const downloadCsv = () => {
    const body = Array.from(filtered, (i) => [
      columns.productId[i],
      columns.instrument[i],
      columns.half[i],
      columns.bands[i],
      columns.qualityTier[i],
      columns.orbit[i],
      columns.seqId[i],
      fmtTime(columns.startTimeMs[i]),
      fmt(columns.boreLat[i], 4),
      fmt(columns.boreLonEast[i], 4),
      fmt(columns.boreEmission[i], 3),
      fmt(columns.medianPixelKm[i], 4),
      fmt(columns.onPlanetFrac[i], 4),
      fmt(columns.daysideFrac[i], 4),
    ]);
    downloadText('jiram_catalog_filtered.csv', toCsv(TABLE_COLUMNS, body));
  };

  return (
    <div className={styles.view}>
      <CatalogFilters />
      <CatalogMap />

      <div className={styles.charts}>
        <div className={styles.card}>
          <h3>frames per latitude band</h3>
          {active && (
            <Plot testId="chart-lat-band" data={latBandChart} layout={{ xaxis: { tickangle: -35 } }} height={190} />
          )}
        </div>
        <div className={styles.card}>
          <h3>frames per orbit</h3>
          {active && <Plot testId="chart-orbit" data={orbitChart} height={190} />}
        </div>
        <div className={styles.card}>
          <h3>frames per month</h3>
          {active && <Plot testId="chart-month" data={monthChart} height={190} />}
        </div>
      </div>

      <div className={styles.toolbar}>
        <span className={styles.muted} data-testid="table-caption">
          {filtered.length.toLocaleString()} rows pass the filters
          {summary ? ` (server agrees: ${summary.n.toLocaleString()})` : ''}
        </span>
        <div className={styles.sep} />
        <button onClick={() => setPage(Math.max(0, current - 1))} disabled={current === 0}>
          prev
        </button>
        <span className={styles.muted} data-testid="page-label">
          page {current + 1} of {pages}
        </span>
        <button onClick={() => setPage(Math.min(pages - 1, current + 1))} disabled={current >= pages - 1}>
          next
        </button>
        <div className={styles.sep} />
        <button data-testid="add-page-to-selection" onClick={() => addToSelection(Uint32Array.from(rows))}>
          add this page to selection
        </button>
        <button data-testid="download-csv" onClick={downloadCsv} disabled={filtered.length === 0}>
          download CSV
        </button>
      </div>

      <div className={styles.tableWrap}>
        <table data-testid="catalog-table">
          <thead>
            <tr>
              <th />
              {TABLE_COLUMNS.map((name) => (
                <th key={name}>{name}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((i) => {
              const key = frameKey(columns.productId[i], columns.half[i]);
              const checked = selectionKeys.has(key);
              return (
                <tr key={key} aria-selected={checked}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`select ${columns.productId[i]}`}
                      checked={checked}
                      onChange={(event) =>
                        event.target.checked
                          ? addToSelection(Uint32Array.of(i))
                          : removeFromSelection(Uint32Array.of(i))
                      }
                    />
                  </td>
                  <td>
                    <a
                      href="#detail"
                      onClick={(event) => {
                        event.preventDefault();
                        void openDetail(columns.productId[i]);
                      }}
                    >
                      {columns.productId[i]}
                    </a>
                  </td>
                  <td>{columns.instrument[i]}</td>
                  <td>{columns.half[i]}</td>
                  <td>{columns.bands[i]}</td>
                  <td>{columns.qualityTier[i]}</td>
                  <td>{columns.orbit[i]}</td>
                  <td>{columns.seqId[i]}</td>
                  <td>{fmtTime(columns.startTimeMs[i])}</td>
                  <td>{fmt(columns.boreLat[i], 2)}</td>
                  <td>{fmt(columns.boreLonEast[i], 2)}</td>
                  <td>{fmt(columns.boreEmission[i], 1)}</td>
                  <td>{fmt(columns.medianPixelKm[i], 2)}</td>
                  <td>{fmt(columns.onPlanetFrac[i], 3)}</td>
                  <td>{fmt(columns.daysideFrac[i], 3)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {detail && <DetailCard />}
    </div>
  );
}

function DetailCard() {
  const detail = useStore((s) => s.detail);
  const closeDetail = useStore((s) => s.closeDetail);
  const columns = useStore((s) => s.columns);
  const keyIndex = useStore((s) => s.keyIndex);
  const addToSelection = useStore((s) => s.addToSelection);
  if (!detail) return null;
  const productId = String(detail.product_id);
  const halves = detail.halves ?? ['L', 'M'];
  return (
    <Modal title={productId} onClose={closeDetail}>
      <div className={styles.kv} data-testid="frame-detail">
        {Object.entries(detail)
          .filter(([, value]) => typeof value !== 'object' || value === null)
          .map(([name, value]) => (
            <div key={name} style={{ display: 'contents' }}>
              <span>{name}</span>
              <b>{value === null ? '--' : String(value)}</b>
            </div>
          ))}
      </div>
      <div style={{ marginTop: 10 }}>
        <button
          data-testid="detail-add-to-selection"
          onClick={() => {
            const indices: number[] = [];
            for (const half of halves) {
              const i = keyIndex.get(`${productId}|${half}`);
              if (i !== undefined) indices.push(i);
            }
            addToSelection(Uint32Array.from(indices));
            closeDetail();
          }}
        >
          add to selection ({columns.n > 0 ? halves.join(', ') : 'n/a'})
        </button>
      </div>
    </Modal>
  );
}
