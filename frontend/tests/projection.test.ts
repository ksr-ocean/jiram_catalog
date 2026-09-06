import { describe, expect, it } from 'vitest';
import {
  graticule,
  project,
  projectColumns,
  projectCylindrical,
  projectPolar,
  viewLimits,
  wrapLon,
} from '../src/lib/projection';

describe('cylindrical projection', () => {
  it('is the identity inside 0..360', () => {
    expect(projectCylindrical(12.5, 200)).toEqual([200, 12.5]);
  });

  it('folds the antimeridian and negative longitudes into 0..360', () => {
    expect(wrapLon(-170)).toBeCloseTo(190, 10);
    expect(wrapLon(180)).toBeCloseTo(180, 10);
    expect(wrapLon(360)).toBeCloseTo(0, 10);
    expect(wrapLon(380)).toBeCloseTo(20, 10);
    expect(wrapLon(-0.5)).toBeCloseTo(359.5, 10);
    expect(projectCylindrical(0, -180)[0]).toBeCloseTo(180, 10);
  });

  it('keeps NaN as NaN so nothing is drawn', () => {
    expect(projectCylindrical(NaN, 10).every(Number.isNaN)).toBe(false);
    expect(Number.isNaN(projectCylindrical(NaN, 10)[1])).toBe(true);
    expect(Number.isNaN(wrapLon(NaN))).toBe(true);
  });
});

describe('polar projection', () => {
  it('puts the pole at the origin and the equator on the rim', () => {
    const [x, y] = projectPolar(90, 123, 'N');
    expect(x).toBeCloseTo(0, 10);
    expect(y).toBeCloseTo(0, 10);
    const rim = projectPolar(0, 0, 'N');
    expect(Math.hypot(rim[0], rim[1])).toBeCloseTo(90, 10);
  });

  it('uses east longitude as the polar angle', () => {
    const [x, y] = projectPolar(60, 90, 'N');
    expect(x).toBeCloseTo(0, 8);
    expect(y).toBeCloseTo(30, 8);
    const wrapped = projectPolar(60, 450, 'N');
    expect(wrapped[0]).toBeCloseTo(x, 8);
    expect(wrapped[1]).toBeCloseTo(y, 8);
  });

  it('is continuous across the antimeridian', () => {
    const before = projectPolar(70, 179.999, 'N');
    const after = projectPolar(70, -180.001, 'N');
    expect(Math.hypot(before[0] - after[0], before[1] - after[1])).toBeLessThan(1e-3);
  });

  it('drops the other hemisphere to NaN', () => {
    expect(projectPolar(-10, 0, 'N').every(Number.isNaN)).toBe(true);
    expect(projectPolar(10, 0, 'S').every(Number.isNaN)).toBe(true);
    expect(projectPolar(-10, 0, 'S').some(Number.isNaN)).toBe(false);
  });

  it('mirrors the two hemispheres about the equator in rho', () => {
    expect(Math.hypot(...projectPolar(-45, 30, 'S'))).toBeCloseTo(Math.hypot(...projectPolar(45, 30, 'N')), 10);
  });
});

describe('column projection', () => {
  it('writes a flat xy buffer in the requested mode', () => {
    const lat = Float32Array.from([0, 45, -45]);
    const lon = Float32Array.from([-10, 90, 90]);
    const cyl = projectColumns(lat, lon, 'cyl');
    expect(cyl.length).toBe(6);
    expect(cyl[0]).toBeCloseTo(350, 4);
    const north = projectColumns(lat, lon, 'N');
    expect(Number.isNaN(north[4])).toBe(true);
    expect(project(45, 90, 'N')[1]).toBeCloseTo(45, 6);
  });

  it('reuses a supplied buffer of the right length', () => {
    const out = new Float32Array(4);
    const result = projectColumns(Float32Array.of(1, 2), Float32Array.of(3, 4), 'cyl', out);
    expect(result).toBe(out);
  });
});

describe('view furniture', () => {
  it('gives each mode its data limits', () => {
    expect(viewLimits('cyl')).toEqual([0, 360, -90, 90]);
    expect(viewLimits('N')).toEqual([-90, 90, -90, 90]);
  });

  it('draws a 10-degree graticule in cylindrical mode', () => {
    const paths = graticule('cyl');
    expect(paths.length).toBe(19 + 37);
    expect(paths[0].path).toEqual([[0, -90], [360, -90]]);
    expect(graticule('N').length).toBeGreaterThan(0);
  });
});
