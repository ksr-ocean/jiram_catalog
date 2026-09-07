/**
 * The Strips view: library table, a map of strip centres, the viewer, and
 * the per-strip statistics.
 *
 * The statistics are behind a toggle and start hidden: they are three Plotly
 * figures over a server round trip, and what the user is usually looking at
 * is the image, which takes the free space back whenever they are away.
 *
 * "Show in Strips" from the selection tray sets the orbit filter here, and
 * the filter row says so in words -- v1's "send to Strips" changed a hidden
 * filter and looked like it had done nothing.
 *
 * A JunoCam strip carries several bands in one file.  The band selector picks
 * which one the viewer draws and which one the statistics panel computes for
 * (`stats?band=`); the RGB composite is assembled in the browser from three
 * `image.png?band=` requests, because the amendment gives strips a band
 * parameter and no composite endpoint of their own.
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import DeckGL from '@deck.gl/react';
import { OrthographicView, type OrthographicViewState, type PickingInfo } from '@deck.gl/core';
import { PathLayer, ScatterplotLayer } from '@deck.gl/layers';
import styles from './Views.module.css';
import { ColorBar, ImageView } from '../components/ImageView';
import { Plot } from '../components/Plot';
import { useStore } from '../store/store';
import { api } from '../api/client';
import { fmt, fmtOrbits, fmtTime } from '../lib/format';
import { downloadText } from '../lib/csv';
import { LAT_BAND_NAMES, latBandLimits } from '../lib/latbands';
import {
  DEFAULT_STRIP_FILTERS,
  filterStrips,
  stripBandOptions,
  type StripFilters,
  type StripRow,
} from '../lib/stripsTable';
import {
  FLAT_SIGMA_RANGE,
  hasRgb,
  INSTRUMENTS,
  MINNAERT_K_RANGE,
  NORM_BLURBS,
  NORM_LABELS,
  NORM_NAMES,
  normName,
  stretchFor,
} from '../lib/bands';
import { currentStripNorm } from '../store/store';
import type { NormName, StretchMode } from '../api/types';
import { categoricalColor, rgbCss } from '../lib/colorScale';
import { graticule } from '../lib/projection';
import { COLOR_MAPS, type ColorMapName } from '../lib/lut';

/** Viewer height with the statistics panel open, and with it away: the three
 *  cards are about 260 px of chart, and the image takes every one of them. */
const VIEWER_HEIGHT = 380;
const VIEWER_HEIGHT_ALONE = 640;

