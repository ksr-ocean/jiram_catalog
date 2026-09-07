/**
 * The image viewer shared by Poles and Strips.
 *
 * The server sends one 8-bit grayscale PNG (value in R, validity in alpha).
 * The browser maps it through a 256-entry LUT into an offscreen canvas and
 * hands that canvas to a deck.gl `BitmapLayer`, so changing the colour map
 * costs one pass over pixels already in memory -- it cannot fail to arrive,
 * which is exactly how v1's colour-map control failed.  The canvas stays in
 * the DOM (`data-testid="frame-canvas"`, `data-loaded`) because it is also
 * the only honest way for a test to sample what the user is looking at
 * without depending on a GPU read-back.
 *
 * The view is an `OrthographicView` with a scalar zoom, so the aspect ratio
 * is locked by construction: v1's zoom-changes-the-aspect-ratio bug has no
 * expression here.
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import DeckGL from '@deck.gl/react';
import { OrthographicView, type OrthographicViewState, type PickingInfo } from '@deck.gl/core';
import { BitmapLayer, PathLayer } from '@deck.gl/layers';
import styles from './ImageView.module.css';
import type { GeoJsonCollection, ImagePayload } from '../api/types';
import { applyLut, lutColor, type ColorMapName } from '../lib/lut';

/** RGBA rows reversed, because the server serves row 0 at `ymin`. */
function flipRows(src: Uint8ClampedArray, width: number, height: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(src.length);
  const stride = width * 4;
  for (let row = 0; row < height; row++) {
    out.set(src.subarray((height - 1 - row) * stride, (height - row) * stride), row * stride);
  }
  return out;
}

function paths(collection: GeoJsonCollection | null | undefined): { path: [number, number][] }[] {
  if (!collection?.features) return [];
  return collection.features
    .filter((feature) => feature.geometry?.type === 'LineString')
    .map((feature) => ({ path: feature.geometry.coordinates as [number, number][] }));
}

export interface ImageViewProps {
  image: ImagePayload | null;
  overlay?: ImagePayload | null;
  overlayAlpha?: number;
  cmap: ColorMapName;
  graticule?: GeoJsonCollection | null;
  contours?: GeoJsonCollection | null;
  showGraticule?: boolean;
  /**
   * True when `image.gray` already holds colour (an RGB composite), so the
   * colour map must not be applied on top of it.
   */
  composite?: boolean;
  height?: number;
  /** Take the container's free space as well, never less than `height`. */
  grow?: boolean;
  testId?: string;
  unit?: string;
  emptyMessage?: string;
  camera?: OrthographicViewState | null;
  onCameraChange?: (camera: OrthographicViewState) => void;
  vectors?: { x_km: number; y_km: number; u: number; v: number }[];
  vectorSeconds?: number;
  maskOverlay?: boolean;
}

