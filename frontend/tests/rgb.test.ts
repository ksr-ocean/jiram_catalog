/**
 * Bands and colour composites: the stretch state, the request each mode
 * makes, and the browser-side composition the strips viewer needs.
 *
 * The two composites take different routes on purpose.  A stack has its three
 * bands in one file, so the contract gives it `frame/{t}/rgb.png` and the
 * server does the work; a strip has `image.png?band=` and nothing else, so
 * the three images are fetched and combined here.  Both end at one RGBA
 * buffer with a band per channel, and `ImageView` draws it without a LUT.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '../src/api/client';
import type { ImagePayload, StackMeta, StretchField } from '../src/api/types';
import {
  bandForChannel,
  hasRgb,
  isByNormStretch,
  isPerBandStretch,
  normLabel,
  normName,
  resolveStretch,
  stretchFor,
  uniqueBands,
} from '../src/lib/bands';
import { composePayloads, composeRgb } from '../src/lib/rgb';
import { compositeStretch, useStore } from '../src/store/store';

/** The per-band map a backend from before the photometry amendment sends. */
const PER_BAND: StretchField = { RED: { p1: 1, p99: 9 }, GREEN: { p1: 2, p99: 8 }, BLUE: { p1: 3, p99: 7 } };

const META: StackMeta = {
  id: 'north_pole_paper/junocam_RED-GREEN-BLUE_orbits4_frame',
  region: 'north_pole_paper',
  band: 'RGB',
  level: 'frame',
  instrument: 'JunoCam',
  bands: ['RED', 'GREEN', 'BLUE'],
  km_per_px: 15,
  x_km: [0, 1],
  y_km: [0, 1],
  shape: [4, 4],
  times: ['a', 'b'],
  per_time: [{ i: 0 }, { i: 1 }],
  stretch: PER_BAND,
  graticule: { type: 'FeatureCollection', features: [] },
};

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
}

/** An `n`-pixel grayscale-plus-alpha buffer: value in red, validity in alpha. */
function gray(values: number[], alphas: number[]): Uint8ClampedArray {
  const out = new Uint8ClampedArray(values.length * 4);
  values.forEach((value, i) => {
    out[4 * i] = value;
    out[4 * i + 3] = alphas[i];
  });
  return out;
}

function payload(data: Uint8ClampedArray, width = 2, height = 1): ImagePayload {
  return { bitmap: null as unknown as ImageBitmap, gray: data, width, height, bounds: [0, 1, 0, 1], stride: 1 };
}

