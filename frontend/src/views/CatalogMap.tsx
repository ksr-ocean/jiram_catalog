/**
 * The catalog map: every band-half that sees the planet, as GPU points.
 *
 * v1 rasterised the points with datashader, so hovering a point did nothing
 * once the set was large -- there were no points on the client to hover.
 * Here the positions are a `Float32Array` uploaded once per view change and
 * deck.gl picks them on the GPU, so the tooltip works at 47,000 points.  A
 * short CPU nearest-point search backs the GPU pick up (and reports which
 * one answered in `data-source`), because a one-pixel dot is a hard target
 * for a mouse and an impossible one for a headless test.
 *
 * Box and lasso are done as a polygon test over the same typed arrays: the
 * screen polygon is unprojected once and the test is one pass over the
 * filtered indices.
 *
 * JunoCam rows are not points.  One JunoCam image is a swath tens of degrees
 * across, so it is drawn as its outline -- a `PolygonLayer` under the points,
 * stroked and filled at low alpha, cut at the 0/360 seam by
 * `src/lib/footprints.ts` -- and the outlines are pickable on the same terms
 * as the points, by the GPU when the pan tool is out and by a bounding-box
 * plus point-in-polygon test when the selection overlay is covering the canvas.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import DeckGL from '@deck.gl/react';
import { OrthographicView, type OrthographicViewState, type PickingInfo } from '@deck.gl/core';
import { PathLayer, PolygonLayer, ScatterplotLayer } from '@deck.gl/layers';
import styles from './Views.module.css';
import { useStore, type ColorBy } from '../store/store';
import { categoricalColor, rampColor, rgbCss, type RGB } from '../lib/colorScale';
import { graticule, viewLimits } from '../lib/projection';
import { boxPolygon, indicesInPolygon, pointInPolygon } from '../lib/filters';
import { fmt, fmtTime } from '../lib/format';
import { INSTRUMENTS } from '../lib/bands';
import type { FootprintItem } from '../lib/footprints';

type Tool = 'pan' | 'box' | 'lasso';

/** Screen pixels per world unit at a given orthographic zoom. */
function scaleOf(zoom: number): number {
  return 2 ** zoom;
}

function unproject(
  sx: number,
  sy: number,
  viewState: OrthographicViewState,
  width: number,
  height: number,
): [number, number] {
  const scale = scaleOf(viewState.zoom as number);
  const [tx, ty] = viewState.target as number[];
  return [tx + (sx - width / 2) / scale, ty - (sy - height / 2) / scale];
}

function projectToScreen(
  wx: number,
  wy: number,
  viewState: OrthographicViewState,
  width: number,
  height: number,
): [number, number] {
  const scale = scaleOf(viewState.zoom as number);
  const [tx, ty] = viewState.target as number[];
  return [width / 2 + (wx - tx) * scale, height / 2 - (wy - ty) * scale];
}

function colorRange(values: Float32Array, indices: Uint32Array): [number, number] {
  let low = Infinity;
  let high = -Infinity;
  for (let k = 0; k < indices.length; k++) {
    const value = values[indices[k]];
    if (!Number.isFinite(value)) continue;
    if (value < low) low = value;
    if (value > high) high = value;
  }
  return Number.isFinite(low) ? [low, high] : [0, 1];
}

