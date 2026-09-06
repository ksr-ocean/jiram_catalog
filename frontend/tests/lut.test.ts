import { describe, expect, it } from 'vitest';
import { applyLut, COLOR_MAPS, lutColor, makeLut } from '../src/lib/lut';

describe('colour-map LUTs', () => {
  it('has 256 RGB entries for every map', () => {
    for (const name of COLOR_MAPS) {
      const lut = makeLut(name);
      expect(lut.length).toBe(256 * 3);
      expect(lut).toBeInstanceOf(Uint8Array);
    }
  });

  it('pins the endpoints to the map extremes', () => {
    expect([...makeLut('gray').slice(0, 3)]).toEqual([0, 0, 0]);
    expect([...makeLut('gray').slice(-3)]).toEqual([255, 255, 255]);
    expect([...makeLut('viridis').slice(0, 3)]).toEqual([68, 1, 84]);
    expect([...makeLut('viridis').slice(-3)]).toEqual([253, 231, 37]);
    expect([...makeLut('inferno').slice(0, 3)]).toEqual([0, 0, 4]);
    expect([...makeLut('cividis').slice(-3)]).toEqual([253, 205, 42]);
  });

  it('is monotone in luminance for gray and returns the same object twice', () => {
    const lut = makeLut('gray');
    for (let i = 1; i < 256; i++) expect(lut[i * 3]).toBeGreaterThanOrEqual(lut[(i - 1) * 3]);
    expect(makeLut('gray')).toBe(lut);
  });

  it('renders a hex swatch', () => {
    expect(lutColor('gray', 0)).toBe('#000000');
    expect(lutColor('gray', 255)).toBe('#ffffff');
    expect(lutColor('gray', 1e6)).toBe('#ffffff');
  });

  it('maps a grayscale RGBA buffer and keeps its alpha', () => {
    const source = new Uint8ClampedArray([0, 0, 0, 255, 255, 255, 255, 0]);
    const mapped = applyLut(source, 'viridis');
    expect([...mapped.slice(0, 4)]).toEqual([68, 1, 84, 255]);
    expect([...mapped.slice(4, 8)]).toEqual([253, 231, 37, 0]);
    expect(mapped).not.toBe(source);
  });

  it('gives different maps different colours for the same value', () => {
    const gray = makeLut('gray').slice(128 * 3, 128 * 3 + 3);
    const magma = makeLut('magma').slice(128 * 3, 128 * 3 + 3);
    expect([...gray]).not.toEqual([...magma]);
  });
});
