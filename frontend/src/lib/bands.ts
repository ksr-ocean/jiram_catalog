/**
 * Instruments, band names, quality tiers: the vocabulary the amendment of
 * 2026-09-07 added to the contract, in one place.
 *
 * A catalog row carries its bands as one string -- the detector half for
 * JIRAM, the filter order joined by `;` for JunoCam -- because that is what
 * `frames.arrow` sends and because a string column costs one allocation per
 * row instead of one array per row at 47,000 rows.  `bandsInclude` therefore
 * scans the string for a whole token rather than splitting it, so a filter
 * pass over the whole catalog allocates nothing.
 */
import type { NormName, Stretch, StretchByNorm, StretchField } from '../api/types';

export type Instrument = 'JIRAM' | 'JunoCam';

export const INSTRUMENTS: Instrument[] = ['JIRAM', 'JunoCam'];

/** JIRAM's two detector halves, and JunoCam's four filters. */
export const JIRAM_BANDS = ['L', 'M'];
export const JUNOCAM_BANDS = ['RED', 'GREEN', 'BLUE', 'METHANE'];

/** The three that make a colour composite, in channel order. */
export const RGB_BANDS = ['RED', 'GREEN', 'BLUE'] as const;
export type RgbChannel = 'r' | 'g' | 'b';

/** Quality tiers, best first; the contract's `quality_min` names the worst kept. */
export const QUALITY_TIERS = ['A', 'B', 'C'] as const;
export type QualityTier = (typeof QUALITY_TIERS)[number];

/** How the tier filter reads in the toolbar. */
export const QUALITY_LABELS: Record<QualityTier, string> = {
  A: 'A only',
  B: 'A + B',
  C: 'all',
};

/** `A` -> 0, `B` -> 1, `C` -> 2; anything else -> -1, which never fails a test. */
export function qualityRank(tier: string | null | undefined): number {
  if (!tier) return -1;
  const index = (QUALITY_TIERS as readonly string[]).indexOf(tier.trim().toUpperCase());
  return index;
}

/**
 * Whether a row of tier `tier` survives `quality_min`.
 *
 * The contract hides tier `C` unless `quality_min=C`, and a row whose tier is
 * unknown is kept -- the same "a threshold excludes only what it knows about"
 * rule the rest of the filters follow.
 */
export function qualityPasses(tier: string | null | undefined, minimum: QualityTier): boolean {
  const rank = qualityRank(tier);
  if (rank < 0) return true;
  return rank <= qualityRank(minimum);
}

/** The bands of one row, as names: `RED;GREEN;BLUE` or `["RED", ...]` or `M`. */
export function splitBands(field: unknown): string[] {
  if (field === null || field === undefined) return [];
  if (Array.isArray(field)) return field.map((name) => String(name)).filter(Boolean);
  // An Arrow list value is neither a string nor an array; it has `toArray`.
  if (typeof field === 'object' && typeof (field as { toArray?: unknown }).toArray === 'function') {
    return Array.from((field as { toArray: () => ArrayLike<unknown> }).toArray(), (name) => String(name)).filter(
      Boolean,
    );
  }
  const text = String(field);
  if (text === '') return [];
  return text
    .split(/[;,]/)
    .map((name) => name.trim())
    .filter(Boolean);
}

/**
 * Whether a `;`-joined band field contains `want`, without allocating.
 *
 * The comparison is case-insensitive on the ASCII names the contract uses,
 * and it matches whole tokens only, so `RED` never matches `INFRARED`.
 */
export function bandsInclude(field: string, want: string): boolean {
  if (!want) return true;
  if (!field) return false;
  const haystack = field.toUpperCase();
  const needle = want.toUpperCase();
  let from = 0;
  for (;;) {
    const at = haystack.indexOf(needle, from);
    if (at < 0) return false;
    const before = at === 0 ? ';' : haystack[at - 1];
    const afterIndex = at + needle.length;
    const after = afterIndex >= haystack.length ? ';' : haystack[afterIndex];
    if ((before === ';' || before === ',') && (after === ';' || after === ',')) return true;
    from = at + 1;
  }
}

/**
 * A band list with its duplicates removed, order preserved.
 *
 * The contract's `bands` is the set of band names a stack or strip carries,
 * but a backend that reports one entry per time step instead would otherwise
 * fill the band selector with hundreds of copies of the same name; deduping
 * costs nothing and makes the selector right either way.
 */
export function uniqueBands(bands: readonly string[] | null | undefined): string[] {
  if (!bands) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const band of bands) {
    const name = String(band ?? '').trim();
    if (!name || seen.has(name.toUpperCase())) continue;
    seen.add(name.toUpperCase());
    out.push(name);
  }
  return out;
}

/** True when RED, GREEN and BLUE are all present, so a composite can be made. */
export function hasRgb(bands: readonly string[] | null | undefined): boolean {
  if (!bands) return false;
  const upper = bands.map((name) => name.toUpperCase());
  return RGB_BANDS.every((name) => upper.includes(name));
}

/** The band a composite draws into a channel, matched case-insensitively. */
export function bandForChannel(bands: readonly string[], channel: RgbChannel): string | null {
  const want = { r: 'RED', g: 'GREEN', b: 'BLUE' }[channel];
  return bands.find((name) => name.toUpperCase() === want) ?? null;
}

// ---------------------------------------------------------------------------
// illumination normalisation (the photometry amendment of 2026-09-07)
// ---------------------------------------------------------------------------
/** Every normalisation the API answers to, in the order the selector shows. */
export const NORM_NAMES: NormName[] = ['none', 'lambert', 'minnaert', 'flat'];

