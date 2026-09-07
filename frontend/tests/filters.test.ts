import { describe, expect, it } from 'vitest';
import { bandsByInstrument, columnsFromIPC, columnsFromTable } from '../src/lib/catalogTable';
import { bandOptions, bandsInclude, qualityPasses } from '../src/lib/bands';
import {
  boxPolygon,
  DEFAULT_FILTERS,
  filterIndices,
  filtersToParams,
  indicesInPolygon,
  isDefaultFilters,
  pointInPolygon,
} from '../src/lib/filters';
import { SPEC, syntheticIPC, syntheticTable, syntheticTableListBands } from './synthetic';

const columns = columnsFromTable(syntheticTable());
/** The same eight rows with the second JunoCam image demoted to tier C. */
const withTierC = columnsFromTable(
  syntheticTable({ ...SPEC, qualityTier: ['A', 'A', 'A', 'A', 'A', 'A', 'A', 'C'] }),
);
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

  it('normalises a `bands` column sent as a list instead of a joined string', () => {
    const listed = columnsFromTable(syntheticTableListBands());
    expect(listed.bands[6]).toBe('RED;GREEN;BLUE');
    expect(listed.bands[0]).toBe('L');
    expect(Array.from(filterIndices(listed, { ...DEFAULT_FILTERS, band: 'RED' }))).toEqual([6, 7]);
    expect(bandsByInstrument(listed).JunoCam).toEqual(['BLUE', 'GREEN', 'METHANE', 'RED']);
  });

  it('reads the columns the amendment added, footprints included', () => {
    expect(columns.instrument[0]).toBe('JIRAM');
    expect(columns.instrument[6]).toBe('JunoCam');
    expect(columns.bands[6]).toBe('RED;GREEN;BLUE');
    expect(columns.qualityTier[7]).toBe('B');
    // A JIRAM row has an empty outline and a JunoCam row four vertices.
    expect(columns.fpLon.offsets[1] - columns.fpLon.offsets[0]).toBe(0);
    expect(columns.fpLon.offsets[7] - columns.fpLon.offsets[6]).toBe(4);
    expect([...columns.fpLat.values.subarray(columns.fpLat.offsets[6], columns.fpLat.offsets[7])]).toEqual([
      70, 70, 84, 84,
    ]);
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
    expect(keep({})).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(isDefaultFilters(DEFAULT_FILTERS)).toBe(true);
    expect(isDefaultFilters({ ...DEFAULT_FILTERS, half: 'M' })).toBe(false);
  });

  it('restricts the orbit range', () => {
    expect(keep({ orbitMin: 4, orbitMax: 11 })).toEqual([0, 1, 2, 3]);
  });

  it('restricts the epoch range', () => {
    expect(keep({ timeMin: Date.UTC(2018, 0, 1) })).toEqual([2, 3, 4, 5, 6, 7]);
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
    expect(keep({ latMin: 60 })).toEqual([0, 4, 6, 7]);
    expect(keep({ latMin: 60, latMax: 90 })).toEqual([0, 4, 6, 7]);
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

describe('the instrument, band and quality filters of the 2026-09-07 amendment', () => {
  const keepIn = (patch: Partial<typeof DEFAULT_FILTERS>, cols = columns) =>
    Array.from(filterIndices(cols, { ...DEFAULT_FILTERS, ...patch }));

  it('keeps one instrument at a time, and both by default', () => {
    expect(keepIn({ instrument: 'JIRAM' })).toEqual([0, 1, 2, 3, 4, 5]);
    expect(keepIn({ instrument: 'JunoCam' })).toEqual([6, 7]);
    expect(keepIn({ instrument: 'all' })).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
  });

  it('asks for a band the row must carry, across both instruments', () => {
    expect(keepIn({ band: 'M' })).toEqual([1, 3, 5]);
    expect(keepIn({ band: 'RED' })).toEqual([6, 7]);
    // METHANE is the fourth filter, which only the second JunoCam row has.
    expect(keepIn({ band: 'METHANE' })).toEqual([7]);
    expect(keepIn({ band: 'ULTRAVIOLET' })).toEqual([]);
  });

  it('matches a band as a whole token, not as a substring', () => {
    expect(bandsInclude('RED;GREEN;BLUE', 'RED')).toBe(true);
    expect(bandsInclude('RED;GREEN;BLUE', 'BLU')).toBe(false);
    expect(bandsInclude('INFRARED;GREEN', 'RED')).toBe(false);
    expect(bandsInclude('red;green', 'RED')).toBe(true);
    expect(bandsInclude('', 'RED')).toBe(false);
  });

  it('combines instrument and band', () => {
    expect(keepIn({ instrument: 'JunoCam', band: 'METHANE' })).toEqual([7]);
    expect(keepIn({ instrument: 'JIRAM', band: 'RED' })).toEqual([]);
  });

  it('hides tier C unless it is asked for, and keeps a row with no tier', () => {
    expect(keepIn({}, withTierC)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(keepIn({ qualityMin: 'C' }, withTierC)).toEqual([0, 1, 2, 3, 4, 5, 6, 7]);
    expect(keepIn({ qualityMin: 'A' }, withTierC)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(keepIn({ qualityMin: 'A' })).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(qualityPasses(null, 'A')).toBe(true);
    expect(qualityPasses('', 'A')).toBe(true);
  });

  it('offers the band names the instrument in view actually has', () => {
    const byInstrument = bandsByInstrument(columns);
    expect(byInstrument.JIRAM).toEqual(['L', 'M']);
    expect(byInstrument.JunoCam).toEqual(['BLUE', 'GREEN', 'METHANE', 'RED']);
    expect(bandOptions(byInstrument, 'JIRAM')).toEqual(['L', 'M']);
    expect(bandOptions(byInstrument, 'JunoCam')).toEqual(['RED', 'GREEN', 'BLUE', 'METHANE']);
    expect(bandOptions(byInstrument, 'all')).toEqual(['L', 'M', 'RED', 'GREEN', 'BLUE', 'METHANE']);
  });
});

describe('the summary query string mirrors the client-side filter', () => {
  it('sends nothing when nothing is set', () => {
    expect(filtersToParams(DEFAULT_FILTERS)).toEqual({});
  });

  it('sends the amendment\'s three parameters only when they are set', () => {
    expect(filtersToParams({ ...DEFAULT_FILTERS, qualityMin: 'B' })).toEqual({});
    expect(
      filtersToParams({ ...DEFAULT_FILTERS, instrument: 'JunoCam', band: 'RED', qualityMin: 'C' }),
    ).toEqual({ instrument: 'JunoCam', bands: 'RED', quality_min: 'C' });
    expect(filtersToParams({ ...DEFAULT_FILTERS, qualityMin: 'A' })).toEqual({ quality_min: 'A' });
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
