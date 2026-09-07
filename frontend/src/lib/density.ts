import type { CatalogColumns } from './catalogTable';
import type { FootprintSet } from './footprints';
import { polygonsIntersect } from './filters';
import { observationId } from './labels';
export interface DensityCell {
  polygon: [number, number][];
  count: number;
}
/** Counts unique observations per coarse cell, using footprints where available. */
export function coverageDensity(
  columns: CatalogColumns,
  indices: Uint32Array,
  positions: Float32Array,
  footprints: FootprintSet,
  bounds: [number, number, number, number],
): DensityCell[] {
  const [x0, x1, y0, y1] = bounds,
    nx = 36,
    ny = 18,
    dx = (x1 - x0) / nx,
    dy = (y1 - y0) / ny;
  const cells = new Map<number, { polygon: [number, number][]; ids: Set<string> }>();
  const covered = new Set(footprints.items.map((item) => item.row));
  const cell = (ix: number, iy: number) => {
    const key = iy * nx + ix;
    let value = cells.get(key);
    if (!value) {
      const x = x0 + ix * dx,
        y = y0 + iy * dy;
      value = {
        polygon: [
          [x, y],
          [x + dx, y],
          [x + dx, y + dy],
          [x, y + dy],
        ],
        ids: new Set(),
      };
      cells.set(key, value);
    }
    return value;
  };
  for (const i of indices) {
    if (covered.has(i)) continue;
    const x = positions[i * 2],
      y = positions[i * 2 + 1],
      ix = Math.floor((x - x0) / dx),
      iy = Math.floor((y - y0) / dy);
    if (Number.isFinite(x) && Number.isFinite(y) && ix >= 0 && ix < nx && iy >= 0 && iy < ny)
      cell(ix, iy).ids.add(observationId(columns.productId[i]));
  }
  for (const item of footprints.items) {
    const [a, b, c, d] = item.bounds;
    for (
      let ix = Math.max(0, Math.floor((a - x0) / dx));
      ix <= Math.min(nx - 1, Math.floor((b - x0) / dx));
      ix++
    )
      for (
        let iy = Math.max(0, Math.floor((c - y0) / dy));
        iy <= Math.min(ny - 1, Math.floor((d - y0) / dy));
        iy++
      ) {
        const target = cell(ix, iy);
        if (polygonsIntersect(item.polygon, target.polygon))
          target.ids.add(observationId(columns.productId[item.row]));
      }
  }
  return [...cells.values()]
    .filter((c) => c.ids.size > 0)
    .map((c) => ({ polygon: c.polygon, count: c.ids.size }));
}