/** How each one reads in the toolbar. */
export const NORM_LABELS: Record<NormName, string> = {
  none: 'None',
  lambert: 'Lambert',
  minnaert: 'Minnaert',
  flat: 'Flatten',
};

/** One line of why, shown as the selector's tooltip. */
export const NORM_BLURBS: Record<NormName, string> = {
  none: 'raw radiance; what the instrument recorded',
  lambert: 'divide by cos(i): the diffuse-surface correction, no parameters',
  minnaert: 'divide by cos(i)^k cos(e)^(k-1): k near 0.7 fits a cloud deck',
  flat: 'divide by the image own Gaussian low-pass: removes any smooth gradient',
};

/** Minnaert's exponent and the flattener's width: the ranges the API accepts. */
export const MINNAERT_K_RANGE: [number, number] = [0.3, 1.2];
export const DEFAULT_MINNAERT_K = 0.7;
export const FLAT_SIGMA_RANGE: [number, number] = [8, 256];
export const DEFAULT_FLAT_SIGMA = 32;

/** The wire spelling of a norm and its parameter: `minnaert:0.7`, `flat:32`. */
export function normLabel(name: string, k: number, sigma: number): string {
  if (name === 'minnaert') return `minnaert:${k}`;
  if (name === 'flat') return `flat:${sigma}`;
  return name;
}

/** The bare name of a wire spelling: `minnaert:0.7` -> `minnaert`. */
export function normName(label: string | null | undefined): NormName {
  const head = String(label ?? 'none').split(':')[0].trim().toLowerCase();
  return (NORM_NAMES as string[]).includes(head) ? (head as NormName) : 'none';
}

/** True when `stretch` is keyed by norm name rather than by band or by `p1`. */
export function isByNormStretch(
  stretch: StretchField | StretchByNorm | null | undefined,
): stretch is StretchByNorm {
  if (!stretch || 'p1' in stretch) return false;
  const values = Object.values(stretch as Record<string, unknown>);
  if (values.length === 0) return false;
  // A per-band map holds `{p1, p99}`; a per-norm map holds another map.
  return values.every((value) => !!value && typeof value === 'object' && !('p1' in (value as object)));
}

/**
 * The per-band stretch map for one normalisation.
 *
 * A backend from before the amendment sends one map with no norm level at
 * all, and its numbers are the unnormalised ones; an unknown norm falls back
 * to whatever the product's default was, which is what the server itself
 * would have used.
 */
export function stretchForNorm(
  stretch: StretchField | StretchByNorm | null | undefined,
  norm: string | null | undefined,
): StretchField | null {
  if (!stretch) return null;
  if (!isByNormStretch(stretch)) return stretch;
  const entries = Object.entries(stretch);
  const wanted = String(norm ?? 'none');
  // Exact spelling first, then the same model at whatever parameter the
  // metadata carries -- `minnaert:0.8` starts from `minnaert:0.7`'s limits,
  // which is a far better guess than the raw image's -- and only then the
  // first entry, which is always `none`.
  const hit =
    entries.find(([name]) => name === wanted) ??
    entries.find(([name]) => normName(name) === normName(wanted)) ??
    entries[0];
  return hit ? hit[1] : null;
}

/** The `{p1, p99}` one band starts at, under one normalisation. */
export function stretchFor(
  stretch: StretchField | StretchByNorm | null | undefined,
  norm: string | null | undefined,
  band?: string | null,
): Stretch {
  return resolveStretch(stretchForNorm(stretch, norm), band);
}

/** True when `stretch` is the amendment's per-band map rather than one pair. */
export function isPerBandStretch(stretch: StretchField | null | undefined): stretch is Record<string, Stretch> {
  if (!stretch) return false;
  return !('p1' in stretch && typeof (stretch as Stretch).p1 === 'number');
}

/**
 * The stretch a band starts at.
 *
 * A single-band stack sends one pair and every band resolves to it; a stack
 * with a `band` dimension sends one pair per band, and an unknown band falls
 * back to the first, which is what the server itself defaults to.
 */
export function resolveStretch(stretch: StretchField | null | undefined, band?: string | null): Stretch {
  const fallback: Stretch = { p1: 0, p99: 1 };
  if (!stretch) return fallback;
  if (!isPerBandStretch(stretch)) return stretch;
  const entries = Object.entries(stretch);
  if (entries.length === 0) return fallback;
  if (band) {
    const hit = entries.find(([name]) => name.toUpperCase() === band.toUpperCase());
    if (hit) return hit[1];
  }
  return entries[0][1];
}

/** The band names on offer for an instrument choice, from what the data holds. */
export function bandOptions(
  byInstrument: Record<string, string[]>,
  instrument: 'all' | Instrument,
): string[] {
  const names = new Set<string>();
  for (const [key, bands] of Object.entries(byInstrument)) {
    if (instrument !== 'all' && key.toUpperCase() !== instrument.toUpperCase()) continue;
    for (const band of bands) names.add(band);
  }
  // JIRAM's halves first, then JunoCam's filters, then anything unexpected.
  const order = [...JIRAM_BANDS, ...JUNOCAM_BANDS];
  return [...names].sort((a, b) => {
    const ia = order.indexOf(a);
    const ib = order.indexOf(b);
    if (ia >= 0 && ib >= 0) return ia - ib;
    if (ia >= 0) return -1;
    if (ib >= 0) return 1;
    return a.localeCompare(b);
  });
}
