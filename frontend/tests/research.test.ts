import { describe, expect, it, vi, afterEach } from 'vitest';
import {
  DEFAULT_FILTERS,
  filterIndices,
  filtersToParams,
  polygonsIntersect,
  boxPolygon,
  CATALOG_PRESETS,
} from '../src/lib/filters';
import { columnsFromTable } from '../src/lib/catalogTable';
import { syntheticTable } from './synthetic';
import { observationId } from '../src/lib/labels';
import { coverageDensity } from '../src/lib/density';
import { useStore, frameCache, type Store } from '../src/store/store';
import { api } from '../src/api/client';
import * as client from '../src/api/client';
import type { ImagePayload, StackMeta, StripMeta } from '../src/api/types';
afterEach(() => vi.unstubAllGlobals());
describe('review scientific semantics', () => {
  it('uses swath range overlap by default, with a matching server boresight alternative', () => {
    const original = columnsFromTable(syntheticTable());
    const minLat = original.minLat.slice(),
      maxLat = original.maxLat.slice();
    minLat[6] = 30;
    maxLat[6] = 85;
    const columns = { ...original, minLat, maxLat };
    const coverage = { ...DEFAULT_FILTERS, instrument: 'JunoCam' as const, latMin: 40, latMax: 50 };
    expect([...filterIndices(columns, coverage)]).toEqual([6]);
    const boresight = { ...coverage, latitudeMode: 'boresight' as const };
    expect([...filterIndices(columns, boresight)]).toEqual([]);
    expect(filtersToParams(boresight)).toMatchObject({
      latitude_mode: 'boresight',
      lat_min: '40',
      lat_max: '50',
    });
  });
  it('selects crossed swaths even when neither centre nor corner is inside the rectangle', () => {
    const horizontal = boxPolygon(-5, -1, 5, 1),
      vertical = boxPolygon(-1, -5, 1, 5);
    expect(polygonsIntersect(horizontal, vertical)).toBe(true);
    expect(polygonsIntersect(horizontal, boxPolygon(20, 20, 21, 21))).toBe(false);
    expect(polygonsIntersect(horizontal, boxPolygon(-0.1, -0.1, 0.1, 0.1))).toBe(true);
  });
  it('does not confuse processing versions with observations when counting density', () => {
    const original = columnsFromTable(syntheticTable());
    const columns = {
      ...original,
      productId: ['OBS_V01', 'OBS_V02', ...original.productId.slice(2)],
    };
    const density = coverageDensity(
      columns,
      Uint32Array.of(0, 1),
      Float32Array.from([10, 10, 10, 10]),
      { items: [], n: 0 },
      [0, 360, -90, 90],
    );
    expect(density).toHaveLength(1);
    expect(density[0].count).toBe(1);
    expect(observationId('A_V002')).toBe('A');
    expect(observationId('A_V002_extra')).toBe('A_V002_extra');
  });
  it('clears instrument-specific controls through store actions, including programmatic presets', () => {
    vi.spyOn(api, 'summary').mockResolvedValue({
      n: 0,
      by_lat_band: [],
      by_orbit: [],
      by_month: [],
    });
    const before = useStore.getState().filters;
    useStore.setState({ filters: { ...DEFAULT_FILTERS, half: 'M', revisitOnly: true } });
    useStore.getState().setFilters({ instrument: 'JunoCam' });
    expect(useStore.getState().filters).toMatchObject({
      half: 'all',
      revisitOnly: false,
      instrument: 'JunoCam',
    });
    expect(CATALOG_PRESETS.find((p) => p.id === 'repeat')?.filters.instrument).toBe('JIRAM');
    useStore.setState({ filters: before });
    vi.restoreAllMocks();
  });
});

it('keeps the latest image selected when metadata requests finish out of order', async () => {
  const original = useStore.getState();
  const meta = {
    attrs: {},
    x_km: [0, 1],
    y_km: [0, 1],
    shape: [2, 2],
    stretch: { p1: 0, p99: 1 },
    graticule: { type: 'FeatureCollection', features: [] },
  } as import('../src/api/types').StripMeta;
  let finishFirst: (value: typeof meta) => void = () => undefined;
  let finishSecond: (value: typeof meta) => void = () => undefined;
  vi.spyOn(api, 'stripMeta').mockImplementation(
    (id) =>
      new Promise((resolve) => {
        if (id === 'first') finishFirst = resolve;
        else finishSecond = resolve;
      }),
  );
  vi.spyOn(api, 'stripStats').mockResolvedValue({
    k: [],
    E: [],
    k_x: [],
    P_x: [],
    k_y: [],
    P_y: [],
    r_m: [],
    S2: [],
    S3: [],
  });
  useStore.setState({ loadStripImage: async () => undefined });
  const first = useStore.getState().openStrip('first');
  expect(useStore.getState().stripId).toBe('first');
  const second = useStore.getState().openStrip('second');
  finishSecond({ ...meta, attrs: { source: 'second' } });
  await second;
  finishFirst({ ...meta, attrs: { source: 'first' } });
  await first;
  expect(useStore.getState().stripId).toBe('second');
  expect(useStore.getState().stripMeta?.attrs?.source).toBe('second');
  useStore.setState(original);
  vi.restoreAllMocks();
});

