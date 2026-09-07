/**
 * Footprint outlines: the seam, the pole, and the two polar projections.
 *
 * The seam is the case that matters.  A JunoCam swath that straddles 0/360 is
 * one shape on the sphere; drawn as one polygon on a cylindrical map it is a
 * band right across the picture, which is both wrong and the sort of wrong
 * that looks like real coverage.  These tests pin the split, the interpolated
 * latitude where the pieces meet the edges, and the case above them both --
 * an outline that goes right round a pole and never closes at all.
 */
import { describe, expect, it } from 'vitest';
import { columnsFromTable } from '../src/lib/catalogTable';
import { DEFAULT_FILTERS, filterIndices } from '../src/lib/filters';
import { computeFootprints, footprintPolygons, shortestDelta, splitSeam } from '../src/lib/footprints';
import { SPEC, syntheticTable } from './synthetic';

const columns = columnsFromTable(syntheticTable());
const filtered = filterIndices(columns, DEFAULT_FILTERS);

/** Every longitude of a polygon, rounded, for comparing shapes. */
const xs = (ring: [number, number][]) => ring.map(([x]) => Math.round(x * 1000) / 1000);
const ys = (ring: [number, number][]) => ring.map(([, y]) => Math.round(y * 1000) / 1000);

describe('the way round a ring', () => {
  it('folds a longitude difference into the shorter arc', () => {
    expect(shortestDelta(20)).toBe(20);
    expect(shortestDelta(-20)).toBe(-20);
    expect(shortestDelta(340)).toBe(-20);
    expect(shortestDelta(-340)).toBe(20);
    expect(shortestDelta(180)).toBe(180);
    expect(shortestDelta(NaN)).toBe(0);
  });
});

describe('seam splitting', () => {
  it('leaves an outline that does not touch the seam alone', () => {
    const rings = splitSeam([10, 30, 30, 10], [60, 60, 70, 70]);
    expect(rings).toHaveLength(1);
    expect(xs(rings[0])).toEqual([10, 30, 30, 10, 10]);
  });

  it('cuts an outline that straddles 0/360 into two polygons at the edges', () => {
    // A box from 350 east to 10 east: two pieces, one against each edge.
    const rings = splitSeam([350, 10, 10, 350], [20, 20, 30, 30]);
    expect(rings).toHaveLength(2);
    const spans = rings.map((ring) => [Math.min(...xs(ring)), Math.max(...xs(ring))]);
    expect(spans).toContainEqual([0, 10]);
    expect(spans).toContainEqual([350, 360]);
    // Every piece keeps the latitudes of the box it came from.
    for (const ring of rings) {
      for (const y of ys(ring)) expect(y).toBeGreaterThanOrEqual(20);
      for (const y of ys(ring)) expect(y).toBeLessThanOrEqual(30);
    }
  });

  it('interpolates the latitude at which an edge crosses the seam', () => {
    // One edge from (350, 0) to (10, 20): half way round, so half way up.
    const rings = splitSeam([350, 10, 10, 350], [0, 20, 40, 40]);
    const atEdge = rings
      .flat()
      .filter(([x]) => x === 0 || x === 360)
      .map(([, y]) => Math.round(y * 100) / 100);
    expect(atEdge).toContain(10);
  });

  it('closes an outline that encircles the pole over the top of the map', () => {
    // A ring right round the north pole: eight vertices, one full turn.
    const lon = [0, 45, 90, 135, 180, 225, 270, 315];
    const lat = lon.map(() => 80);
    const rings = splitSeam(lon, lat);
    expect(rings).toHaveLength(1);
    const ring = rings[0];
    expect(Math.min(...xs(ring))).toBe(0);
    expect(Math.max(...xs(ring))).toBe(360);
    // The last two vertices are the corners that close it over the pole.
    expect(ys(ring).filter((y) => y === 90)).toHaveLength(2);
  });

  it('closes a south-polar ring over the bottom edge instead', () => {
    const lon = [0, 90, 180, 270];
    const rings = splitSeam(lon, lon.map(() => -85));
    expect(ys(rings[0]).filter((y) => y === -90)).toHaveLength(2);
  });

  it('gives nothing rather than a degenerate shape', () => {
    expect(splitSeam([], [])).toEqual([]);
    expect(splitSeam([10, 20], [1, 2])).toEqual([]);
    expect(splitSeam([10, NaN, 20, 30], [1, 2, NaN, 4])).toEqual([]);
    // A closing vertex that repeats the first is not a fourth corner.
    expect(splitSeam([10, 30, 20, 10], [1, 2, 3, 1])).toHaveLength(1);
  });

  it('wraps a negative east longitude before anything else', () => {
    const rings = splitSeam([-10, 10, 10, -10], [20, 20, 30, 30]);
    expect(rings).toHaveLength(2);
  });
});

