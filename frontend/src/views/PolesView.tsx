/**
 * The Poles view: a stack chooser, an image player, and the movie.
 *
 * Three v1 failures are designed out here.  The colour map is a browser-side
 * LUT (see `ImageView`), so it always applies.  The movie is a native
 * `<video>` element pointing at the API's range-capable endpoint, so a
 * rendered movie is visible rather than merely written.  The player is an
 * `OrthographicView` with a scalar zoom, so panning and zooming cannot
 * distort the aspect ratio.
 *
 * A stack is one of three views of the same frames -- the snapshot per spin
 * sequence, the sweep filling in, the raw frames -- and the mode selector
 * moves between them by opening the sibling file, or by building it when the
 * mirror does not have it yet.  A JunoCam stack has only the one level, so
 * the selector shows only that one rather than offering to build two files
 * the instrument cannot produce.
 *
 * A stack with a `band` dimension gains a band selector and, when RED, GREEN
 * and BLUE are all present, an RGB composite.  The composite is the one
 * picture in the application the browser does not colour-map: it arrives from
 * the contract's `frame/{t}/rgb.png` already carrying three bands, and the
 * lookup tables apply to single-band display only.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import styles from './Views.module.css';
import { ColorBar, ImageView } from '../components/ImageView';
import { researchApi, type Readiness, type Vectors } from '../api/research';
import { humanRegion } from '../lib/labels';
import { downloadText } from '../lib/csv';
import { api, fetchImage } from '../api/client';
import { useStore } from '../store/store';
import { fmt, fmtBytes } from '../lib/format';
import { COLOR_MAPS, type ColorMapName } from '../lib/lut';
import {
  LEVEL_BLURBS,
  LEVEL_LABELS,
  availableLevels,
  levelLabel,
  orbitsOf,
  siblingsOf,
  sweepReadout,
  type StackLevel,
} from '../lib/stackModes';
import {
  bandForChannel,
  FLAT_SIGMA_RANGE,
  hasRgb,
  MINNAERT_K_RANGE,
  NORM_BLURBS,
  NORM_LABELS,
  NORM_NAMES,
  normName,
  RGB_BANDS,
  stretchFor,
  uniqueBands,
} from '../lib/bands';
import { currentNorm } from '../store/store';
import type { NormName, StretchMode } from '../api/types';

export function PolesView({ active }: { active: boolean }) {
  const stacks = useStore((s) => s.stacks);
  const stackId = useStore((s) => s.stackId);
  const meta = useStore((s) => s.stackMeta);
  const openStack = useStore((s) => s.openStack);
  const t = useStore((s) => s.t);
  const setTime = useStore((s) => s.setTime);
  const stepTime = useStore((s) => s.stepTime);
  const playing = useStore((s) => s.playing);
  const setPlaying = useStore((s) => s.setPlaying);
  const fps = useStore((s) => s.fps);
  const setFps = useStore((s) => s.setFps);
  const cmap = useStore((s) => s.cmap);
  const setCmap = useStore((s) => s.setCmap);
  const vmin = useStore((s) => s.vmin);
  const vmax = useStore((s) => s.vmax);
  const setStretch = useStore((s) => s.setStretch);
  const stackBands = useStore((s) => s.stackBands);
  const band = useStore((s) => s.band);
  const setBand = useStore((s) => s.setBand);
  const composite = useStore((s) => s.composite);
  const setComposite = useStore((s) => s.setComposite);
  const linkBands = useStore((s) => s.linkBands);
  const setLinkBands = useStore((s) => s.setLinkBands);
  const bandStretch = useStore((s) => s.bandStretch);
  const setBandStretch = useStore((s) => s.setBandStretch);
  const norm = useStore((s) => s.norm);
  const normK = useStore((s) => s.normK);
  const normSigma = useStore((s) => s.normSigma);
  const setNorm = useStore((s) => s.setNorm);
  const stretchMode = useStore((s) => s.stretchMode);
  const setStretchMode = useStore((s) => s.setStretchMode);
  const showGraticule = useStore((s) => s.showGraticule);
  const setShowGraticule = useStore((s) => s.setShowGraticule);
  const emissionAlpha = useStore((s) => s.emissionAlpha);
  const setEmissionAlpha = useStore((s) => s.setEmissionAlpha);
  const frameImage = useStore((s) => s.frameImage);
  const frameComposite = useStore((s) => s.frameComposite);
  const emissionImage = useStore((s) => s.emissionImage);
  const pushToast = useStore((s) => s.pushToast);
  const watchJob = useStore((s) => s.watchJob);
  const selectionKeys = useStore((s) => s.selectionKeys);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [draftStretch, setDraftStretch] = useState<[number, number]>([vmin, vmax]);
  const [movieNonce, setMovieNonce] = useState(0);
  // The mode the user asked for and the mirror does not hold: the only thing
  // that puts a "Build this view" button on screen.
  const [missingMode, setMissingMode] = useState<StackLevel | null>(null);
  const [building, setBuilding] = useState(false);
  const [analysisBand, setAnalysisBand] = useState<string | null>(null);
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [readinessError, setReadinessError] = useState('');
  const [vectors, setVectors] = useState<Vectors | null>(null);
  const [showVectors, setShowVectors] = useState(false);
  const [movieReady, setMovieReady] = useState(false);

  const listing = useMemo(
    () => stacks.find((stack) => stack.id === stackId) ?? null,
    [stacks, stackId],
  );
  const nTimes = meta?.times.length ?? 0;
  const level = meta?.level ?? listing?.level ?? null;
  const siblings = useMemo(() => siblingsOf(listing, stacks), [listing, stacks]);
  const sweep = sweepReadout(meta, t);
  const instrument = meta?.instrument ?? listing?.instrument ?? 'JIRAM';
  const levels = useMemo(() => availableLevels(instrument), [instrument]);
  const rgbReady = hasRgb(stackBands);
  const normLabel = currentNorm({ norm, normK, normSigma });
  // Only the models the product can actually answer for: Lambert and
  // Minnaert need a per-pixel incidence angle, and a stack built before the
  // photometry amendment carries none.
  const normChoices = useMemo(() => {
    const offered = meta?.norm_options?.map((name) => normName(name));
    const known = NORM_NAMES.filter((name) => !offered || offered.includes(name));
    return known.length > 0 ? known : (['none'] as NormName[]);
  }, [meta]);

  const physicalBand = composite ? analysisBand : band;
  const needsBand = composite && !analysisBand;
  useEffect(() => {
    setAnalysisBand(null);
    setMovieReady(false);
  }, [stackId]);
  useEffect(() => {
    setReadiness(null);
    setReadinessError('');
    setMovieReady(false);
    if (!stackId || !meta || needsBand) return;
    let current = true;
    researchApi
      .readiness(stackId, physicalBand, normLabel)
      .then((data) => {
        if (current) setReadiness(data);
      })
      .catch((e) => {
        if (current) setReadinessError(e.message);
      });
    return () => {
      current = false;
    };
  }, [stackId, meta, physicalBand, normLabel, needsBand]);
  useEffect(() => {
    if (!stackId || !showVectors) return;
    let current = true;
    setVectors(null);
    researchApi
      .vectors(stackId, t)
      .then((data) => {
        if (current) setVectors(data);
      })
      .catch((e) => {
        if (current)
          setVectors({
            status: 'unassessed',
            reason: e.message,
            units: 'm s-1',
            provenance: null,
            features: [],
          });
      });
    return () => {
      current = false;
    };
  }, [stackId, t, showVectors]);

  // A new stack answers whatever mode question was open.
  useEffect(() => {
    setMissingMode(null);
  }, [stackId]);

  useEffect(() => setDraftStretch([vmin, vmax]), [vmin, vmax]);

  // Refetching a frame for a new stretch is a network round trip; debounce it.
  useEffect(() => {
    if (draftStretch[0] === vmin && draftStretch[1] === vmax) return;
    const timer = setTimeout(() => setStretch(draftStretch[0], draftStretch[1]), 350);
    return () => clearTimeout(timer);
  }, [draftStretch, vmin, vmax, setStretch]);

  useEffect(() => {
    if (!playing || nTimes === 0) return;
    const timer = setInterval(() => stepTime(1), Math.max(40, 1000 / Math.max(1, fps)));
    return () => clearInterval(timer);
  }, [playing, fps, nTimes, stepTime]);

  // Left/right step the time slider whenever this view is on screen.
  useEffect(() => {
    if (!active) return;
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ['INPUT', 'SELECT', 'TEXTAREA'].includes(target.tagName)) return;
      if (event.key === 'ArrowLeft') {
        stepTime(-1);
        event.preventDefault();
      } else if (event.key === 'ArrowRight') {
        stepTime(1);
        event.preventDefault();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [active, stepTime]);

  // The emission overlay is fetched only once it is asked for.
  useEffect(() => {
    if (!stackId || emissionAlpha <= 0) return;
    let cancelled = false;
    void fetchImage(api.stackEmissionUrl(stackId, t))
      .then((image) => {
        if (!cancelled) useStore.setState({ emissionImage: image });
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [stackId, t, emissionAlpha]);

  const chooseMode = useCallback(
    (target: StackLevel) => {
      const sibling = siblings[target];
      if (sibling) {
        setMissingMode(null);
        if (sibling !== stackId) void openStack(sibling);
        return;
      }
      setMissingMode(target);
    },
    [siblings, stackId, openStack],
  );

  const buildMode = useCallback(
    (target: StackLevel) => {
      if (!listing) return;
      const orbits = orbitsOf(listing.id);
      setBuilding(true);
      void api
        .buildStack({
          region: listing.region,
          band: listing.band,
          level: target,
          ...(orbits ? { orbits } : {}),
        })
        .then(({ job_id }) => {
          pushToast('info', `building "${LEVEL_LABELS[target]}" (job ${job_id})`);
          watchJob(job_id, (job) => {
            setBuilding(false);
            if (job.status !== 'done') {
              pushToast('error', `build failed: ${job.message ?? ''}`);
              return;
            }
            const result = job.result as { stack_id?: string } | null;
            pushToast('info', `built "${LEVEL_LABELS[target]}"`);
            void useStore
              .getState()
              .loadStacks()
              .then(() => {
                setMissingMode(null);
                if (result?.stack_id) void openStack(result.stack_id);
              });
          });
        })
        .catch((error: Error) => {
          setBuilding(false);
          pushToast('error', `build: ${error.message}`);
        });
    },
    [listing, openStack, pushToast, watchJob],
  );

  const renderMovie = useCallback(() => {
    if (!stackId) return;
    void api
      .renderMovie(stackId, { fps, pct: [1, 99], cmap, band: physicalBand, norm: normLabel })
      .then(({ job_id }) => {
        pushToast('info', `movie render started (job ${job_id})`);
        watchJob(job_id, (job) => {
          if (job.status === 'done') {
            pushToast('info', 'movie rendered');
            setMovieReady(true);
            void useStore.getState().loadStacks();
            setMovieNonce((n) => n + 1);
            videoRef.current?.load();
          } else {
            pushToast('error', `movie render failed: ${job.message ?? ''}`);
          }
        });
      })
      .catch((error: Error) => pushToast('error', `movie: ${error.message}`));
  }, [stackId, fps, cmap, physicalBand, normLabel, pushToast, watchJob]);

  const exportTriples = useCallback(() => {
    if (!stackId || !readiness?.ready || needsBand) return;
    void api
      .exportTriples(stackId, { crop_to_valid: true, band: physicalBand, norm: normLabel })
      .then(({ job_id }) => {
        pushToast('info', `export started (job ${job_id})`);
        watchJob(job_id, (job) => {
          const result = job.result as { out_dir?: string; n_realizations?: number } | null;
          pushToast(
            job.status === 'done' ? 'info' : 'error',
            job.status === 'done'
              ? `exported ${result?.n_realizations ?? '?'} realizations to ${result?.out_dir ?? '?'}`
              : `export failed: ${job.message ?? ''}`,
          );
        });
      })
      .catch((error: Error) => pushToast('error', `export: ${error.message}`));
  }, [stackId, readiness, needsBand, physicalBand, normLabel, pushToast, watchJob]);

  const perTime = meta?.per_time?.[t];

  return (
    <div className={styles.view}>
      <div className={styles.toolbar}>
        <div className={styles.group}>
          <label htmlFor="stack-select">stack</label>
          <select
            id="stack-select"
            data-testid="stack-select"
            value={stackId ?? ''}
            onChange={(event) => void openStack(event.target.value)}
          >
            <option value="" disabled>
              choose a stack ({stacks.length} available)
            </option>
            {stacks.map((stack) => (
              <option key={stack.id} value={stack.id}>
                {humanRegion(stack.region)} · {stack.instrument ?? 'JIRAM'} ·{' '}
                {uniqueBands(stack.bands).join('/') || stack.band}{' '}
                {stack.label ?? levelLabel(stack.level)}, {stack.n_time} steps
                {stack.has_movie ? ' [movie]' : ''}
              </option>
            ))}
          </select>
        </div>
        {listing && (
          <span className={styles.badge} data-testid="stack-label">
            {listing.label ?? levelLabel(listing.level)}
          </span>
        )}
        {listing && (
          <span className={styles.muted} data-testid="stack-summary">
            {listing.shape[0]} x {listing.shape[1]} at {fmt(listing.km_per_px, 1)} km/px,{' '}
            {fmtBytes(listing.size_bytes)}
          </span>
        )}
        <div className={styles.sep} />
        <span className={styles.muted}>
          {selectionKeys.size > 0
            ? `${selectionKeys.size} frames in the tray -- "Build stack..." makes one from them`
            : 'Select observations in Explore to build a time series'}
        </span>
      </div>

      <details className={styles.modeHelp} data-testid="mode-help">
        <summary>How time-series modes differ</summary>
        {levels.map((name) => (
          <div key={name}>{LEVEL_BLURBS[name]}</div>
        ))}
      </details>

      {!stackId ? (
        <div className={styles.card}>
          <p className={styles.muted}>
            Choose a time series above to inspect repeated views, or build one from observations in
            your selection.
          </p>
        </div>
      ) : (
        <div className={styles.stack}>
          <div className={styles.stack}>
            <div
              className={styles.toolbar}
              data-testid="mode-bar"
              role="group"
              aria-label="viewing mode"
            >
              {levels.map((name) => (
                <button
                  key={name}
                  data-testid={`mode-${name}`}
                  className={`${styles.modeButton} ${level === name ? styles.modeActive : ''}`}
                  aria-pressed={level === name}
                  onClick={() => chooseMode(name)}
                >
                  {LEVEL_LABELS[name]}
                </button>
              ))}
              {missingMode && (
                <>
                  <div className={styles.sep} />
                  <span className={styles.muted}>
                    this region has no "{LEVEL_LABELS[missingMode]}" stack yet
                  </span>
                  <button
                    data-testid="build-mode"
                    disabled={building}
                    onClick={() => buildMode(missingMode)}
                  >
                    {building ? 'building...' : 'Build this view'}
                  </button>
                </>
              )}
            </div>

            <div className={styles.toolbar}>
              <button
                data-testid="play-pause"
                aria-pressed={playing}
                onClick={() => setPlaying(!playing)}
                disabled={nTimes === 0}
              >
                {playing ? 'pause' : 'play'}
              </button>
              <button aria-label="previous frame" onClick={() => stepTime(-1)}>
                &lt;
              </button>
              <button aria-label="next frame" onClick={() => stepTime(1)}>
                &gt;
              </button>
              <label htmlFor="time-slider" data-testid="time-readout">
                time {t + 1}/{nTimes}
                {sweep ? ` -- ${sweep}` : ''}
              </label>
              <input
                id="time-slider"
                data-testid="time-slider"
                type="range"
                min={0}
                max={Math.max(0, nTimes - 1)}
                value={t}
                style={{ width: 220 }}
                onChange={(event) => setTime(Number(event.target.value))}
              />
              <label htmlFor="fps">speed</label>
              <input
                id="fps"
                type="number"
                min={1}
                max={30}
                style={{ width: 54 }}
                value={fps}
                onChange={(event) => setFps(Number(event.target.value))}
              />
              {stackBands.length > 0 && (
                <>
                  <div className={styles.sep} />
                  <label htmlFor="band-select">band</label>
                  <select
                    id="band-select"
                    data-testid="band-select"
                    value={composite ? '__rgb' : (band ?? '')}
                    onChange={(event) => {
                      if (event.target.value === '__rgb') {
                        setComposite(true);
                        return;
                      }
                      if (composite) setComposite(false);
                      setBand(event.target.value);
                    }}
                  >
                    {stackBands.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                    {rgbReady && <option value="__rgb">RGB composite</option>}
                  </select>
                </>
              )}
              <div className={styles.sep} />
              <label htmlFor="cmap-select">colour map</label>
              <select
                id="cmap-select"
                data-testid="cmap-select"
                value={cmap}
                onChange={(event) => setCmap(event.target.value as ColorMapName)}
                disabled={composite}
                title={composite ? 'the composite carries its own colour' : undefined}
              >
                {COLOR_MAPS.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              {composite ? (
                <span className={styles.muted} data-testid="composite-note">
                  RGB composite:{' '}
                  {(['r', 'g', 'b'] as const)
                    .map((channel, i) => bandForChannel(stackBands, channel) ?? RGB_BANDS[i])
                    .join(', ')}
                </span>
              ) : (
                <ColorBar cmap={cmap} vmin={vmin} vmax={vmax} />
              )}
              <div className={styles.sep} />
              <label>
                <input
                  type="checkbox"
                  data-testid="graticule-toggle"
                  checked={showGraticule}
                  onChange={(event) => setShowGraticule(event.target.checked)}
                />
                graticule
              </label>
              <label htmlFor="emission-alpha">emission overlay</label>
              <input
                id="emission-alpha"
                data-testid="emission-alpha"
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={emissionAlpha}
                style={{ width: 100 }}
                onChange={(event) => setEmissionAlpha(Number(event.target.value))}
              />
            </div>

            <details className={styles.card}>
              <summary>Advanced illumination and display stretch</summary>
              <div className={styles.toolbar} data-testid="illumination-bar">
                <label htmlFor="norm-select" title={NORM_BLURBS[norm]}>
                  illumination
                </label>
                <select
                  id="norm-select"
                  data-testid="norm-select"
                  value={norm}
                  title={NORM_BLURBS[norm]}
                  onChange={(event) => setNorm(event.target.value as NormName)}
                >
                  {normChoices.map((name) => (
                    <option key={name} value={name} title={NORM_BLURBS[name]}>
                      {NORM_LABELS[name]}
                    </option>
                  ))}
                </select>
                {norm === 'minnaert' && (
                  <>
                    <label htmlFor="norm-k">k {normK.toFixed(2)}</label>
                    <input
                      id="norm-k"
                      data-testid="norm-k"
                      type="range"
                      min={MINNAERT_K_RANGE[0]}
                      max={MINNAERT_K_RANGE[1]}
                      step={0.05}
                      value={normK}
                      style={{ width: 110 }}
                      onChange={(event) => setNorm('minnaert', Number(event.target.value))}
                    />
                  </>
                )}
                {norm === 'flat' && (
                  <>
                    <label htmlFor="norm-sigma">sigma {normSigma} px</label>
                    <input
                      id="norm-sigma"
                      data-testid="norm-sigma"
                      type="range"
                      min={FLAT_SIGMA_RANGE[0]}
                      max={FLAT_SIGMA_RANGE[1]}
                      step={8}
                      value={normSigma}
                      style={{ width: 110 }}
                      onChange={(event) => setNorm('flat', undefined, Number(event.target.value))}
                    />
                  </>
                )}
                <div className={styles.sep} />
                <label htmlFor="stretch-mode">stretch</label>
                <select
                  id="stretch-mode"
                  data-testid="stretch-mode"
                  value={stretchMode}
                  title="how the display range is laid out over the eight bits"
                  onChange={(event) => setStretchMode(event.target.value as StretchMode)}
                >
                  <option value="linear">Linear</option>
                  <option value="asinh">Asinh</option>
                </select>
                <span className={styles.muted} data-testid="norm-note">
                  {NORM_BLURBS[norm]}
                </span>
              </div>

              <div className={styles.toolbar}>
                <label htmlFor="vmin">vmin</label>
                <input
                  id="vmin"
                  data-testid="vmin"
                  type="number"
                  step="any"
                  style={{ width: 110 }}
                  value={draftStretch[0]}
                  onChange={(event) =>
                    setDraftStretch([Number(event.target.value), draftStretch[1]])
                  }
                />
                <label htmlFor="vmax">vmax</label>
                <input
                  id="vmax"
                  data-testid="vmax"
                  type="number"
                  step="any"
                  style={{ width: 110 }}
                  value={draftStretch[1]}
                  onChange={(event) =>
                    setDraftStretch([draftStretch[0], Number(event.target.value)])
                  }
                />
                <button
                  onClick={() => {
                    if (!meta) return;
                    const stretch = stretchFor(meta.stretch, normLabel, band);
                    setStretch(stretch.p1, stretch.p99);
                  }}
                  disabled={!meta}
                >
                  reset stretch
                </button>
                {composite && (
                  <label>
                    <input
                      type="checkbox"
                      data-testid="link-bands"
                      checked={linkBands}
                      onChange={(event) => setLinkBands(event.target.checked)}
                    />
                    link bands
                  </label>
                )}
              </div>

              {composite && !linkBands && (
                <div className={styles.toolbar} data-testid="band-stretch">
                  {(['r', 'g', 'b'] as const).map((channel) => {
                    const name = bandForChannel(stackBands, channel);
                    if (!name) return null;
                    const pair = bandStretch[name] ?? [vmin, vmax];
                    return (
                      <div className={styles.group} key={channel}>
                        <label htmlFor={`stretch-${channel}`}>{name}</label>
                        <input
                          id={`stretch-${channel}`}
                          data-testid={`stretch-${channel}-min`}
                          type="number"
                          step="any"
                          style={{ width: 96 }}
                          value={pair[0]}
                          onChange={(event) =>
                            setBandStretch(name, Number(event.target.value), pair[1])
                          }
                        />
                        <input
                          data-testid={`stretch-${channel}-max`}
                          type="number"
                          step="any"
                          style={{ width: 96 }}
                          value={pair[1]}
                          onChange={(event) =>
                            setBandStretch(name, pair[0], Number(event.target.value))
                          }
                        />
                      </div>
                    );
                  })}
                </div>
              )}
            </details>
            <ImageView
              image={frameImage}
              overlay={emissionImage}
              overlayAlpha={emissionAlpha}
              cmap={cmap}
              composite={frameComposite}
              graticule={meta?.graticule ?? null}
              showGraticule={showGraticule}
              testId="poles-image"
              height={480}
              vectors={showVectors ? vectors?.features : undefined}
              emptyMessage="loading the first frame..."
            />
          </div>

          <div className={styles.stack}>
            <details className={styles.card}>
              <summary>Observation metadata and provenance</summary>
              <div className={styles.kv} data-testid="frame-meta">
                <span>time</span>
                <b>{meta?.times[t] ?? '--'}</b>
                <span>product</span>
                <b>{perTime?.product_id ?? '--'}</b>
                <span>sequence</span>
                <b>{perTime?.seq_id ?? '--'}</b>
                <span>orbit</span>
                <b>{perTime?.orbit ?? '--'}</b>
                <span>frames</span>
                <b>{perTime?.n_frames ?? '--'}</b>
                <span>emission</span>
                <b>{fmt(perTime?.bore_emission, 1)}</b>
                <span>instrument</span>
                <b data-testid="stack-instrument">{instrument}</b>
                <span>band</span>
                <b>{composite ? 'RGB composite' : (band ?? meta?.band ?? '--')}</b>
                <span>km/px</span>
                <b>{fmt(meta?.km_per_px, 2)}</b>
                <span>x range (km)</span>
                <b>{meta ? `${fmt(meta.x_km[0], 0)} .. ${fmt(meta.x_km[1], 0)}` : '--'}</b>
                <span>y range (km)</span>
                <b>{meta ? `${fmt(meta.y_km[0], 0)} .. ${fmt(meta.y_km[1], 0)}` : '--'}</b>
              </div>
              <details>
                <summary>Full processing attributes</summary>
                <pre>{JSON.stringify(meta?.attrs, null, 2)}</pre>
              </details>
            </details>
            <div className={styles.card}>
              <label>
                <input
                  type="checkbox"
                  checked={showVectors}
                  onChange={(e) => setShowVectors(e.target.checked)}
                />
                Tracking vector overlay
              </label>
              {showVectors && (
                <>
                  <p>
                    {vectors
                      ? `${vectors.status}: ${vectors.reason ?? `${vectors.features.length} vectors with matching grid and time`}`
                      : 'Checking vector coverage…'}
                  </p>
                  {vectors && (
                    <details>
                      <summary>Vector provenance and basis</summary>
                      <pre>{JSON.stringify(vectors.provenance, null, 2)}</pre>
                    </details>
                  )}
                </>
              )}
            </div>
            <div className={styles.card} data-testid="stack-readiness">
              <h3>Analysis readiness</h3>
              {composite && (
                <label>
                  Physical band for analysis and movie{' '}
                  <select
                    data-testid="analysis-band"
                    value={analysisBand ?? ''}
                    onChange={(e) => setAnalysisBand(e.target.value || null)}
                  >
                    <option value="">Choose a physical band</option>
                    {stackBands.map((name) => (
                      <option key={name}>{name}</option>
                    ))}
                  </select>
                </label>
              )}
              {needsBand ? (
                <p>
                  RGB is a display composite. Select a physical band to assess or export this time
                  series.
                </p>
              ) : readinessError ? (
                <p role="alert">{readinessError}</p>
              ) : !readiness ? (
                <p role="status">Checking independent observations, cadence and common coverage…</p>
              ) : (
                <>
                  <p>
                    <b>{readiness.ready ? 'Ready for triple export' : 'No valid triple export'}</b>{' '}
                    · {readiness.n_observations} independent observations ·{' '}
                    {readiness.n_versions_removed} duplicate versions removed ·{' '}
                    {readiness.n_realizations} cadence runs
                  </p>
                  <p>{readiness.reasons.join(' ')}</p>
                  <p>
                    Band {readiness.band} · normalization {readiness.norm} · {readiness.units} ·{' '}
                    {readiness.km_per_px} km/px
                  </p>
                  <p>
                    Cadence gaps (s):{' '}
                    {readiness.gaps_s.map((g) => fmt(g, 3)).join(', ') ||
                      'No consecutive observations'}
                  </p>
                  {readiness.runs.map((run, i) => (
                    <p key={i}>
                      {run.n_frames} frames at {fmt(run.dt_s, 3)} s ·{' '}
                      {(run.common_valid_frac * 100).toFixed(1)}% common valid coverage
                    </p>
                  ))}
                  <p className={styles.muted}>
                    Format and cadence readiness does not validate wind accuracy. Check navigation
                    and registration in Compare.
                  </p>
                  <details>
                    <summary>Readiness provenance</summary>
                    <pre>{JSON.stringify(readiness.provenance, null, 2)}</pre>
                  </details>
                  <button
                    onClick={() =>
                      downloadText(
                        'stack_readiness.json',
                        JSON.stringify(readiness, null, 2),
                        'application/json',
                      )
                    }
                  >
                    Download readiness and sources
                  </button>
                </>
              )}
            </div>
            <div className={styles.card}>
              <h3>Movie and export</h3>
              {movieReady ||
              (listing?.has_movie && instrument === 'JIRAM' && normLabel === 'none') ? (
                <video
                  ref={videoRef}
                  data-testid="stack-movie"
                  controls
                  preload="metadata"
                  style={{ width: '100%', background: '#000' }}
                  src={`${api.movieUrl(stackId, physicalBand, normLabel)}${movieNonce ? `&v=${movieNonce}` : ''}`}
                />
              ) : (
                <p className={styles.muted}>No movie has been rendered for this stack yet.</p>
              )}
              <div className={styles.group} style={{ marginTop: 6 }}>
                <button
                  data-testid="render-movie"
                  onClick={renderMovie}
                  disabled={needsBand || nTimes === 0}
                >
                  Render movie
                </button>
                <button
                  data-testid="export-triples"
                  onClick={exportTriples}
                  disabled={needsBand || !readiness?.ready}
                  title={readiness?.reasons.join(' ') || 'Readiness required'}
                >
                  Export triples
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
