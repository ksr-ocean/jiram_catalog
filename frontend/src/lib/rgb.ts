/**
 * Colour composites in the browser.
 *
 * A stack has a server-side composite -- the contract's
 * `frame/{t}/rgb.png` -- because the three bands live in one file and the
 * server is already reading it.  A strip does not: the amendment gives
 * `image.png?band=` and nothing else, so the three band images are fetched
 * and combined here.  Both paths end at the same place, an RGBA buffer with
 * one band per channel, which `ImageView` draws without a colour map.
 *
 * The alpha channel is the union of the three validity masks rather than
 * their intersection: an edge pixel that one filter saw and another did not
 * is real data, and dropping it would eat a band-wide strip off every edge of
 * the composite where the three swaths do not quite overlap.
 */
import type { ImagePayload } from '../api/types';

/** Combine three grayscale-plus-alpha buffers into one RGBA buffer. */
export function composeRgb(
  red: Uint8ClampedArray,
  green: Uint8ClampedArray,
  blue: Uint8ClampedArray,
): Uint8ClampedArray {
  const length = Math.min(red.length, green.length, blue.length);
  const out = new Uint8ClampedArray(length);
  for (let p = 0; p < length; p += 4) {
    const alpha = Math.max(red[p + 3], green[p + 3], blue[p + 3]);
    out[p] = red[p + 3] > 0 ? red[p] : 0;
    out[p + 1] = green[p + 3] > 0 ? green[p] : 0;
    out[p + 2] = blue[p + 3] > 0 ? blue[p] : 0;
    out[p + 3] = alpha;
  }
  return out;
}

/**
 * The three payloads as one.
 *
 * Null when the three do not agree on a size, which would mean the server
 * strided them differently and the composite would be three misaligned
 * pictures rather than one; the caller falls back to a single band and says so.
 */
export function composePayloads(
  red: ImagePayload,
  green: ImagePayload,
  blue: ImagePayload,
): ImagePayload | null {
  if (red.width !== green.width || red.width !== blue.width) return null;
  if (red.height !== green.height || red.height !== blue.height) return null;
  return {
    bitmap: red.bitmap,
    gray: composeRgb(red.gray, green.gray, blue.gray),
    width: red.width,
    height: red.height,
    bounds: red.bounds,
    stride: red.stride,
  };
}