describe('a footprint in the polar projections', () => {
  it('projects without a seam, because a polar view has none', () => {
    const rings = footprintPolygons([350, 10, 10, 350], [76, 76, 88, 88], 'N');
    expect(rings).toHaveLength(1);
    expect(rings[0]).toHaveLength(4);
    // rho = 90 - |lat|, so a vertex at 76 deg sits 14 deg from the origin.
    const radii = rings[0].map(([x, y]) => Math.round(Math.hypot(x, y)));
    expect(radii).toEqual([14, 14, 2, 2]);
  });

  it('drops an outline that is entirely in the other hemisphere', () => {
    expect(footprintPolygons([10, 30, 30, 10], [-60, -60, -70, -70], 'N')).toEqual([]);
    expect(footprintPolygons([10, 30, 30, 10], [60, 60, 70, 70], 'S')).toEqual([]);
  });

  it('clamps an outline that straddles the equator at the rim', () => {
    const rings = footprintPolygons([10, 30, 30, 10], [-10, -10, 10, 10], 'N');
    expect(rings).toHaveLength(1);
    const radii = rings[0].map(([x, y]) => Math.round(Math.hypot(x, y)));
    expect(Math.max(...radii)).toBe(90);
  });
});

describe('the footprints of a filtered catalog', () => {
  it('draws one outline per JunoCam row and none for JIRAM', () => {
    const set = computeFootprints(columns, filtered, 'cyl');
    expect(set.n).toBe(2);
    // The seam-split row contributes two polygons but counts once.
    expect(set.items.length).toBe(3);
    expect(new Set(set.items.map((item) => columns.productId[item.row]))).toEqual(new Set(['JC0', 'JC1']));
  });

  it('carries a bounding box for the hover test', () => {
    const set = computeFootprints(columns, filtered, 'cyl');
    for (const item of set.items) {
      const [xMin, xMax, yMin, yMax] = item.bounds;
      expect(xMax).toBeGreaterThanOrEqual(xMin);
      expect(yMax).toBeGreaterThanOrEqual(yMin);
    }
  });

  it('stops at the limit rather than drawing an unbounded amount of geometry', () => {
    expect(computeFootprints(columns, filtered, 'cyl', 1).n).toBe(1);
    expect(computeFootprints(columns, filtered, 'cyl', 0).items).toEqual([]);
  });

  it('follows the view mode', () => {
    const north = computeFootprints(columns, filtered, 'N');
    expect(north.n).toBe(2);
    const south = computeFootprints(columns, filtered, 'S');
    expect(south.n).toBe(0);
  });

  it('gives nothing when the backend sends no footprint columns', () => {
    const older = columnsFromTable(syntheticTable({ ...SPEC, fpLon: SPEC.fpLon.map(() => []), fpLat: SPEC.fpLat.map(() => []) }));
    expect(computeFootprints(older, filtered, 'cyl')).toEqual({ items: [], n: 0 });
  });
});