it('computes library statistics on demand and clears values when the physical band changes', async () => {
  const original = useStore.getState();
  const stats = { k: [], E: [], k_x: [], P_x: [], k_y: [], P_y: [], r_m: [], S2: [], S3: [] };
  const request = vi.spyOn(api, 'stripStats').mockResolvedValue(stats);
  useStore.setState({
    stripId: 'observation',
    stripMeta: { bands: ['RED', 'GREEN'] } as import('../src/api/types').StripMeta,
    stripBand: 'RED',
    stripNorm: 'none',
    stripStats: null,
    statsVisible: false,
    loadStripImage: async () => undefined,
  });
  await useStore.getState().refreshStripStats();
  expect(request).not.toHaveBeenCalled();
  useStore.getState().setStatsVisible(true);
  await vi.waitFor(() => expect(useStore.getState().stripStats).toBe(stats));
  expect(request).toHaveBeenLastCalledWith('observation', 'RED', 'none');
  useStore.getState().setStatsVisible(false);
  useStore.getState().setStripBand('GREEN');
  expect(useStore.getState().stripStats).toBeNull();
  expect(request).toHaveBeenCalledTimes(1);
  useStore.getState().setStatsVisible(true);
  await vi.waitFor(() => expect(request).toHaveBeenLastCalledWith('observation', 'GREEN', 'none'));
  useStore.setState(original);
  vi.restoreAllMocks();
});

const pixel = (value: number): ImagePayload => ({
  bitmap: { close: () => undefined } as ImageBitmap,
  gray: Uint8ClampedArray.of(value, value, value, 255),
  width: 1,
  height: 1,
  bounds: [0, 1, 0, 1],
  stride: 1,
});

it.each<[string, Partial<Store>, Partial<Store>]>([
  ['band', {}, { band: 'GREEN' }],
  ['normalization parameter', { norm: 'minnaert' }, { normK: 0.9 }],
  ['flatten scale', { norm: 'flat' }, { normSigma: 256 }],
  ['stretch limits', {}, { vmin: 5, vmax: 10 }],
  ['stretch mapping', {}, { stretchMode: 'asinh' }],
  ['RGB mode', {}, { composite: true }],
  [
    'RGB channel stretch',
    { composite: true },
    { bandStretch: { RED: [5, 10], GREEN: [1, 2], BLUE: [2, 3] } },
  ],
  ['RGB stretch linking', { composite: true }, { linkBands: true }],
])('rejects a late time-series image after changing %s', async (_name, initial, changed) => {
  const original = useStore.getState();
  const pending: ((image: ImagePayload) => void)[] = [];
  const request = vi
    .spyOn(client, 'fetchImage')
    .mockImplementation(() => new Promise((resolve) => pending.push(resolve)));
  frameCache.clear();
  useStore.setState({
    stackId: 'test/stack',
    frameImage: pixel(55),
    stackMeta: { times: [0] } as unknown as StackMeta,
    t: 0,
    stackBands: ['RED', 'GREEN', 'BLUE'],
    band: 'RED',
    composite: false,
    norm: 'none',
    normK: 0.5,
    normSigma: 128,
    stretchMode: 'linear',
    vmin: 0,
    vmax: 255,
    linkBands: false,
    bandStretch: { RED: [0, 1], GREEN: [1, 2], BLUE: [2, 3] },
    emissionAlpha: 0,
    ...initial,
  });
  const old = useStore.getState().loadFrame(0);
  expect(useStore.getState().frameImage).toBeNull();
  useStore.setState(changed);
  const latest = useStore.getState().loadFrame(0);
  expect(useStore.getState().frameImage).toBeNull();
  expect(request.mock.calls[0][0]).not.toBe(request.mock.calls[1][0]);
  const currentPixels = pixel(99);
  pending[1](currentPixels);
  await latest;
  pending[0](pixel(10));
  await old;
  expect(useStore.getState().frameImage).toBe(currentPixels);
  expect(useStore.getState().frameComposite).toBe(useStore.getState().composite);
  useStore.setState(original);
  frameCache.clear();
  vi.restoreAllMocks();
});

it.each<[string, Partial<Store>]>([
  ['band', { stripBand: 'GREEN' }],
  ['normalization parameter', { stripNormK: 0.9 }],
  ['stretch mapping', { stripStretchMode: 'asinh' }],
  ['RGB mode', { stripComposite: true }],
])('rejects a late library image after changing %s', async (_name, changed) => {
  const original = useStore.getState();
  const pending: ((image: ImagePayload) => void)[] = [];
  vi.spyOn(client, 'fetchImage').mockImplementation(
    () => new Promise((resolve) => pending.push(resolve)),
  );
  useStore.setState({
    stripId: 'test-strip',
    stripImage: pixel(55),
    stripMeta: { stretch: { p1: 0, p99: 255 } } as StripMeta,
    stripBands: ['RED', 'GREEN', 'BLUE'],
    stripBand: 'RED',
    stripComposite: false,
    stripNorm: 'minnaert',
    stripNormK: 0.5,
    stripNormSigma: 128,
    stripStretchMode: 'linear',
  });
  const old = useStore.getState().loadStripImage();
  expect(useStore.getState().stripImage).toBeNull();
  useStore.setState(changed);
  const latest = useStore.getState().loadStripImage();
  expect(useStore.getState().stripImage).toBeNull();
  pending.slice(1).forEach((resolve) => resolve(pixel(99)));
  await latest;
  const currentPixels = useStore.getState().stripImage;
  pending[0](pixel(10));
  await old;
  expect(useStore.getState().stripImage).toBe(currentPixels);
  expect(useStore.getState().stripImage?.gray[0]).toBe(99);
  expect(useStore.getState().stripImageComposite).toBe(useStore.getState().stripComposite);
  useStore.setState(original);
  vi.restoreAllMocks();
});
