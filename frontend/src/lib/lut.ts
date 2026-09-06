/**
 * Colour-map lookup tables, generated in the browser.
 *
 * v1 asked the server for a re-rendered image whenever the colour map
 * changed, and the change silently failed to arrive.  v2 makes that
 * impossible: the server sends one grayscale PNG per frame and the browser
 * maps it through a 256-entry RGB table, so a colour-map change is a redraw
 * of pixels already in memory and cannot depend on a network round trip.
 */

export type ColorMapName = 'gray' | 'viridis' | 'magma' | 'inferno' | 'cividis';

export const COLOR_MAPS: ColorMapName[] = ['gray', 'viridis', 'magma', 'inferno', 'cividis'];

/** Anchor points (matplotlib, sampled at 9 stops) interpolated to 256 entries. */
const ANCHORS: Record<ColorMapName, [number, number, number][]> = {
  gray: [
    [0, 0, 0], [32, 32, 32], [64, 64, 64], [96, 96, 96], [128, 128, 128],
    [160, 160, 160], [192, 192, 192], [224, 224, 224], [255, 255, 255],
  ],
  viridis: [
    [68, 1, 84], [72, 40, 120], [62, 74, 137], [49, 104, 142], [38, 130, 142],
    [31, 158, 137], [53, 183, 121], [109, 205, 89], [253, 231, 37],
  ],
  magma: [
    [0, 0, 4], [28, 16, 68], [79, 18, 123], [129, 37, 129], [181, 54, 122],
    [229, 80, 100], [251, 135, 97], [254, 194, 135], [252, 253, 191],
  ],
  inferno: [
    [0, 0, 4], [31, 12, 72], [85, 15, 109], [136, 34, 106], [186, 54, 85],
    [227, 89, 51], [249, 142, 9], [249, 201, 50], [252, 255, 164],
  ],
  cividis: [
    [0, 32, 76], [0, 51, 110], [51, 71, 111], [83, 91, 110], [110, 110, 110],
    [140, 131, 105], [173, 154, 95], [208, 178, 79], [253, 205, 42],
  ],
};

const CACHE = new Map<ColorMapName, Uint8Array>();

/**
 * A 256 x 3 table, flattened, for `name`.  Endpoint 0 is the map's darkest
 * colour and endpoint 255 its brightest; the tables are cached because the
 * time slider asks for one on every frame.
 */
export function makeLut(name: ColorMapName): Uint8Array {
  const cached = CACHE.get(name);
  if (cached) return cached;
  const anchors = ANCHORS[name] ?? ANCHORS.gray;
  const lut = new Uint8Array(256 * 3);
  const segments = anchors.length - 1;
  for (let i = 0; i < 256; i++) {
    const position = (i / 255) * segments;
    const lower = Math.min(Math.floor(position), segments - 1);
    const frac = position - lower;
    const a = anchors[lower];
    const b = anchors[lower + 1];
    for (let c = 0; c < 3; c++) {
      lut[i * 3 + c] = Math.round(a[c] + (b[c] - a[c]) * frac);
    }
  }
  CACHE.set(name, lut);
  return lut;
}

/** `#rrggbb` for one entry, for legends and swatches. */
export function lutColor(name: ColorMapName, value: number): string {
  const lut = makeLut(name);
  const i = Math.max(0, Math.min(255, Math.round(value))) * 3;
  const hex = (v: number) => v.toString(16).padStart(2, '0');
  return `#${hex(lut[i])}${hex(lut[i + 1])}${hex(lut[i + 2])}`;
}

/**
 * Map a grayscale-plus-alpha RGBA buffer through a LUT in place-ish: the
 * source's red channel is the value and its alpha the validity mask, and the
 * result is a new RGBA buffer ready for `createImageBitmap`.
 */
export function applyLut(src: Uint8ClampedArray, name: ColorMapName): Uint8ClampedArray {
  const lut = makeLut(name);
  const out = new Uint8ClampedArray(src.length);
  for (let p = 0; p < src.length; p += 4) {
    const v = src[p] * 3;
    out[p] = lut[v];
    out[p + 1] = lut[v + 1];
    out[p + 2] = lut[v + 2];
    out[p + 3] = src[p + 3];
  }
  return out;
}