export function StripsView({ active }: { active: boolean }) {
  const strips = useStore((s) => s.strips);
  const stripId = useStore((s) => s.stripId);
  const stripMeta = useStore((s) => s.stripMeta);
  const stripImage = useStore((s) => s.stripImage);
  const stripImageComposite = useStore((s) => s.stripImageComposite);
  const stripStats = useStore((s) => s.stripStats);
  const openStrip = useStore((s) => s.openStrip);
  const orbitFilter = useStore((s) => s.stripOrbitFilter);
  const setStripOrbitFilter = useStore((s) => s.setStripOrbitFilter);
  const showGraticule = useStore((s) => s.showGraticule);
  const setShowGraticule = useStore((s) => s.setShowGraticule);
  const statsVisible = useStore((s) => s.statsVisible);
  const setStatsVisible = useStore((s) => s.setStatsVisible);
  const stripBands = useStore((s) => s.stripBands);
  const stripBand = useStore((s) => s.stripBand);
  const setStripBand = useStore((s) => s.setStripBand);
  const stripComposite = useStore((s) => s.stripComposite);
  const setStripComposite = useStore((s) => s.setStripComposite);
  const stripNorm = useStore((s) => s.stripNorm);
  const stripNormK = useStore((s) => s.stripNormK);
  const stripNormSigma = useStore((s) => s.stripNormSigma);
  const setStripNorm = useStore((s) => s.setStripNorm);
  const stripStretchMode = useStore((s) => s.stripStretchMode);
  const setStripStretchMode = useStore((s) => s.setStripStretchMode);

  const [filters, setFilters] = useState<StripFilters>(DEFAULT_STRIP_FILTERS);
  const [cmap, setCmap] = useState<ColorMapName>('gray');
  const [showContours, setShowContours] = useState(true);

  useEffect(() => setFilters((previous) => ({ ...previous, orbits: orbitFilter })), [orbitFilter]);

  const rows = useMemo(
    () => filterStrips(strips, filters, latBandLimits(filters.latBand)),
    [strips, filters],
  );

  const bandChoices = useMemo(
    () => stripBandOptions(strips, filters.instrument),
    [strips, filters.instrument],
  );
  const hasJunocamStrips = useMemo(
    () => strips.some((row) => row.instrument.toUpperCase() === 'JUNOCAM'),
    [strips],
  );
  const rgbReady = hasRgb(stripBands);
  const normLabel = currentStripNorm({ stripNorm, stripNormK, stripNormSigma });
  const normChoices = useMemo(() => {
    const offered = stripMeta?.norm_options?.map((name) => normName(name));
    const known = NORM_NAMES.filter((name) => !offered || offered.includes(name));
    return known.length > 0 ? known : (['none'] as NormName[]);
  }, [stripMeta]);

  const resolutionClasses = useMemo(
    () => [...new Set(strips.map((row) => row.resolution_class).filter(Boolean))].sort(),
    [strips],
  );
  const [resolutionClass, setResolutionClass] = useState('all');
  const shown = useMemo(
    () => (resolutionClass === 'all' ? rows : rows.filter((row) => row.resolution_class === resolutionClass)),
    [rows, resolutionClass],
  );

  const stats = stripStats;

  const isotropic = useMemo(
    () =>
      stats
        ? [{ type: 'scatter', mode: 'lines', x: stats.k, y: stats.E, line: { color: '#6ab0f3' } }]
        : [],
    [stats],
  );
  const oneD = useMemo(
    () =>
      stats
        ? [
            { type: 'scatter', mode: 'lines', name: 'along x', x: stats.k_x, y: stats.P_x, line: { color: '#7fd1a6' } },
            { type: 'scatter', mode: 'lines', name: 'along y', x: stats.k_y, y: stats.P_y, line: { color: '#f0a35e' } },
          ]
        : [],
    [stats],
  );
  const structure = useMemo(
    () =>
      stats
        ? [
            { type: 'scatter', mode: 'lines', name: 'S2', x: stats.r_m, y: stats.S2, line: { color: '#6ab0f3' } },
            {
              type: 'scatter',
              mode: 'lines',
              name: 'S3',
              x: stats.r_m,
              y: stats.S3,
              yaxis: 'y2',
              line: { color: '#f2705f' },
            },
          ]
        : [],
    [stats],
  );

  return (
    <div className={styles.view}>
      <div className={styles.toolbar} data-testid="strip-filters">
        <div className={styles.group}>
          <label htmlFor="s-latband">latitude band</label>
          <select
            id="s-latband"
            data-testid="strip-lat-band"
            value={filters.latBand}
            onChange={(event) => setFilters({ ...filters, latBand: event.target.value })}
          >
            <option value="all">all</option>
            {LAT_BAND_NAMES.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        {hasJunocamStrips && (
          <div className={styles.group}>
            <label htmlFor="s-instrument">instrument</label>
            <select
              id="s-instrument"
              data-testid="strip-instrument"
              value={filters.instrument}
              onChange={(event) => {
                const instrument = event.target.value as StripFilters['instrument'];
                const keep = stripBandOptions(strips, instrument).includes(filters.band);
                setFilters({ ...filters, instrument, band: keep ? filters.band : 'all' });
              }}
            >
              <option value="all">both</option>
              {INSTRUMENTS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className={styles.group}>
          <label htmlFor="s-band">band</label>
          <select
            id="s-band"
            data-testid="strip-band-filter"
            value={filters.band}
            onChange={(event) => setFilters({ ...filters, band: event.target.value })}
          >
            <option value="all">all</option>
            {bandChoices.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.group}>
          <label htmlFor="s-res">resolution class</label>
          <select id="s-res" value={resolutionClass} onChange={(event) => setResolutionClass(event.target.value)}>
            <option value="all">all</option>
            {resolutionClasses.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <div className={styles.group}>
          <label htmlFor="s-valid">valid frac &gt;=</label>
          <input
            id="s-valid"
            type="number"
            step="0.05"
            min={0}
            max={1}
            style={{ width: 66 }}
            value={filters.validMin}
            onChange={(event) => setFilters({ ...filters, validMin: Number(event.target.value) })}
          />
        </div>
        <div className={styles.group}>
          <label>
            <input
              type="checkbox"
              checked={filters.daysideOnly}
              onChange={(event) => setFilters({ ...filters, daysideOnly: event.target.checked })}
            />
            dayside only
          </label>
        </div>
        <div className={styles.sep} />
        <span className={styles.muted} data-testid="strip-count">
          {shown.length} of {strips.length} strips
        </span>
        {orbitFilter && orbitFilter.length > 0 && (
          <span className={styles.muted} data-testid="strip-orbit-filter">
            limited to the tray&apos;s orbits {fmtOrbits(orbitFilter)}{' '}
            <button onClick={() => setStripOrbitFilter(null)}>clear</button>
          </span>
        )}
      </div>

      <div className={styles.split}>
        <div className={styles.listWrap}>
          <table data-testid="strips-table">
            <thead>
              <tr>
                <th>strip_id</th>
                <th>orbit</th>
                <th>instrument</th>
                <th>band</th>
                <th>start</th>
                <th>lat</th>
                <th>km/px</th>
                <th>valid</th>
                <th>dayside</th>
              </tr>
            </thead>
            <tbody>
              {shown.slice(0, 400).map((row) => (
                <tr
                  key={row.strip_id}
                  aria-selected={row.strip_id === stripId}
                  onClick={() => void openStrip(row.strip_id)}
                  style={{ cursor: 'pointer' }}
                >
                  <td>{row.strip_id}</td>
                  <td>{row.orbit}</td>
                  <td>{row.instrument}</td>
                  <td>{row.bands.length > 1 ? row.bands.join(';') : row.band}</td>
                  <td>{fmtTime(row.time_start_ms)}</td>
                  <td>{fmt(row.center_lat, 1)}</td>
                  <td>{fmt(row.km_per_px, 1)}</td>
                  <td>{fmt(row.valid_frac, 2)}</td>
                  <td>{fmt(row.dayside_frac, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <StripCentresMap rows={shown} onPick={(row) => void openStrip(row.strip_id)} selected={stripId} />
      </div>

      {stripId && (
        <>
          <div className={styles.toolbar}>
            <b data-testid="current-strip">{stripId}</b>
            <div className={styles.sep} />
            {stripBands.length > 0 && (
              <>
                <label htmlFor="strip-band">band</label>
                <select
                  id="strip-band"
                  data-testid="strip-band-select"
                  value={stripComposite ? '__rgb' : stripBand ?? ''}
                  onChange={(event) => {
                    if (event.target.value === '__rgb') {
                      setStripComposite(true);
                      return;
                    }
                    if (stripComposite) setStripComposite(false);
                    setStripBand(event.target.value);
                  }}
                >
                  {stripBands.map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                  {rgbReady && <option value="__rgb">RGB composite</option>}
                </select>
                <div className={styles.sep} />
              </>
            )}
            <label htmlFor="strip-cmap">colour map</label>
            <select
              id="strip-cmap"
              data-testid="strip-cmap-select"
              value={cmap}
              onChange={(event) => setCmap(event.target.value as ColorMapName)}
              disabled={stripComposite}
              title={stripComposite ? 'the composite carries its own colour' : undefined}
            >
              {COLOR_MAPS.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
            {stripMeta && !stripComposite && (
              <ColorBar
                cmap={cmap}
                vmin={stretchFor(stripMeta.stretch, normLabel, stripBand).p1}
                vmax={stretchFor(stripMeta.stretch, normLabel, stripBand).p99}
              />
            )}
            {stripComposite && (
              <span className={styles.muted} data-testid="strip-composite-note">
                RGB composite of {stripBands.join(', ')}
              </span>
            )}
            <div className={styles.sep} />
            <label htmlFor="strip-norm" title={NORM_BLURBS[stripNorm]}>
              illumination
            </label>
            <select
              id="strip-norm"
              data-testid="strip-norm-select"
              value={stripNorm}
              title={NORM_BLURBS[stripNorm]}
              onChange={(event) => setStripNorm(event.target.value as NormName)}
            >
              {normChoices.map((name) => (
                <option key={name} value={name} title={NORM_BLURBS[name]}>
                  {NORM_LABELS[name]}
                </option>
              ))}
            </select>
            {stripNorm === 'minnaert' && (
              <input
                aria-label="Minnaert k"
                data-testid="strip-norm-k"
                type="range"
                min={MINNAERT_K_RANGE[0]}
                max={MINNAERT_K_RANGE[1]}
                step={0.05}
                value={stripNormK}
                style={{ width: 100 }}
                onChange={(event) => setStripNorm('minnaert', Number(event.target.value))}
              />
            )}
            {stripNorm === 'flat' && (
              <input
                aria-label="flatten sigma"
                data-testid="strip-norm-sigma"
                type="range"
                min={FLAT_SIGMA_RANGE[0]}
                max={FLAT_SIGMA_RANGE[1]}
                step={8}
                value={stripNormSigma}
                style={{ width: 100 }}
                onChange={(event) => setStripNorm('flat', undefined, Number(event.target.value))}
              />
            )}
            <select
              aria-label="stretch mapping"
              data-testid="strip-stretch-mode"
              value={stripStretchMode}
              onChange={(event) => setStripStretchMode(event.target.value as StretchMode)}
            >
              <option value="linear">Linear</option>
              <option value="asinh">Asinh</option>
            </select>
            <div className={styles.sep} />
            <label>
              <input
                type="checkbox"
                checked={showGraticule}
                onChange={(event) => setShowGraticule(event.target.checked)}
              />
              graticule
            </label>
            <label>
              <input
                type="checkbox"
                checked={showContours}
                onChange={(event) => setShowContours(event.target.checked)}
              />
              local-time contours
            </label>
            <button
              data-testid="toggle-stats"
              aria-pressed={statsVisible}
              onClick={() => setStatsVisible(!statsVisible)}
            >
              {statsVisible ? 'Hide statistics' : 'Show statistics'}
            </button>
            <button
              data-testid="download-stats"
              disabled={!stats}
              onClick={() => stats && downloadText(`${stripId}_stats.json`, JSON.stringify(stats, null, 1), 'application/json')}
            >
              Download stats (JSON)
            </button>
          </div>

          <ImageView
            image={stripImage}
            cmap={cmap}
            composite={stripImageComposite}
            graticule={stripMeta?.graticule ?? null}
            contours={showContours ? stripMeta?.local_time_contours ?? null : null}
            showGraticule={showGraticule}
            testId="strip-image"
            height={statsVisible ? VIEWER_HEIGHT : VIEWER_HEIGHT_ALONE}
            grow={!statsVisible}
            emptyMessage="loading the strip..."
          />

          {statsVisible && (
            <div className={styles.charts} data-testid="strip-stats">
              <div className={styles.card}>
                <h3>
                  isotropic spectrum{stripBand ? ` (${stripBand})` : ''}
                  {stripNorm === 'none' ? '' : `, ${NORM_LABELS[stripNorm]}`}
                </h3>
                {active && stats && (
                  <Plot
                    testId="plot-isotropic"
                    data={isotropic}
                    height={210}
                    layout={{
                      xaxis: { type: 'log', title: { text: 'k (rad/m)' } },
                      yaxis: { type: 'log', title: { text: 'E(k)' } },
                      annotations: annotateWavelengths(stats.k),
                    }}
                  />
                )}
              </div>
              <div className={styles.card}>
                <h3>1-D spectra (x, y)</h3>
                {active && stats && (
                  <Plot
                    testId="plot-1d"
                    data={oneD}
                    height={210}
                    layout={{
                      showlegend: true,
                      legend: { x: 0.6, y: 1 },
                      xaxis: { type: 'log', title: { text: 'k (rad/m)' } },
                      yaxis: { type: 'log', title: { text: 'P' } },
                    }}
                  />
                )}
              </div>
              <div className={styles.card}>
                <h3>structure functions</h3>
                {active && stats && (
                  <Plot
                    testId="plot-structure"
                    data={structure}
                    height={210}
                    layout={{
                      showlegend: true,
                      legend: { x: 0.05, y: 1 },
                      xaxis: { type: 'log', title: { text: 'r (m)' } },
                      yaxis: { type: 'log', title: { text: 'S2' } },
                      yaxis2: { overlaying: 'y', side: 'right', title: { text: 'S3 (signed)' } },
                    }}
                  />
                )}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** A wavelength scale for the spectrum's log-log x axis. */
function annotateWavelengths(k: number[]): Record<string, unknown>[] {
  if (!k || k.length === 0) return [];
  const finite = k.filter((value) => Number.isFinite(value) && value > 0);
  if (finite.length === 0) return [];
  return [
    {
      xref: 'paper',
      yref: 'paper',
      x: 1,
      y: 1.08,
      showarrow: false,
      font: { size: 9 },
      text: `wavelength ${(((2 * Math.PI) / Math.max(...finite)) / 1000).toFixed(1)} to ${(
        ((2 * Math.PI) / Math.min(...finite)) / 1000
      ).toFixed(0)} km`,
    },
  ];
}

/** Strip centres in cylindrical coordinates, coloured by year. */
function StripCentresMap({
  rows,
  onPick,
  selected,
}: {
  rows: StripRow[];
  onPick: (row: StripRow) => void;
  selected: string | null;
}) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<[number, number]>([320, 300]);
  const [hover, setHover] = useState<{ row: StripRow; x: number; y: number } | null>(null);
  const viewState: OrthographicViewState = useMemo(() => {
    const zoom = Math.log2(Math.min(size[0] / 360, size[1] / 180));
    return { target: [180, 0, 0], zoom };
  }, [size]);

  useEffect(() => {
    const node = wrapRef.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setSize([node.clientWidth || 320, node.clientHeight || 300]));
    observer.observe(node);
    setSize([node.clientWidth || 320, node.clientHeight || 300]);
    return () => observer.disconnect();
  }, []);

  const layers = useMemo(
    () => [
      new PathLayer({
        id: 'strip-graticule',
        data: graticule('cyl'),
        getPath: (d: { path: [number, number][] }) => d.path,
        getColor: [70, 88, 112, 180],
        getWidth: 1,
        widthUnits: 'pixels',
        widthMinPixels: 1,
      }),
      new ScatterplotLayer<StripRow>({
        id: 'strip-centres',
        data: rows,
        pickable: true,
        getPosition: (row) => [((row.center_lon_east % 360) + 360) % 360, row.center_lat],
        getFillColor: (row) =>
          categoricalColor(Number.isFinite(row.time_mid_ms) ? new Date(row.time_mid_ms).getUTCFullYear() : 0),
        getLineColor: [255, 255, 255, 220],
        stroked: true,
        lineWidthUnits: 'pixels',
        getLineWidth: (row) => (row.strip_id === selected ? 2 : 0),
        radiusUnits: 'pixels',
        getRadius: 4,
        radiusMinPixels: 3,
        onHover: (info: PickingInfo<StripRow>) =>
          setHover(info.object ? { row: info.object, x: info.x ?? 0, y: info.y ?? 0 } : null),
        onClick: (info: PickingInfo<StripRow>) => info.object && onPick(info.object),
        updateTriggers: { getLineWidth: selected },
      }),
    ],
    [rows, selected, onPick],
  );

  return (
    <div className={styles.mapWrap} style={{ height: 300 }} ref={wrapRef} data-testid="strip-map">
      <DeckGL
        views={new OrthographicView({ id: 'strip-ortho', flipY: false })}
        viewState={viewState}
        controller={false}
        layers={layers as never}
        width={size[0]}
        height={size[1]}
      />
      {hover && (
        <div
          className={`${styles.tooltip} tooltip`}
          data-testid="strip-tooltip"
          style={{ left: Math.min(hover.x + 10, size[0] - 200), top: Math.min(hover.y + 10, size[1] - 60) }}
        >
          <div>
            <b>{hover.row.strip_id}</b>
          </div>
          <div>
            orbit {hover.row.orbit} &middot; {fmt(hover.row.km_per_px, 1)} km/px
          </div>
          <div>{fmtTime(hover.row.time_mid_ms)}</div>
        </div>
      )}
      <div className={styles.legend}>
        <div>year</div>
        {[...new Set(rows.map((row) => (Number.isFinite(row.time_mid_ms) ? new Date(row.time_mid_ms).getUTCFullYear() : 0)))]
          .filter((year) => year > 0)
          .sort()
          .slice(0, 8)
          .map((year) => (
            <div className={styles.swatchRow} key={year}>
              <span className={styles.swatch} style={{ background: rgbCss(categoricalColor(year)) }} />
              <span>{year}</span>
            </div>
          ))}
      </div>
    </div>
  );
}

export const _stripsInternals = { annotateWavelengths, api };
