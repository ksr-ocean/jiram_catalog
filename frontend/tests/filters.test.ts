import { describe, expect, it } from 'vitest';
import { columnsFromIPC, columnsFromTable } from '../src/lib/catalogTable';
import {
  boxPolygon,
  DEFAULT_FILTERS,
  filterIndices,
  filtersToParams,
  indicesInPolygon,
  isDefaultFilters,
  pointInPolygon,
} from '../src/lib/filters';
import { SPEC, syntheticIPC, syntheticTable } from './synthetic';

const columns = columnsFromTable(syntheticTable());
const keep = (patch: Partial<typeof DEFAULT_FILTERS>) =>
  Array.from(filterIndices(columns, { ...DEFAULT_FILTERS, ...patch }));

describe('the Arrow table becomes typed columns', () => {
  it('reads the contract columns with the right shapes', () => {
    expect(columns.n).toBe(SPEC.productId.length);
    expect(columns.productId[0]).toBe('P0');
    expect(columns.half[3]).toBe('M');
    expect(columns.boreLat).toBeInstanceOf(Float32Array);
    expect(columns.startTimeMs[0]).toBe(SPEC.startTimeMs[0]);
    expect(columns.year[0]).toBe(2016);
    expect(Number.isNaN(columns.boreLat[3])).toBe(true);
    expect(columns.hasPartner[0]).toBe(1);
    expect(columns.hasPartner[1]).toBe(0);
  });

  it('parses the same table from IPC bytes', () => {
    const fromBytes = columnsFromIPC(syntheticIPC());
    expect(fromBytes.n).toBe(columns.n);
    expect([...fromBytes.orbit]).toEqual([...columns.orbit]);
  });

  it('fills a missing column with NaN rather than throwing', () => {
    expect(columns.lonSpanDeg[0]).toBe(12);
  });
});

describe('catalog filters', () => {
  it('keeps everything by default', () => {
    expect(keep({})).toEqual([0, 1, 2, 3, 4, 5]);
    expect(isDefaultFilters(DEFAULT_FILTERS)).toBe(true);
    expect(isDefaultFilters({ ...DEFAULT_FILTERS, half: 'M' })).toBe(false);
  });

  it('restricts the orbit range', () => {
    expect(keep({ orbitMin: 4, orbitMax: 11 })).toEqual([0, 1, 2, 3]);
  });

  it('restricts the epoch range', () => {
    expect(keep({ timeMin: Date.UTC(2018, 0, 1) })).toEqual([2, 3, 4, 5]);
    expect(keep({ timeMax: Date.UTC(2017, 0, 1) })).toEqual([0, 1]);
  });

  it('selects one band half', () => {
    expect(keep({ half: 'L' })).toEqual([0, 2, 4]);
    expect(keep({ half: 'M' })).toEqual([1, 3, 5]);
  });

  it('excludes on a threshold but keeps rows whose value is missing', () => {
    // row 2 has no pixel size and row 2 no emission angle: both stay.
    expect(keep({ pixelMaxKm: 20 })).toEqual([0, 2, 3, 4]);
    expect(keep({ emissionMax: 40 })).toEqual([0, 2, 3, 4]);
  });

  it('drops rows with no on-planet fraction, the one threshold that requires', () => {
    // row 4 has NaN on_planet_frac and is dropped even though 0.5 <= NaN is false.
    expect(keep({ onPlanetMin: 0.5 })).toEqual([0, 2, 5]);
  });

  it('drops rows with no boresight latitude once a latitude bound is set', () => {
    // row 3 has NaN bore_lat: kept with no bound, dropped as soon as one is given.
    expect(keep({}).includes(3)).toBe(true);
    expect(keep({ latMin: -90, latMax: 60 })).toEqual([1, 2, 5]);
    expect(keep({ latMin: 60 })).toEqual([0, 4]);
    expect(keep({ latMin: 60, latMax: 90 })).toEqual([0, 4]);
  });

  it('keeps only dayside frames when asked', () => {
    expect(keep({ daysideOnly: true })).toEqual([0, 2, 4]);
  });

  it('keeps only frames with a same-pass revisit', () => {
    expect(keep({ revisitOnly: true })).toEqual([0, 2, 4]);
  });

  it('combines filters', () => {
    expect(keep({ half: 'L', latMin: 60, latMax: 90 })).toEqual([0, 4]);
  });
});

describe('the summary query string mirrors the client-side filter', () => {
  it('sends nothing when nothing is set', () => {
    expect(filtersToParams(DEFAULT_FILTERS)).toEqual({});
  });

  it('sends exactly the parameters that differ', () => {
    const params = filtersToParams({
      ...DEFAULT_FILTERS,
      half: 'M',
      latMin: 60,
      pixelMaxKm: 20,
      daysideOnly: true,
      revisitOnly: true,
      timeMin: Date.UTC(2018, 0, 1),
    });
    expect(params).toEqual({
      half: 'M',
      lat_min: '60',
      pixel_max_km: '20',
      dayside_only: 'true',
      revisit_only: 'true',
      time_min: '2018-01-01T00:00:00.000Z',
    });
    expect(params.lat_max).toBeUndefined();
  });
});

describe('polygon selection over the typed arrays', () => {
  const xy = Float32Array.from([0, 0, 5, 5, 20, 20, NaN, 1]);
  const all = Uint32Array.of(0, 1, 2, 3);

  it('tests a point against a polygon', () => {
    const square = boxPolygon(-1, -1, 10, 10);
    expect(pointInPolygon(5, 5, square)).toBe(true);
    expect(pointInPolygon(20, 20, square)).toBe(false);
    expect(pointInPolygon(5, 5, [[0, 0], [1, 1]])).toBe(false);
  });

  it('returns the indices inside a box and skips NaN positions', () => {
    expect(indicesInPolygon(xy, all, boxPolygon(-1, -1, 10, 10))).toEqual([0, 1]);
    expect(indicesInPolygon(xy, all, boxPolygon(100, 100, 200, 200))).toEqual([]);
    expect(indicesInPolygon(xy, all, [[0, 0], [1, 1]])).toEqual([]);
  });

  it('handles a lasso that is not a rectangle', () => {
    const triangle: [number, number][] = [[-1, -1], [12, -1], [-1, 12]];
    expect(indicesInPolygon(xy, all, triangle)).toEqual([0, 1]);
  });

  it('builds a normalised box from any drag direction', () => {
    expect(boxPolygon(10, 10, 0, 0)).toEqual(boxPolygon(0, 0, 10, 10));
  });
});