export function CatalogMap() {
  const columns = useStore((s) => s.columns);
  const filtered = useStore((s) => s.filtered);
  const positions = useStore((s) => s.positions);
  const footprints = useStore((s) => s.footprints);
  const viewMode = useStore((s) => s.viewMode);
  const colorBy = useStore((s) => s.colorBy);
  const selectionKeys = useStore((s) => s.selectionKeys);
  const addToSelection = useStore((s) => s.addToSelection);
  const replaceSelection = useStore((s) => s.replaceSelection);
  const openDetail = useStore((s) => s.openDetail);

  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState<[number, number]>([900, 420]);
  const [viewState, setViewState] = useState<OrthographicViewState>({ target: [180, 0, 0], zoom: 1 });
  const [tool, setTool] = useState<Tool>('box');
  const [replaceMode, setReplaceMode] = useState(false);
  const [drag, setDrag] = useState<[number, number][] | null>(null);
  const [hover, setHover] = useState<{ index: number; x: number; y: number; source: 'gpu' | 'cpu' } | null>(null);

  useEffect(() => {
    const node = wrapRef.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setSize([node.clientWidth || 900, node.clientHeight || 420]));
    observer.observe(node);
    setSize([node.clientWidth || 900, node.clientHeight || 420]);
    return () => observer.disconnect();
  }, []);

  const fit = useCallback(
    (bounds?: [number, number, number, number]) => {
      const [xMin, xMax, yMin, yMax] = bounds ?? viewLimits(viewMode);
      const spanX = Math.abs(xMax - xMin) || 1;
      const spanY = Math.abs(yMax - yMin) || 1;
      const zoom = Math.log2(Math.min(size[0] / spanX, size[1] / spanY));
      setViewState({ target: [(xMin + xMax) / 2, (yMin + yMax) / 2, 0], zoom });
    },
    [size, viewMode],
  );

  useEffect(() => {
    fit();
  }, [fit]);

  /** Fit to whatever passes the filters -- the "zoom to data" button. */
  const fitToFiltered = useCallback(() => {
    let xMin = Infinity;
    let xMax = -Infinity;
    let yMin = Infinity;
    let yMax = -Infinity;
    for (let k = 0; k < filtered.length; k++) {
      const i = filtered[k];
      const x = positions[2 * i];
      const y = positions[2 * i + 1];
      if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
      if (x < xMin) xMin = x;
      if (x > xMax) xMax = x;
      if (y < yMin) yMin = y;
      if (y > yMax) yMax = y;
    }
    if (!Number.isFinite(xMin)) return;
    const padX = (xMax - xMin) * 0.02 + 0.5;
    const padY = (yMax - yMin) * 0.02 + 0.5;
    fit([xMin - padX, xMax + padX, yMin - padY, yMax + padY]);
  }, [filtered, positions, fit]);

  const colorValues = useMemo(() => {
    switch (colorBy) {
      case 'year':
        return Float32Array.from(columns.year);
      case 'pixel':
        return columns.medianPixelKm;
      case 'emission':
        return columns.boreEmission;
      case 'instrument': {
        // The instrument is a name, so it is coloured by its position in the
        // contract's list rather than by a number the row happens to carry.
        const values = new Float32Array(columns.n);
        for (let i = 0; i < columns.n; i++) {
          const at = INSTRUMENTS.indexOf(columns.instrument[i] as (typeof INSTRUMENTS)[number]);
          values[i] = at < 0 ? INSTRUMENTS.length : at;
        }
        return values;
      }
      default:
        return columns.orbit;
    }
  }, [colorBy, columns]);

  const categorical = colorBy === 'orbit' || colorBy === 'year' || colorBy === 'instrument';
  const range = useMemo(() => colorRange(colorValues, filtered), [colorValues, filtered]);
  const colorOf = useCallback(
    (row: number): RGB =>
      categorical ? categoricalColor(colorValues[row]) : rampColor(colorValues[row], range[0], range[1]),
    [categorical, colorValues, range],
  );

  /** One packed buffer per (filter, view, colour) change; deck uploads it once. */
  const binary = useMemo(() => {
    const n = filtered.length;
    const xy = new Float32Array(n * 2);
    const rgb = new Uint8Array(n * 3);
    for (let k = 0; k < n; k++) {
      const i = filtered[k];
      xy[2 * k] = positions[2 * i];
      xy[2 * k + 1] = positions[2 * i + 1];
      const value = colorValues[i];
      const color: RGB = categorical ? categoricalColor(value) : rampColor(value, range[0], range[1]);
      rgb[3 * k] = color[0];
      rgb[3 * k + 1] = color[1];
      rgb[3 * k + 2] = color[2];
    }
    return { length: n, attributes: { getPosition: { value: xy, size: 2 }, getFillColor: { value: rgb, size: 3 } } };
  }, [filtered, positions, colorValues, categorical, range]);

  /** The selected subset drawn on top, so the tray's contents are visible. */
  const selectedXy = useMemo(() => {
    const keyIndex = useStore.getState().keyIndex;
    const xs: number[] = [];
    for (const key of selectionKeys) {
      const i = keyIndex.get(key);
      if (i === undefined) continue;
      const x = positions[2 * i];
      const y = positions[2 * i + 1];
      if (Number.isFinite(x) && Number.isFinite(y)) xs.push(x, y);
    }
    return new Float32Array(xs);
  }, [selectionKeys, positions]);

  /** Nearest filtered point to a screen position, within `maxPixels`. */
  const nearest = useCallback(
    (sx: number, sy: number, maxPixels = 14): number | null => {
      const scale = scaleOf(viewState.zoom as number);
      const [wx, wy] = unproject(sx, sy, viewState, size[0], size[1]);
      const tolerance = maxPixels / scale;
      let best = -1;
      let bestDistance = tolerance * tolerance;
      for (let k = 0; k < filtered.length; k++) {
        const i = filtered[k];
        const dx = positions[2 * i] - wx;
        const dy = positions[2 * i + 1] - wy;
        if (!Number.isFinite(dx) || !Number.isFinite(dy)) continue;
        const distance = dx * dx + dy * dy;
        if (distance < bestDistance) {
          bestDistance = distance;
          best = i;
        }
      }
      return best >= 0 ? best : null;
    },
    [filtered, positions, viewState, size],
  );

  /**
   * The footprint under a screen position.
   *
   * A bounding-box rejection first, because a point-in-polygon test over
   * thousands of 64-vertex outlines on every mouse move is not free; the
   * outlines are already in view coordinates, so the mouse is unprojected
   * once rather than the outlines projected many times.
   */
  const footprintAt = useCallback(
    (sx: number, sy: number): FootprintItem | null => {
      const [wx, wy] = unproject(sx, sy, viewState, size[0], size[1]);
      for (const item of footprints.items) {
        const [xMin, xMax, yMin, yMax] = item.bounds;
        if (wx < xMin || wx > xMax || wy < yMin || wy > yMax) continue;
        if (pointInPolygon(wx, wy, item.polygon)) return item;
      }
      return null;
    },
    [footprints, viewState, size],
  );

  const onDeckHover = useCallback(
    (info: PickingInfo) => {
      const sx = info.x ?? 0;
      const sy = info.y ?? 0;
      if (info.index >= 0 && info.layer?.id === 'catalog-points') {
        setHover({ index: filtered[info.index], x: sx, y: sy, source: 'gpu' });
        return;
      }
      if (info.index >= 0 && info.layer?.id === 'catalog-footprints') {
        const item = (info.object ?? null) as FootprintItem | null;
        if (item) {
          setHover({ index: item.row, x: sx, y: sy, source: 'gpu' });
          return;
        }
      }
      const fallback = nearest(sx, sy);
      if (fallback !== null) {
        setHover({ index: fallback, x: sx, y: sy, source: 'cpu' });
        return;
      }
      const item = footprintAt(sx, sy);
      setHover(item === null ? null : { index: item.row, x: sx, y: sy, source: 'cpu' });
    },
    [filtered, nearest, footprintAt],
  );

  const commitPolygon = useCallback(
    (screenPolygon: [number, number][]) => {
      const polygon = screenPolygon.map(([sx, sy]) => unproject(sx, sy, viewState, size[0], size[1]));
      const hits = indicesInPolygon(positions, filtered, polygon as [number, number][]);
      if (hits.length === 0) return;
      if (replaceMode) replaceSelection(Uint32Array.from(hits));
      else addToSelection(Uint32Array.from(hits));
    },
    [viewState, size, positions, filtered, replaceMode, addToSelection, replaceSelection],
  );

  const layers = useMemo(() => {
    const list: unknown[] = [
      new PathLayer({
        id: 'catalog-graticule',
        data: graticule(viewMode),
        getPath: (d: { path: [number, number][] }) => d.path,
        getColor: [70, 88, 112, 200],
        getWidth: 1,
        widthUnits: 'pixels',
        widthMinPixels: 1,
      }),
    ];
    if (footprints.items.length > 0) {
      // Below the points on purpose: a swath is large enough to swallow every
      // JIRAM boresight inside it, and the point is the more precise target.
      list.push(
        new PolygonLayer<FootprintItem>({
          id: 'catalog-footprints',
          data: footprints.items,
          getPolygon: (d) => d.polygon,
          pickable: true,
          filled: true,
          stroked: true,
          getFillColor: (d) => [...colorOf(d.row), 34] as [number, number, number, number],
          getLineColor: (d) => [...colorOf(d.row), 220] as [number, number, number, number],
          lineWidthUnits: 'pixels',
          getLineWidth: 1.2,
          lineWidthMinPixels: 1,
          onHover: onDeckHover,
          onClick: (info: PickingInfo<FootprintItem>) => {
            if (info.object) void openDetail(columns.productId[info.object.row]);
          },
          updateTriggers: { getFillColor: [colorBy, range], getLineColor: [colorBy, range] },
        }),
      );
    }
    list.push(
      new ScatterplotLayer({
        id: 'catalog-points',
        data: binary as never,
        pickable: true,
        radiusUnits: 'pixels',
        getRadius: 2.2,
        radiusMinPixels: 1.5,
        radiusMaxPixels: 6,
        opacity: 0.85,
        onHover: onDeckHover,
        onClick: (info: PickingInfo) => {
          if (info.index >= 0) void openDetail(columns.productId[filtered[info.index]]);
        },
        updateTriggers: { getPosition: binary, getFillColor: binary },
      }),
    );
    if (selectedXy.length > 0) {
      list.push(
        new ScatterplotLayer({
          id: 'catalog-selected',
          data: {
            length: selectedXy.length / 2,
            attributes: { getPosition: { value: selectedXy, size: 2 } },
          } as never,
          pickable: false,
          radiusUnits: 'pixels',
          getRadius: 3.6,
          radiusMinPixels: 2.5,
          filled: false,
          stroked: true,
          lineWidthUnits: 'pixels',
          getLineWidth: 1.2,
          getLineColor: [255, 255, 255, 230],
        }),
      );
    }
    if (drag && drag.length > 2) {
      const world = drag.map(([sx, sy]) => unproject(sx, sy, viewState, size[0], size[1]));
      list.push(
        new PolygonLayer({
          id: 'catalog-drag',
          data: [{ polygon: world }],
          getPolygon: (d: { polygon: number[][] }) => d.polygon,
          filled: true,
          getFillColor: [106, 176, 243, 40],
          stroked: true,
          getLineColor: [106, 176, 243, 220],
          getLineWidth: 1,
          lineWidthUnits: 'pixels',
        }),
      );
    }
    return list;
  }, [
    viewMode,
    binary,
    footprints,
    colorOf,
    colorBy,
    range,
    selectedXy,
    drag,
    viewState,
    size,
    onDeckHover,
    openDetail,
    columns,
    filtered,
  ]);

  const hoverIndex = hover?.index ?? null;

  return (
    <>
      <div className={styles.toolbar}>
        <div className={styles.group}>
          <span className={styles.muted}>tool</span>
          {(['pan', 'box', 'lasso'] as Tool[]).map((name) => (
            <button
              key={name}
              data-testid={`tool-${name}`}
              aria-pressed={tool === name}
              onClick={() => setTool(name)}
            >
              {name}
            </button>
          ))}
          <label>
            <input
              type="checkbox"
              data-testid="replace-mode"
              checked={replaceMode}
              onChange={(e) => setReplaceMode(e.target.checked)}
            />
            replace instead of add
          </label>
        </div>
        <div className={styles.sep} />
        <div className={styles.group}>
          <span className={styles.muted}>view</span>
          {(['cyl', 'N', 'S'] as const).map((mode) => (
            <button
              key={mode}
              data-testid={`view-mode-${mode}`}
              aria-pressed={viewMode === mode}
              onClick={() => useStore.getState().setViewMode(mode)}
            >
              {mode}
            </button>
          ))}
        </div>
        <div className={styles.group}>
          <label htmlFor="color-by">colour by</label>
          <select
            id="color-by"
            data-testid="color-by"
            value={colorBy}
            onChange={(e) => useStore.getState().setColorBy(e.target.value as ColorBy)}
          >
            <option value="orbit">orbit</option>
            <option value="year">year</option>
            <option value="pixel">pixel size (km)</option>
            <option value="emission">emission (deg)</option>
            <option value="instrument">instrument</option>
          </select>
        </div>
        <button data-testid="zoom-to-data" onClick={fitToFiltered}>
          zoom to data
        </button>
        <button data-testid="reset-view" onClick={() => fit()}>
          reset view
        </button>
        <span className={styles.muted} data-testid="point-count">
          {filtered.length.toLocaleString()} of {columns.n.toLocaleString()} drawn
          {footprints.n > 0 ? `, ${footprints.n.toLocaleString()} as footprints` : ''}
        </span>
      </div>

      <div className={styles.mapWrap} ref={wrapRef} data-testid="catalog-map">
        <DeckGL
          views={new OrthographicView({ id: 'catalog-ortho', flipY: false })}
          viewState={viewState}
          onViewStateChange={({ viewState: next }) => setViewState(next as OrthographicViewState)}
          controller={tool === 'pan' ? { dragRotate: false } : false}
          layers={layers as never}
          width={size[0]}
          height={size[1]}
          getCursor={() => (tool === 'pan' ? 'grab' : 'crosshair')}
          onHover={onDeckHover}
        />
        {tool !== 'pan' && (
          <div
            className={styles.overlay}
            data-testid="select-overlay"
            onPointerDown={(event) => {
              try {
                (event.target as HTMLElement).setPointerCapture(event.pointerId);
              } catch {
                /* synthetic pointers without capture support still work */
              }
              const rect = event.currentTarget.getBoundingClientRect();
              setDrag([[event.clientX - rect.left, event.clientY - rect.top]]);
            }}
            onPointerMove={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              const point: [number, number] = [event.clientX - rect.left, event.clientY - rect.top];
              if (!drag) {
                const index = nearest(point[0], point[1]);
                if (index !== null) {
                  setHover({ index, x: point[0], y: point[1], source: 'cpu' });
                  return;
                }
                // The overlay swallows deck.gl's own picking, so the outlines
                // are tested here too -- otherwise hovering a JunoCam swath
                // would do nothing unless the pan tool was chosen first.
                const item = footprintAt(point[0], point[1]);
                setHover(item === null ? null : { index: item.row, x: point[0], y: point[1], source: 'cpu' });
                return;
              }
              setDrag((previous) => {
                if (!previous) return previous;
                if (tool === 'box') return [previous[0], point];
                return [...previous, point];
              });
            }}
            onPointerUp={(event) => {
              const rect = event.currentTarget.getBoundingClientRect();
              const point: [number, number] = [event.clientX - rect.left, event.clientY - rect.top];
              const path = drag ? (tool === 'box' ? [drag[0], point] : [...drag, point]) : null;
              setDrag(null);
              if (!path) return;
              if (tool === 'box' && path.length === 2) {
                commitPolygon(boxPolygon(path[0][0], path[0][1], path[1][0], path[1][1]));
              } else if (path.length > 2) {
                commitPolygon(path);
              }
            }}
            onPointerLeave={() => setDrag(null)}
          />
        )}
        {drag && drag.length >= 2 && tool === 'box' && (
          <div
            style={{
              position: 'absolute',
              zIndex: 4,
              pointerEvents: 'none',
              border: '1px solid var(--accent)',
              background: '#6ab0f322',
              left: Math.min(drag[0][0], drag[1][0]),
              top: Math.min(drag[0][1], drag[1][1]),
              width: Math.abs(drag[1][0] - drag[0][0]),
              height: Math.abs(drag[1][1] - drag[0][1]),
            }}
          />
        )}
        {hoverIndex !== null && hover && (
          <div
            className={`${styles.tooltip} tooltip`}
            data-testid="catalog-tooltip"
            data-source={hover.source}
            style={{ left: Math.min(hover.x + 12, size[0] - 260), top: Math.min(hover.y + 12, size[1] - 96) }}
          >
            <div>
              <b data-testid="tooltip-product-id">{columns.productId[hoverIndex]}</b>{' '}
              ({columns.half[hoverIndex] || columns.bands[hoverIndex] || '--'})
            </div>
            <div data-testid="tooltip-instrument">
              {columns.instrument[hoverIndex] || 'JIRAM'}
              {columns.qualityTier[hoverIndex] ? ` · tier ${columns.qualityTier[hoverIndex]}` : ''}
            </div>
            <div>{fmtTime(columns.startTimeMs[hoverIndex])}</div>
            <div>
              orbit {columns.orbit[hoverIndex]} &middot; seq {columns.seqId[hoverIndex]}
            </div>
            <div>
              pixel {fmt(columns.medianPixelKm[hoverIndex], 1)} km &middot; emission{' '}
              {fmt(columns.boreEmission[hoverIndex], 1)} deg
            </div>
          </div>
        )}
        <div className={styles.legend} data-testid="catalog-legend">
          <div style={{ marginBottom: 3 }}>{colorBy}</div>
          {colorBy === 'instrument' ? (
            <>
              {INSTRUMENTS.map((name, index) => (
                <div className={styles.swatchRow} key={name}>
                  <span className={styles.swatch} style={{ background: rgbCss(categoricalColor(index)) }} />
                  <span>{name}</span>
                </div>
              ))}
            </>
          ) : colorBy === 'orbit' || colorBy === 'year' ? (
            <div className={styles.swatchRow}>
              {[0, 1, 2, 3, 4, 5].map((k) => (
                <span key={k} className={styles.swatch} style={{ background: rgbCss(categoricalColor(k)) }} />
              ))}
              <span>categorical</span>
            </div>
          ) : (
            <div className={styles.swatchRow}>
              <span>{fmt(range[0], 1)}</span>
              {[0, 0.25, 0.5, 0.75, 1].map((f) => (
                <span
                  key={f}
                  className={styles.swatch}
                  style={{ background: rgbCss(rampColor(range[0] + f * (range[1] - range[0]), range[0], range[1])) }}
                />
              ))}
              <span>{fmt(range[1], 1)}</span>
            </div>
          )}
        </div>
      </div>
    </>
  );
}

/** Screen-space helpers, exported for the unit tests. */
export const _internals = { unproject, projectToScreen, scaleOf };
