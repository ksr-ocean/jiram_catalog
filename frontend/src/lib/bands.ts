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
import type { StretchField, Stretch } from '../api/types';

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