describe('per-band stretch', () => {
  it('tells one pair from a pair per band', () => {
    expect(isPerBandStretch({ p1: 0, p99: 1 })).toBe(false);
    expect(isPerBandStretch(PER_BAND)).toBe(true);
    expect(isPerBandStretch(null)).toBe(false);
  });

  it('resolves a band to its own pair, and an unknown band to the first', () => {
    expect(resolveStretch(PER_BAND, 'GREEN')).toEqual({ p1: 2, p99: 8 });
    expect(resolveStretch(PER_BAND, 'green')).toEqual({ p1: 2, p99: 8 });
    expect(resolveStretch(PER_BAND, 'METHANE')).toEqual({ p1: 1, p99: 9 });
    expect(resolveStretch(PER_BAND, null)).toEqual({ p1: 1, p99: 9 });
  });

  it('gives a single-band stack the one pair whatever band is asked for', () => {
    expect(resolveStretch({ p1: 4, p99: 6 }, 'M')).toEqual({ p1: 4, p99: 6 });
    expect(resolveStretch(undefined, 'M')).toEqual({ p1: 0, p99: 1 });
  });

  it('deduplicates a band list, whatever shape the backend sends it in', () => {
    expect(uniqueBands(['RED', 'GREEN', 'BLUE'])).toEqual(['RED', 'GREEN', 'BLUE']);
    // A backend that reports one entry per time step must not fill the
    // selector with hundreds of copies of the same band.
    expect(uniqueBands(Array(294).fill('M'))).toEqual(['M']);
    expect(uniqueBands(['RED', 'red', '', ' '])).toEqual(['RED']);
    expect(uniqueBands(null)).toEqual([]);
  });

  it('knows when a composite is possible and which band is which channel', () => {
    expect(hasRgb(['RED', 'GREEN', 'BLUE'])).toBe(true);
    expect(hasRgb(['red', 'green', 'blue', 'METHANE'])).toBe(true);
    expect(hasRgb(['RED', 'GREEN'])).toBe(false);
    expect(hasRgb(null)).toBe(false);
    expect(bandForChannel(['RED', 'GREEN', 'BLUE'], 'g')).toBe('GREEN');
    expect(bandForChannel(['L', 'M'], 'r')).toBeNull();
  });

  it('sends one pair to all three channels when the bands are linked', () => {
    const state = {
      stackBands: ['RED', 'GREEN', 'BLUE'],
      linkBands: true,
      bandStretch: { RED: [1, 9] as [number, number], GREEN: [2, 8] as [number, number], BLUE: [3, 7] as [number, number] },
      vmin: 0,
      vmax: 100,
    };
    expect(compositeStretch(state)).toEqual({ r: [0, 100], g: [0, 100], b: [0, 100] });
    expect(compositeStretch({ ...state, linkBands: false })).toEqual({ r: [1, 9], g: [2, 8], b: [3, 7] });
  });

  it('falls back to the shared pair for a band with no stretch of its own', () => {
    expect(
      compositeStretch({ stackBands: ['RED', 'GREEN', 'BLUE'], linkBands: false, bandStretch: {}, vmin: 5, vmax: 6 }),
    ).toEqual({ r: [5, 6], g: [5, 6], b: [5, 6] });
  });
});

