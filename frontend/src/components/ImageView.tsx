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
  height?: number;
  testId?: string;
  unit?: string;
  emptyMessage?: string;
}

export function ImageView({
  image,
  overlay = null,
  overlayAlpha = 0,
  cmap,
  graticule = null,
  contours = null,
  showGraticule = true,
  height = 460,
  testId = 'image-view',
  unit = 'km',
  emptyMessage = 'nothing loaded',
}: ImageViewProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const overlayCanvasRef = useRef<HTMLCanvasElement | null>(null);
  const [bitmap, setBitmap] = useState<ImageBitmap | null>(null);
  const [overlayBitmap, setOverlayBitmap] = useState<ImageBitmap | null>(null);
  const [size, setSize] = useState<[number, number]>([800, height]);
  const [viewState, setViewState] = useState<OrthographicViewState>({ target: [0, 0, 0], zoom: 0 });
  const [readout, setReadout] = useState<string>('');

  useLayoutEffect(() => {
    const node = wrapRef.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setSize([node.clientWidth || 800, node.clientHeight || height]));
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
    const mapped = flipRows(applyLut(image.gray, cmap), image.width, image.height);
    const frame = context.createImageData(image.width, image.height);
    frame.data.set(mapped);
    context.putImageData(frame, 0, 0);
    canvas.dataset.loaded = 'true';
    canvas.dataset.cmap = cmap;
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
  }, [image, cmap]);

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
    const mapped = flipRows(applyLut(overlay.gray, 'inferno'), overlay.width, overlay.height);
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
  }, [overlay, overlayAlpha]);

  // Fit the image to the container whenever a different extent arrives.
  const boundsKey = image ? image.bounds.join(',') : '';
  useEffect(() => {
    if (!image) return;
    const [xMin, xMax, yMin, yMax] = image.bounds;
    const spanX = Math.abs(xMax - xMin) || 1;
    const spanY = Math.abs(yMax - yMin) || 1;
    const zoom = Math.log2(Math.min(size[0] / spanX, size[1] / spanY));
    setViewState({ target: [(xMin + xMax) / 2, (yMin + yMax) / 2, 0], zoom });
  }, [boundsKey, size, image]);

  const onHover = useCallback((info: PickingInfo) => {
    if (!info.coordinate) {
      setReadout('');
      return;
    }
    const [x, y] = info.coordinate;
    setReadout(`x ${x.toFixed(1)} ${unit}   y ${y.toFixed(1)} ${unit}`);
  }, [unit]);

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
      const graticulePaths = paths(graticule);
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
    return list;
  }, [image, bitmap, overlayBitmap, overlayAlpha, showGraticule, graticule, contours, testId]);

  return (
    <div className={styles.wrap} style={{ height }} ref={wrapRef} data-testid={testId}>
      {image ? (
        <DeckGL
          style={{ position: 'absolute', inset: '0' }}
          views={new OrthographicView({ id: `${testId}-ortho`, flipY: false })}
          viewState={viewState}
          onViewStateChange={({ viewState: next }) => setViewState(next as OrthographicViewState)}
          controller={{ dragRotate: false }}
          layers={layers as never}
          onHover={onHover}
          width={size[0]}
          height={size[1]}
        />
      ) : (
        <div style={{ padding: 12, color: 'var(--muted)' }}>{emptyMessage}</div>
      )}
      {readout && <div className={styles.readout}>{readout}</div>}
      {/* Kept in the DOM on purpose: this is the colour-mapped image itself. */}
      <canvas ref={canvasRef} data-testid="frame-canvas" data-loaded="false" className={styles.hidden} />
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
      <span className={styles.bar} style={{ background: gradient }} aria-label={`${cmap} colour bar`} />
      <span>{vmax.toPrecision(3)}</span>
    </span>
  );
}
