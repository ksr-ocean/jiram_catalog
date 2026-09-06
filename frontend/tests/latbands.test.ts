import { describe, expect, it } from 'vitest';
import { binByLatBand, latBand, latBandLimits, LAT_BAND_NAMES } from '../src/lib/latbands';

describe('latitude bands', () => {
  it('matches the trackability report edges', () => {
    expect(latBand(-90)).toBe('S polar');
    expect(latBand(-75)).toBe('S polar');
    expect(latBand(-60)).toBe('S mid');
    expect(latBand(-30)).toBe('S low');
    expect(latBand(-10)).toBe('equator');
    expect(latBand(0)).toBe('equator');
    expect(latBand(10)).toBe('N low');
    expect(latBand(30)).toBe('N mid');
    expect(latBand(60)).toBe('N polar');
    expect(latBand(90)).toBe('N polar');
  });

  it('has no band for a missing latitude', () => {
    expect(latBand(NaN)).toBeNull();
  });

  it('reports each band span, and the sphere for an unknown name', () => {
    expect(latBandLimits('N polar')).toEqual([60, 90]);
    expect(latBandLimits('equator')).toEqual([-10, 10]);
    expect(latBandLimits('all')).toEqual([-90, 90]);
  });

  it('bins a column, optionally through an index array', () => {
    const lat = Float32Array.from([70, 65, 0, -70, NaN, 20]);
    const all = binByLatBand(lat);
    expect(all.get('N polar')).toBe(2);
    expect(all.get('equator')).toBe(1);
    expect(all.get('S polar')).toBe(1);
    expect(all.get('N low')).toBe(1);
    expect([...all.keys()]).toEqual([...LAT_BAND_NAMES]);
    const subset = binByLatBand(lat, Uint32Array.of(0, 2));
    expect(subset.get('N polar')).toBe(1);
    expect(subset.get('S polar')).toBe(0);
  });
});