describe('illumination normalisation (the photometry amendment)', () => {
  /** What a JunoCam product sends now: a per-band map per normalisation. */
  const BY_NORM = {
    none: PER_BAND,
    lambert: { RED: { p1: 10, p99: 90 }, GREEN: { p1: 20, p99: 80 }, BLUE: { p1: 30, p99: 70 } },
  };

  it('tells a per-norm map from a per-band one and from a single pair', () => {
    expect(isByNormStretch(BY_NORM)).toBe(true);
    expect(isByNormStretch(PER_BAND)).toBe(false);
    expect(isByNormStretch({ p1: 0, p99: 1 })).toBe(false);
    expect(isByNormStretch(null)).toBe(false);
    expect(isByNormStretch({})).toBe(false);
  });

  it('reads a band under the norm asked for, and falls back to the first', () => {
    expect(stretchFor(BY_NORM, 'lambert', 'GREEN')).toEqual({ p1: 20, p99: 80 });
    expect(stretchFor(BY_NORM, 'none', 'GREEN')).toEqual({ p1: 2, p99: 8 });
    // A norm the metadata has no entry for at all takes the first, which is
    // `none`: the server computes its own limits either way, and the raw
    // pair is the conservative starting point.
    expect(stretchFor(BY_NORM, 'flat:32', 'RED')).toEqual({ p1: 1, p99: 9 });
    // ...but the same model at another parameter matches the model, because
    // Minnaert at k = 0.8 looks far more like k = 0.7 than like the raw image.
    expect(stretchFor({ none: PER_BAND, 'minnaert:0.7': BY_NORM.lambert }, 'minnaert:0.9', 'RED')).toEqual({
      p1: 10,
      p99: 90,
    });
    // A backend from before the amendment sends no norm level at all.
    expect(stretchFor(PER_BAND, 'lambert', 'BLUE')).toEqual({ p1: 3, p99: 7 });
    expect(stretchFor(undefined, 'lambert', 'BLUE')).toEqual({ p1: 0, p99: 1 });
  });

  it('spells a norm and its parameter the way the API parses them', () => {
    expect(normLabel('none', 0.7, 32)).toBe('none');
    expect(normLabel('lambert', 0.7, 32)).toBe('lambert');
    expect(normLabel('minnaert', 0.8, 32)).toBe('minnaert:0.8');
    expect(normLabel('flat', 0.7, 64)).toBe('flat:64');
    expect(normName('minnaert:0.8')).toBe('minnaert');
    expect(normName('LAMBERT')).toBe('lambert');
    expect(normName(undefined)).toBe('none');
    expect(normName('chartreuse')).toBe('none');
  });

  it('a stack opens on its own default and re-reads the stretch when it changes', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ ...META, norm_default: 'lambert', stretch: BY_NORM })),
    );
    await useStore.getState().openStack(META.id);
    expect(useStore.getState().norm).toBe('lambert');
    expect([useStore.getState().vmin, useStore.getState().vmax]).toEqual([10, 90]);
    expect(useStore.getState().bandStretch).toEqual({ RED: [10, 90], GREEN: [20, 80], BLUE: [30, 70] });
    // A JunoCam composite starts unlinked: three colour strips do not share
    // a stretch without tinting the picture.
    expect(useStore.getState().linkBands).toBe(false);

    useStore.getState().setNorm('none');
    expect(useStore.getState().norm).toBe('none');
    expect([useStore.getState().vmin, useStore.getState().vmax]).toEqual([1, 9]);
    expect(useStore.getState().bandStretch).toEqual({ RED: [1, 9], GREEN: [2, 8], BLUE: [3, 7] });
  });

  it('carries the norm and the mapping on every request it makes', () => {
    const frame = api.stackFrameUrl('r/s', 3, 1, 2, 800, 'GREEN', 'minnaert:0.8', 'asinh');
    expect(frame).toContain('norm=minnaert%3A0.8');
    expect(frame).toContain('stretch=asinh');
    const rgb = api.stackRgbUrl('r/s', 2, { r: [1, 9], g: [2, 8], b: [3, 7] }, 900, 'lambert', 'linear');
    expect(rgb).toContain('norm=lambert');
    const strip = api.stripImageUrl('s1', 0, 1, 800, 'BLUE', 'flat:64', 'asinh');
    expect(strip).toContain('norm=flat%3A64');
    expect(strip).toContain('stretch=asinh');
    // An omitted norm means "whatever the product's default is", so nothing
    // is sent rather than a guess.
    expect(api.stackFrameUrl('r/s', 3, 1, 2, 800, 'GREEN')).not.toContain('norm=');
  });
});

describe('the requests each mode makes', () => {
  it('names the band on a single-band request and omits it when there is none', () => {
    expect(api.stackFrameUrl('r/s', 3, 1, 2, 800, 'GREEN')).toContain('band=GREEN');
    expect(api.stackFrameUrl('r/s', 3, 1, 2, 800)).not.toContain('band=');
    expect(api.stackFrameUrl('r/s', 3, 1, 2, 800, null)).not.toContain('band=');
  });

  it('sends six stretch bounds to the composite endpoint', () => {
    const target = api.stackRgbUrl('r/s', 2, { r: [1, 9], g: [2, 8], b: [3, 7] }, 900);
    expect(target).toContain('/api/stacks/r/s/frame/2/rgb.png');
    for (const pair of ['vmin_r=1', 'vmax_r=9', 'vmin_g=2', 'vmax_g=8', 'vmin_b=3', 'vmax_b=7', 'max_px=900']) {
      expect(target).toContain(pair);
    }
  });

  it('asks a strip for one band at a time, and for that band\'s statistics', () => {
    expect(api.stripImageUrl('s1', 0, 1, 800, 'BLUE')).toContain('band=BLUE');
    expect(api.stripStats).toBeTypeOf('function');
  });
});

