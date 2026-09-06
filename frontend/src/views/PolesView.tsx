/**
 * The Poles view: a stack chooser, an image player, and the movie.
 *
 * Three v1 failures are designed out here.  The colour map is a browser-side
 * LUT (see `ImageView`), so it always applies.  The movie is a native
 * `<video>` element pointing at the API's range-capable endpoint, so a
 * rendered movie is visible rather than merely written.  The player is an
 * `OrthographicView` with a scalar zoom, so panning and zooming cannot
 * distort the aspect ratio.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import styles from './Views.module.css';
import { ColorBar, ImageView } from '../components/ImageView';
import { api, fetchImage } from '../api/client';
import { useStore } from '../store/store';
import { fmt, fmtBytes } from '../lib/format';
import { COLOR_MAPS, type ColorMapName } from '../lib/lut';

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
  const showGraticule = useStore((s) => s.showGraticule);
  const setShowGraticule = useStore((s) => s.setShowGraticule);
  const emissionAlpha = useStore((s) => s.emissionAlpha);
  const setEmissionAlpha = useStore((s) => s.setEmissionAlpha);
  const frameImage = useStore((s) => s.frameImage);
  const emissionImage = useStore((s) => s.emissionImage);
  const pushToast = useStore((s) => s.pushToast);
  const watchJob = useStore((s) => s.watchJob);
  const selectionKeys = useStore((s) => s.selectionKeys);

  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [draftStretch, setDraftStretch] = useState<[number, number]>([vmin, vmax]);
  const [movieNonce, setMovieNonce] = useState(0);

  const listing = useMemo(() => stacks.find((stack) => stack.id === stackId) ?? null, [stacks, stackId]);
  const nTimes = meta?.times.length ?? 0;

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

  const renderMovie = useCallback(() => {
    if (!stackId) return;
    void api
      .renderMovie(stackId, { fps, pct: [1, 99], cmap })
      .then(({ job_id }) => {
        pushToast('info', `movie render started (job ${job_id})`);
        watchJob(job_id, (job) => {
          if (job.status === 'done') {
            pushToast('info', 'movie rendered');
            void useStore.getState().loadStacks();
            setMovieNonce((n) => n + 1);
            videoRef.current?.load();
          } else {
            pushToast('error', `movie render failed: ${job.message ?? ''}`);
          }
        });
      })
      .catch((error: Error) => pushToast('error', `movie: ${error.message}`));
  }, [stackId, fps, cmap, pushToast, watchJob]);

  const exportTriples = useCallback(() => {
    if (!stackId) return;
    void api
      .exportTriples(stackId, { crop_to_valid: true })
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
  }, [stackId, pushToast, watchJob]);

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
                {stack.id} - {stack.band} {stack.level}, {stack.n_time} steps
                {stack.has_movie ? ' [movie]' : ''}
              </option>
            ))}
          </select>
        </div>
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
            : 'select frames in the Catalog to build a new stack'}
        </span>
      </div>

      {!stackId ? (
        <div className={styles.card}>
          <p className={styles.muted}>
            No stack is open. Stacks are the NetCDF cubes under <code>&lt;mirror&gt;/regions/</code> that
            <code> jiram-catalog region-stack</code> writes; choose one above, or build one from the selection tray.
          </p>
        </div>
      ) : (
        <div className={styles.split}>
          <div className={styles.stack}>
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
              <label htmlFor="time-slider">
                time {t + 1}/{nTimes}
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
              <div className={styles.sep} />
              <label htmlFor="cmap-select">colour map</label>
              <select
                id="cmap-select"
                data-testid="cmap-select"
                value={cmap}
                onChange={(event) => setCmap(event.target.value as ColorMapName)}
              >
                {COLOR_MAPS.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <ColorBar cmap={cmap} vmin={vmin} vmax={vmax} />
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

            <div className={styles.toolbar}>
              <label htmlFor="vmin">vmin</label>
              <input
                id="vmin"
                data-testid="vmin"
                type="number"
                step="any"
                style={{ width: 110 }}
                value={draftStretch[0]}
                onChange={(event) => setDraftStretch([Number(event.target.value), draftStretch[1]])}
              />
              <label htmlFor="vmax">vmax</label>
              <input
                id="vmax"
                data-testid="vmax"
                type="number"
                step="any"
                style={{ width: 110 }}
                value={draftStretch[1]}
                onChange={(event) => setDraftStretch([draftStretch[0], Number(event.target.value)])}
              />
              <button
                onClick={() => meta && setStretch(meta.stretch.p1, meta.stretch.p99)}
                disabled={!meta}
              >
                reset stretch
              </button>
            </div>

            <ImageView
              image={frameImage}
              overlay={emissionImage}
              overlayAlpha={emissionAlpha}
              cmap={cmap}
              graticule={meta?.graticule ?? null}
              showGraticule={showGraticule}
              testId="poles-image"
              height={480}
              emptyMessage="loading the first frame..."
            />
          </div>

          <div className={styles.stack}>
            <div className={styles.card}>
              <h3>frame metadata</h3>
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
                <span>km/px</span>
                <b>{fmt(meta?.km_per_px, 2)}</b>
                <span>x range (km)</span>
                <b>{meta ? `${fmt(meta.x_km[0], 0)} .. ${fmt(meta.x_km[1], 0)}` : '--'}</b>
                <span>y range (km)</span>
                <b>{meta ? `${fmt(meta.y_km[0], 0)} .. ${fmt(meta.y_km[1], 0)}` : '--'}</b>
              </div>
            </div>

            <div className={styles.card}>
              <h3>movie</h3>
              {listing?.has_movie ? (
                <video
                  ref={videoRef}
                  data-testid="stack-movie"
                  controls
                  preload="metadata"
                  style={{ width: '100%', background: '#000' }}
                  src={`${api.movieUrl(stackId)}${movieNonce ? `?v=${movieNonce}` : ''}`}
                />
              ) : (
                <p className={styles.muted}>No movie has been rendered for this stack yet.</p>
              )}
              <div className={styles.group} style={{ marginTop: 6 }}>
                <button data-testid="render-movie" onClick={renderMovie}>
                  Render movie
                </button>
                <button data-testid="export-triples" onClick={exportTriples}>
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