export function ImageView({
  image,
  overlay = null,
  overlayAlpha = 0,
  cmap,
  graticule = null,
  contours = null,
  showGraticule = true,
  composite = false,
  height = 460,
  grow = false,
  testId = 'image-view',
  unit = 'km',
  emptyMessage = 'nothing loaded',
  camera = null,
  onCameraChange,
  vectors = [],
  vectorSeconds = 3600,
  maskOverlay = false,
}: ImageViewProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const overlayCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const [bitmap, setBitmap] = useState<ImageBitmap | null>(null);
  const [overlayBitmap, setOverlayBitmap] = useState<ImageBitmap | null>(null);
  const [size, setSize] = useState<[number, number]>([800, height]);
  const [viewState, setViewState] = useState<OrthographicViewState>({ target: [0, 0, 0], zoom: 0 });
  const [readout, setReadout] = useState<string>('');
  const [gridDensity, setGridDensity] = useState('sparse');
  const cameraChange = useRef(onCameraChange);
  cameraChange.current = onCameraChange;
  const changeCamera = useCallback((next: OrthographicViewState) => {
    setViewState(next);
    cameraChange.current?.(next);
  }, []);

  useLayoutEffect(() => {
    const node = wrapRef.current;
    if (!node) return;
    const observer = new ResizeObserver(() =>
      setSize([node.clientWidth || 800, node.clientHeight || height]),
    );
    observer.observe(node);
    setSize([node.clientWidth || 800, node.clientHeight || height]);
    return () => observer.disconnect();
  }, [height]);

  // Colour-map the frame into the DOM canvas, then upload it as a bitmap.
  useEffect(() => {
    let cancelled = false;
    const canvas = canvasRef.current;
    if (!canvas || !image) {
      setBitmap(null);
      if (canvas) canvas.dataset.loaded = 'false';
      return;
    }
    canvas.width = image.width;
    canvas.height = image.height;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    if (!context) return;
    // A composite is already three bands of colour; a single band is one
    // number per pixel and the colour map is what makes it visible.
    const mapped = flipRows(
      composite ? image.gray : applyLut(image.gray, cmap),
      image.width,
      image.height,
    );
    const frame = context.createImageData(image.width, image.height);
    frame.data.set(mapped);
    context.putImageData(frame, 0, 0);
    canvas.dataset.loaded = 'true';
    canvas.dataset.cmap = composite ? 'rgb' : cmap;
    canvas.dataset.composite = composite ? 'true' : 'false';
    void createImageBitmap(canvas).then((next) => {
      if (cancelled) {
        next.close();
        return;
      }
      setBitmap((previous) => {
        previous?.close();
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [image, cmap, composite]);

  // The emission overlay is a second bitmap on the same bounds.
  useEffect(() => {
    let cancelled = false;
    const canvas = overlayCanvasRef.current;
    if (!canvas || !overlay || overlayAlpha <= 0) {
      setOverlayBitmap(null);
      return;
    }
    canvas.width = overlay.width;
    canvas.height = overlay.height;
    const context = canvas.getContext('2d');
    if (!context) return;
    const mapped = flipRows(
      maskOverlay ? overlay.gray : applyLut(overlay.gray, 'inferno'),
      overlay.width,
      overlay.height,
    );
    const frame = context.createImageData(overlay.width, overlay.height);
    frame.data.set(mapped);
    context.putImageData(frame, 0, 0);
    void createImageBitmap(canvas).then((next) => {
      if (cancelled) {
        next.close();
        return;
      }
      setOverlayBitmap((previous) => {
        previous?.close();
        return next;
      });
    });
    return () => {
      cancelled = true;
    };
  }, [overlay, overlayAlpha, maskOverlay]);

  // Fit the image to the container whenever a different extent arrives.
  const boundsKey = image ? image.bounds.join(',') : '';
  useEffect(() => {
    if (!image) return;
    const [xMin, xMax, yMin, yMax] = image.bounds;
    const spanX = Math.abs(xMax - xMin) || 1;
    const spanY = Math.abs(yMax - yMin) || 1;
    const zoom = Math.log2(Math.min(size[0] / spanX, size[1] / spanY));
    changeCamera({ target: [(xMin + xMax) / 2, (yMin + yMax) / 2, 0], zoom });
  }, [boundsKey, size, changeCamera]);

  const onHover = useCallback(
    (info: PickingInfo) => {
      if (!info.coordinate) {
        setReadout('');
        return;
      }
      const [x, y] = info.coordinate;
      setReadout(`x ${x.toFixed(1)} ${unit}   y ${y.toFixed(1)} ${unit}`);
    },
    [unit],
  );

  const layers = useMemo(() => {
    if (!image) return [];
    const [xMin, xMax, yMin, yMax] = image.bounds;
    const bounds: [number, number, number, number] = [xMin, yMin, xMax, yMax];
    const list: unknown[] = [];
    if (bitmap) {
      list.push(
        new BitmapLayer({
          id: `${testId}-bitmap`,
          image: bitmap,
          bounds,
          pickable: false,
          textureParameters: { minFilter: 'nearest', magFilter: 'nearest' },
        }),
      );
    }
    if (overlayBitmap && overlayAlpha > 0) {
      list.push(
        new BitmapLayer({
          id: `${testId}-emission`,
          image: overlayBitmap,
          bounds,
          opacity: overlayAlpha,
          pickable: false,
          textureParameters: { minFilter: 'nearest', magFilter: 'nearest' },
        }),
      );
    }
    if (showGraticule) {
      const graticulePaths = paths(graticule).filter(
        (_, i) => gridDensity === 'dense' || i % 3 === 0,
      );
      if (graticulePaths.length > 0) {
        list.push(
          new PathLayer({
            id: `${testId}-graticule`,
            data: graticulePaths,
            getPath: (d: { path: [number, number][] }) => d.path,
            getColor: [120, 190, 255, 150],
            getWidth: 1,
            widthUnits: 'pixels',
            widthMinPixels: 1,
          }),
        );
      }
      const contourPaths = paths(contours);
      if (contourPaths.length > 0) {
        list.push(
          new PathLayer({
            id: `${testId}-contours`,
            data: contourPaths,
            getPath: (d: { path: [number, number][] }) => d.path,
            getColor: [250, 190, 110, 170],
            getWidth: 1,
            widthUnits: 'pixels',
            widthMinPixels: 1,
          }),
        );
      }
    }
    if (vectors.length) {
      const arrows = vectors.map((v) => {
        const dx = (v.u * vectorSeconds) / 1000,
          dy = (v.v * vectorSeconds) / 1000,
          x = v.x_km + dx,
          y = v.y_km + dy;
        return {
          path: [
            [v.x_km, v.y_km],
            [x, y],
            [x - dx * 0.22 - dy * 0.12, y - dy * 0.22 + dx * 0.12],
            [x, y],
            [x - dx * 0.22 + dy * 0.12, y - dy * 0.22 - dx * 0.12],
          ],
        };
      });
      list.push(
        new PathLayer({
          id: `${testId}-vectors`,
          data: arrows,
          getPath: (d: { path: number[][] }) => d.path as [number, number][],
          getColor: [255, 219, 120, 240],
          getWidth: 1.5,
          widthUnits: 'pixels',
        }),
      );
    }
    return list;
  }, [
    image,
    bitmap,
    overlayBitmap,
    overlayAlpha,
    showGraticule,
    graticule,
    gridDensity,
    contours,
    testId,
    vectors,
    vectorSeconds,
  ]);

  return (
    <div
      className={styles.wrap}
      style={{ height, minHeight: Math.max(300, height), flex: grow ? '1 0 auto' : '0 0 auto' }}
      ref={wrapRef}
      data-testid={testId}
    >
      {image ? (
        <DeckGL
          style={{ position: 'absolute', inset: '0' }}
          views={new OrthographicView({ id: `${testId}-ortho`, flipY: false })}
          viewState={camera ?? viewState}
          onViewStateChange={({ viewState: next }) => changeCamera(next as OrthographicViewState)}
          controller={{ dragRotate: false }}
          layers={layers as never}
          onHover={onHover}
          width={size[0]}
          height={size[1]}
        />
      ) : (
        <div style={{ padding: 12, color: 'var(--muted)' }}>{emptyMessage}</div>
      )}
      {image && (
        <div className={styles.tools}>
          <button
            onClick={() => {
              const [a, b, c, d] = image.bounds;
              changeCamera({
                target: [(a + b) / 2, (c + d) / 2, 0],
                zoom: Math.log2(Math.min(size[0] / (b - a), size[1] / (d - c))),
              });
            }}
          >
            Fit map
          </button>
          <button
            onClick={() => {
              let x0 = image.width,
                y0 = image.height,
                x1 = -1,
                y1 = -1;
              for (let y = 0; y < image.height; y++)
                for (let x = 0; x < image.width; x++)
                  if (image.gray[(y * image.width + x) * 4 + 3] > 0) {
                    x0 = Math.min(x0, x);
                    x1 = Math.max(x1, x);
                    y0 = Math.min(y0, y);
                    y1 = Math.max(y1, y);
                  }
              if (x1 < 0) return;
              const [a, b, c, d] = image.bounds,
                dx = (b - a) / image.width,
                dy = (d - c) / image.height;
              changeCamera({
                target: [a + ((x0 + x1 + 1) / 2) * dx, c + ((y0 + y1 + 1) / 2) * dy, 0],
                zoom:
                  Math.log2(
                    Math.min(size[0] / ((x1 - x0 + 1) * dx), size[1] / ((y1 - y0 + 1) * dy)),
                  ) - 0.08,
              });
            }}
          >
            Fit valid data
          </button>
          {showGraticule && (
            <label>
              Grid{' '}
              <select
                aria-label="Graticule density"
                value={gridDensity}
                onChange={(e) => setGridDensity(e.target.value)}
              >
                <option value="sparse">Sparse</option>
                <option value="dense">Dense</option>
              </select>
            </label>
          )}
        </div>
      )}
      {showGraticule && image && (
        <div className={styles.coordinates}>
          Planetocentric latitude · east longitude · x/y in {unit}
        </div>
      )}
      {vectors.length > 0 && (
        <div className={styles.vectorLegend}>
          Arrows: m s⁻¹ · 100 m s⁻¹ spans {vectorSeconds / 10} km
        </div>
      )}
      {readout && <div className={styles.readout}>{readout}</div>}
      {/* Kept in the DOM on purpose: this is the colour-mapped image itself. */}
      <canvas
        ref={canvasRef}
        data-testid="frame-canvas"
        data-loaded="false"
        className={styles.hidden}
      />
      <canvas ref={overlayCanvasRef} className={styles.hidden} />
    </div>
  );
}

/** A small horizontal colour bar for the current map and stretch. */
export function ColorBar({ cmap, vmin, vmax }: { cmap: ColorMapName; vmin: number; vmax: number }) {
  const gradient = useMemo(
    () =>
      `linear-gradient(to right, ${Array.from({ length: 9 }, (_, i) => lutColor(cmap, (i / 8) * 255)).join(', ')})`,
    [cmap],
  );
  return (
    <span className={styles.colorbar}>
      <span>{vmin.toPrecision(3)}</span>
      <span
        className={styles.bar}
        style={{ background: gradient }}
        aria-label={`${cmap} colour bar`}
      />
      <span>{vmax.toPrecision(3)}</span>
    </span>
  );
}