describe('composing three bands in the browser', () => {
  it('puts each band in its channel', () => {
    const out = composeRgb(gray([10, 20], [255, 255]), gray([30, 40], [255, 255]), gray([50, 60], [255, 255]));
    expect([...out.slice(0, 4)]).toEqual([10, 30, 50, 255]);
    expect([...out.slice(4, 8)]).toEqual([20, 40, 60, 255]);
  });

  it('keeps a pixel any one band saw, and zeroes the channels that saw nothing', () => {
    const out = composeRgb(gray([10, 0], [255, 0]), gray([30, 40], [0, 255]), gray([50, 0], [255, 0]));
    // First pixel: red and blue valid, green not -- green reads zero, alpha 255.
    expect([...out.slice(0, 4)]).toEqual([10, 0, 50, 255]);
    // Second pixel: only green -- still drawn, because it is real data.
    expect([...out.slice(4, 8)]).toEqual([0, 40, 0, 255]);
  });

  it('drops a pixel no band saw', () => {
    const out = composeRgb(gray([9], [0]), gray([9], [0]), gray([9], [0]));
    expect(out[3]).toBe(0);
  });

  it('refuses to compose three images the server sized differently', () => {
    const a = payload(gray([1, 2], [255, 255]));
    const b = payload(gray([1, 2], [255, 255]));
    const c = payload(gray([1], [255]), 1, 1);
    expect(composePayloads(a, b, c)).toBeNull();
    const composed = composePayloads(a, b, payload(gray([3, 4], [255, 255])));
    expect(composed?.width).toBe(2);
    expect(composed?.bounds).toEqual([0, 1, 0, 1]);
  });
});

describe('the stack viewer\'s band state', () => {
  beforeEach(() => {
    useStore.setState({
      stackId: null,
      stackMeta: null,
      stackBands: [],
      band: null,
      composite: false,
      linkBands: true,
      bandStretch: {},
      toasts: [],
    });
    // Every image request fails in node; the state under test is set before
    // the fetch and the store turns the failure into a toast, not a throw.
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(META)));
  });

  it('opens on the first band, in single-band mode, with a pair per band', async () => {
    await useStore.getState().openStack(META.id);
    const state = useStore.getState();
    expect(state.stackBands).toEqual(['RED', 'GREEN', 'BLUE']);
    expect(state.band).toBe('RED');
    expect(state.composite).toBe(false);
    expect(state.vmin).toBe(1);
    expect(state.vmax).toBe(9);
    expect(state.bandStretch).toEqual({ RED: [1, 9], GREEN: [2, 8], BLUE: [3, 7] });
  });

  it('switching band moves the stretch with it', async () => {
    await useStore.getState().openStack(META.id);
    useStore.getState().setBand('BLUE');
    expect(useStore.getState().band).toBe('BLUE');
    expect([useStore.getState().vmin, useStore.getState().vmax]).toEqual([3, 7]);
  });

  it('the composite is a mode, and the band survives leaving it', async () => {
    await useStore.getState().openStack(META.id);
    useStore.getState().setBand('GREEN');
    useStore.getState().setComposite(true);
    expect(useStore.getState().composite).toBe(true);
    expect(useStore.getState().band).toBe('GREEN');
    useStore.getState().setComposite(false);
    expect(useStore.getState().composite).toBe(false);
  });

  it('an edited stretch is remembered per band', async () => {
    await useStore.getState().openStack(META.id);
    useStore.getState().setStretch(0, 5);
    expect(useStore.getState().bandStretch.RED).toEqual([0, 5]);
    useStore.getState().setBandStretch('BLUE', 1, 2);
    expect(useStore.getState().bandStretch.BLUE).toEqual([1, 2]);
    useStore.getState().setLinkBands(false);
    expect(compositeStretch(useStore.getState())).toEqual({ r: [0, 5], g: [2, 8], b: [1, 2] });
  });

  it('a single-band stack has no band selector state at all', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ ...META, bands: [], stretch: { p1: 0, p99: 1 } })));
    await useStore.getState().openStack('north_pole_paper/M_orbits4_sequence');
    expect(useStore.getState().stackBands).toEqual([]);
    expect(useStore.getState().band).toBeNull();
    expect([useStore.getState().vmin, useStore.getState().vmax]).toEqual([0, 1]);
  });
});
