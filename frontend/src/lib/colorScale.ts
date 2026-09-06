/**
 * Point colours for the catalog map.
 *
 * A categorical wheel for orbit and year (adjacent orbits should be told
 * apart, not blended) and a continuous viridis ramp for the two physical
 * quantities, so the legend can say what the colours mean.
 */
import { makeLut, type ColorMapName } from './lut';

export type RGB = [number, number, number];

const WHEEL: RGB[] = [
  [102, 194, 165], [252, 141, 98], [141, 160, 203], [231, 138, 195],
  [166, 216, 84], [255, 217, 47], [229, 196, 148], [179, 179, 179],
  [141, 211, 199], [190, 186, 218], [251, 128, 114], [128, 177, 211],
];

export function categoricalColor(value: number): RGB {
  if (!Number.isFinite(value)) return [110, 110, 110];
  return WHEEL[((Math.round(value) % WHEEL.length) + WHEEL.length) % WHEEL.length];
}

export function rampColor(value: number, low: number, high: number, cmap: ColorMapName = 'viridis'): RGB {
  if (!Number.isFinite(value)) return [110, 110, 110];
  const lut = makeLut(cmap);
  const span = high - low || 1;
  const i = Math.max(0, Math.min(255, Math.round(((value - low) / span) * 255))) * 3;
  return [lut[i], lut[i + 1], lut[i + 2]];
}

export function rgbCss([r, g, b]: RGB): string {
  return `rgb(${r},${g},${b})`;
}
