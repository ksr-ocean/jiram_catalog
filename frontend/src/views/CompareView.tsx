import { useEffect, useMemo, useRef, useState } from 'react';
import type { OrthographicViewState } from '@deck.gl/core';
import { api, fetchImage } from '../api/client';
import { researchApi, type Scene, type Comparison } from '../api/research';
import type { ImagePayload, StackMeta, StripMeta } from '../api/types';
import { ImageView } from '../components/ImageView';
import { useStore } from '../store/store';
import { stretchFor, uniqueBands } from '../lib/bands';
import { humanRegion } from '../lib/labels';
import { downloadText } from '../lib/csv';
import { fmt } from '../lib/format';
import styles from './Views.module.css';
const initial: Scene = { kind: 'stack', id: '', t: 0, band: null, norm: 'none' };
type Loaded = { image: ImagePayload; meta: StackMeta | StripMeta; scene: Scene };
export function commonMask(left: ImagePayload, right: ImagePayload): ImagePayload | null {
  if (
    left.width !== right.width ||
    left.height !== right.height ||
    left.bounds.some((v, i) => v !== right.bounds[i])
  )
    return null;
  const gray = new Uint8ClampedArray(left.gray.length);
  for (let i = 0; i < gray.length; i += 4) {
    gray[i] = 80;
    gray[i + 1] = 220;
    gray[i + 2] = 210;
    gray[i + 3] = left.gray[i + 3] > 0 && right.gray[i + 3] > 0 ? 110 : 0;
  }
  return { ...left, gray };
}
export function CompareView({ active }: { active: boolean }) {
  const stacks = useStore((s) => s.stacks),
    strips = useStore((s) => s.strips),
    currentStack = useStore((s) => s.stackId),
    currentStrip = useStore((s) => s.stripId);
  const [left, setLeft] = useState<Scene>(initial),
    [right, setRight] = useState<Scene>(initial);
  const initialized = useRef(false);
  const [leftData, setLeftData] = useState<Loaded | null>(null),
    [rightData, setRightData] = useState<Loaded | null>(null),
    [result, setResult] = useState<Comparison | null>(null);
  const [mode, setMode] = useState('split'),
    [blink, setBlink] = useState(false),
    [locked, setLocked] = useState(true),
    [mask, setMask] = useState(false),
    [speed, setSpeed] = useState(30),
    [navigation, setNavigation] = useState('');
  const [camera, setCamera] = useState<OrthographicViewState | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!active || initialized.current) return;
    if (!currentStack && currentStrip) {
      initialized.current = true;
      setLeft({ ...initial, kind: 'strip', id: currentStrip });
      setRight({ ...initial, kind: 'strip', id: currentStrip });
      return;
    }
    const id = currentStack ?? stacks[0]?.id;
    if (id) {
      initialized.current = true;
      setLeft({ ...initial, id });
      const count = stacks.find((stack) => stack.id === id)?.n_time ?? 1;
      setRight({ ...initial, id, t: count > 1 ? 1 : 0 });
    }
  }, [active, currentStack, currentStrip, stacks]);
  useEffect(() => {
    if (!active || mode !== 'blink') return;
    const timer = setInterval(() => setBlink((v) => !v), 700);
    return () => clearInterval(timer);
  }, [active, mode]);
  useEffect(() => {
    if (!active || !left.id || !right.id) return;
    let current = true;
    setBusy(true);
    setError('');
    setResult(null);
    const load = async () => {
      const meta = await Promise.all(
        [left, right].map((scene) =>
          scene.kind === 'stack' ? api.stackMeta(scene.id) : api.stripMeta(scene.id),
        ),
      );
      const normalized = [left, right].map((scene, i) => {
        const bands = uniqueBands(meta[i].bands);
        const item = meta[i];
        const nativeBand =
          'band' in item
            ? String(item.band)
            : typeof item.attrs?.band === 'string'
              ? item.attrs.band
              : null;
        const selected =
          scene.band ?? (bands.length === 1 ? bands[0] : bands.length > 1 ? null : nativeBand);
        if (bands.length > 1 && !selected)
          throw Error(
            'Choose an explicit physical band for each multi-band image. RGB is a display composite.',
          );
        return { ...scene, band: selected };
      });
      const ranges = normalized.map((scene, i) =>
        stretchFor(meta[i].stretch, scene.norm, scene.band),
      );
      if (locked) {
        if (
          normalized[0].band !== normalized[1].band ||
          normalized[0].norm !== normalized[1].norm ||
          meta[0].instrument !== meta[1].instrument
        )
          throw Error(
            'Locked stretches require the same instrument, band and normalization. Choose independent stretches for cross-instrument context.',
          );
        const lo = Math.min(ranges[0].p1, ranges[1].p1),
          hi = Math.max(ranges[0].p99, ranges[1].p99);
        ranges[0] = { p1: lo, p99: hi };
        ranges[1] = { p1: lo, p99: hi };
      }
      const images = await Promise.all(
        normalized.map((scene, i) =>
          fetchImage(
            scene.kind === 'stack'
              ? api.stackFrameUrl(
                  scene.id,
                  scene.t,
                  ranges[i].p1,
                  ranges[i].p99,
                  1000,
                  scene.band,
                  scene.norm,
                )
              : api.stripImageUrl(
                  scene.id,
                  ranges[i].p1,
                  ranges[i].p99,
                  1000,
                  scene.band,
                  scene.norm,
                ),
          ),
        ),
      );
      if (!current) return;
      setLeftData({ image: images[0], meta: meta[0], scene: normalized[0] });
      setRightData({ image: images[1], meta: meta[1], scene: normalized[1] });
      const data = await researchApi.compare(
        normalized[0],
        normalized[1],
        speed,
        navigation.trim() === '' ? null : Number(navigation),
      );
      if (current) setResult(data);
    };
    const timer = setTimeout(() => {
      void load()
        .catch((e) => {
          if (current) {
            setError(e.message);
            setLeftData(null);
            setRightData(null);
          }
        })
        .finally(() => {
          if (current) setBusy(false);
        });
    }, 250);
    return () => {
      current = false;
      clearTimeout(timer);
    };
  }, [active, left, right, locked, speed, navigation]);
  const overlay = useMemo(
    () =>
      leftData && rightData && result?.compatible
        ? commonMask(leftData.image, rightData.image)
        : null,
    [leftData, rightData, result],
  );
  const chooser = (scene: Scene, set: (scene: Scene) => void, title: string) => {
    const listing = stacks.find((s) => s.id === scene.id),
      strip = strips.find((s) => s.strip_id === scene.id),
      bands = uniqueBands(scene.kind === 'stack' ? listing?.bands : strip?.bands);
    return (
      <div className={styles.card}>
        <h3>{title}</h3>
        <div className={styles.toolbar}>
          <label>
            Source{' '}
            <select
              aria-label={`${title} source type`}
              value={scene.kind}
              onChange={(e) => {
                initialized.current = true;
                set({ ...initial, kind: e.target.value as Scene['kind'] });
              }}
            >
              <option value="stack">Time series frame</option>
              <option value="strip">Library image</option>
            </select>
          </label>
          <label>
            Image{' '}
            <select
              aria-label={`${title} source`}
              value={scene.id}
              onChange={(e) => {
                initialized.current = true;
                set({ ...scene, id: e.target.value, t: 0, band: null });
              }}
            >
              <option value="">Choose image</option>
              {scene.kind === 'stack'
                ? stacks.map((s) => (
                    <option key={s.id} value={s.id}>
                      {humanRegion(s.region)} · {s.instrument ?? 'JIRAM'} · {s.n_time} steps
                    </option>
                  ))
                : strips.map((s) => (
                    <option key={s.strip_id} value={s.strip_id}>
                      {s.instrument} PJ{s.orbit} · {s.strip_id}
                    </option>
                  ))}
            </select>
          </label>
          {scene.kind === 'stack' && (
            <label>
              Frame{' '}
              <input
                aria-label={`${title} frame`}
                type="number"
                min="1"
                max={listing?.n_time ?? 1}
                style={{ width: 70 }}
                value={scene.t + 1}
                onChange={(e) =>
                  set({
                    ...scene,
                    t: Math.max(
                      0,
                      Math.min((listing?.n_time ?? 1) - 1, Number(e.target.value) - 1),
                    ),
                  })
                }
              />
            </label>
          )}
          {bands.length > 0 && (
            <label>
              Analysis band{' '}
              <select
                aria-label={`${title} band`}
                value={scene.band ?? ''}
                onChange={(e) => set({ ...scene, band: e.target.value || null })}
              >
                <option value="">Choose physical band</option>
                {bands.map((b) => (
                  <option key={b}>{b}</option>
                ))}
              </select>
            </label>
          )}
          <label>
            Normalization{' '}
            <select
              aria-label={`${title} normalization`}
              value={scene.norm}
              onChange={(e) => set({ ...scene, norm: e.target.value })}
            >
              <option value="none">Native</option>
              <option value="lambert">Lambert</option>
              <option value="minnaert:0.5">Minnaert k=0.5</option>
              <option value="flat:64">Flatten σ=64 px</option>
            </select>
          </label>
        </div>
      </div>
    );
  };
  return (
    <div className={styles.view} data-testid="compare-view">
      <div className={styles.heading}>
        <h2>Compare observations</h2>
        <p>
          Inspect structure and registration with linked pan and zoom. Registration measures image
          alignment; predicted displacement uses your assumed speed.
        </p>
      </div>
      <div className={styles.compareGrid}>
        {chooser(left, setLeft, 'Left')}
        {chooser(right, setRight, 'Right')}
      </div>
      <div className={styles.toolbar}>
        <label>
          Display{' '}
          <select value={mode} onChange={(e) => setMode(e.target.value)}>
            <option value="split">Side by side</option>
            <option value="blink">Blink</option>
          </select>
        </label>
        <label>
          <input type="checkbox" checked={locked} onChange={(e) => setLocked(e.target.checked)} />
          Lock stretches
        </label>
        <label>
          <input
            type="checkbox"
            checked={mask}
            disabled={!overlay}
            onChange={(e) => setMask(e.target.checked)}
          />
          Common valid mask
        </label>
        <label>
          Assumed speed (m/s){' '}
          <input
            type="number"
            min="0"
            style={{ width: 85 }}
            value={speed}
            onChange={(e) => setSpeed(Number(e.target.value))}
          />
        </label>
        <label>
          Navigation error (px){' '}
          <input
            type="number"
            min="0"
            step="0.1"
            placeholder="Unknown"
            style={{ width: 90 }}
            value={navigation}
            onChange={(e) => setNavigation(e.target.value)}
          />
        </label>
      </div>
      {busy && <p role="status">Loading images and measuring registration…</p>}
      {error && (
        <p role="alert" className={styles.notice}>
          {error}
        </p>
      )}
      {leftData && rightData && (
        <div className={mode === 'split' ? styles.compareGrid : styles.stack}>
          {(mode === 'split' ? [leftData, rightData] : [blink ? rightData : leftData]).map(
            (data, i) => (
              <div key={mode === 'split' ? i : 'blink'}>
                <p>
                  {mode === 'split' ? (i ? 'Right' : 'Left') : blink ? 'Right' : 'Left'} ·{' '}
                  {data.scene.band ?? 'native band'} · {data.scene.norm} ·{' '}
                  {'times' in data.meta
                    ? data.meta.times[data.scene.t]
                    : String(
                        data.meta.attrs?.time_mid ??
                          data.meta.attrs?.time_start ??
                          'Time in provenance',
                      )}
                </p>
                <ImageView
                  image={data.image}
                  cmap="gray"
                  graticule={data.meta.graticule}
                  height={420}
                  camera={camera}
                  onCameraChange={setCamera}
                  overlay={mask ? overlay : null}
                  overlayAlpha={mask ? 0.5 : 0}
                  maskOverlay
                  testId={`compare-image-${i}`}
                />
              </div>
            ),
          )}
        </div>
      )}
      {result && (
        <div className={styles.card} data-testid="compare-diagnostics">
          <h3>
            {result.compatible
              ? 'Compatible comparison grid'
              : 'Grids are incompatible for quantitative registration'}
          </h3>
          <p>{result.reasons.join(' ')}</p>
          <div className={styles.kv}>
            <span>Time separation</span>
            <b>{fmt(result.dt_s, 3)} s</b>
            <span>Common valid coverage</span>
            <b>
              {result.common_valid_frac === null
                ? 'Unassessed'
                : `${(result.common_valid_frac * 100).toFixed(1)}%`}
            </b>
            <span>Registration (x, y)</span>
            <b>
              {fmt(result.registration.dx_px, 3)}, {fmt(result.registration.dy_px, 3)} px ·{' '}
              {result.registration.status}
            </b>
            <span>Native map resolution (left, right)</span>
            <b>
              {fmt(result.left.km_per_px as number, 2)}, {fmt(result.right.km_per_px as number, 2)}{' '}
              km/px
            </b>
            <span>Registration sampling</span>
            <b>
              {fmt(result.registration.sample_km_per_px, 2)} km/px · stride{' '}
              {result.registration.sample_stride ?? 'unassessed'}
            </b>
            <span>Correlation</span>
            <b>{fmt(result.registration.correlation, 3)}</b>
            <span>Expected displacement at assumed speed</span>
            <b>{fmt(result.predicted_displacement_px, 3)} px</b>
            <span>Navigation contribution to velocity uncertainty</span>
            <b>
              {result.velocity_uncertainty_m_s === null
                ? 'Unknown navigation accuracy'
                : `${fmt(result.velocity_uncertainty_m_s, 2)} m/s`}
            </b>
          </div>
          <p className={styles.muted}>
            The common-mask overlay uses served same-grid alpha masks. Sampling and navigation
            limits are recorded below.
          </p>
          <details>
            <summary>Sampling, registration and provenance</summary>
            <pre>{JSON.stringify(result, null, 2)}</pre>
          </details>
          <button
            onClick={() =>
              downloadText(
                'comparison_recipe.json',
                JSON.stringify(
                  {
                    left,
                    right,
                    speed_m_s: speed,
                    navigation_error_px: navigation || null,
                    locked_stretch: locked,
                    result,
                  },
                  null,
                  2,
                ),
                'application/json',
              )
            }
          >
            Download comparison and recipe
          </button>
        </div>
      )}
    </div>
  );
}
