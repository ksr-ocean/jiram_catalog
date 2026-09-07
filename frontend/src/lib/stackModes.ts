/**
 * The three ways to watch a polar region, and how one of them finds the others.
 *
 * A region stack is written as `<band>_orbits<token>_<level>.nc`, so three
 * files that differ only in their level are three views of the same frames:
 * the snapshot per spin sequence, the same sweep filling in frame by frame,
 * and the raw instrument frames.  The backend says so in each listing's
 * `siblings`; everything here also works without it, because the naming
 * convention is the same one `stacks.stack_output_path` writes and the mode
 * selector must not go blank against an older server.
 */
import type { PerTimeRecord, StackListing, StackMeta } from '../api/types';

/** Level order: the model's input first, then the sweep, then the raw frames. */
export const STACK_LEVELS = ['sequence', 'cumulative', 'frame'] as const;
export type StackLevel = (typeof STACK_LEVELS)[number];

export const LEVEL_LABELS: Record<StackLevel, string> = {
  sequence: 'Region snapshots',
  cumulative: 'Accumulating sweep',
  frame: 'Instrument frames',
};

/** One sentence each, shown under the stack list. */
export const LEVEL_BLURBS: Record<StackLevel, string> = {
  sequence:
    'Region snapshots: one averaged snapshot per spin sequence, which is what the velocity model reads.',
  cumulative:
    'Accumulating sweep: the same sweep filling in frame by frame, emptied again at the next sequence.',
  frame:
    'Instrument frames: one step per raw frame, in the order the imager recorded them (a JIRAM frame, or a whole JunoCam swath).',
};

export function isStackLevel(level: string | null | undefined): level is StackLevel {
  return typeof level === 'string' && (STACK_LEVELS as readonly string[]).includes(level);
}

/** The browser's name for a level; an unknown level names itself. */
export function levelLabel(level: string | null | undefined): string {
  if (!level) return '';
  return isStackLevel(level) ? LEVEL_LABELS[level] : level;
}

/**
 * `M_orbits4_frame` -> band, orbit token and level; null if it is not one.
 *
 * The split is on the literal `_orbits` rather than a character class,
 * because a JunoCam stack names its bands as well as its instrument
 * (`junocam_RED-GREEN-BLUE_orbits4_frame`) and a band token with an
 * underscore in it would otherwise stop the mode selector resolving anything.
 */
export function parseStem(stem: string): { band: string; orbits: string; level: StackLevel } | null {
  const marker = stem.indexOf('_orbits');
  if (marker <= 0) return null;
  const band = stem.slice(0, marker);
  const rest = stem.slice(marker + '_orbits'.length);
  const cut = rest.lastIndexOf('_');
  if (cut <= 0) return null;
  const level = rest.slice(cut + 1).toLowerCase();
  if (!isStackLevel(level)) return null;
  return { band: band.toUpperCase(), orbits: rest.slice(0, cut).toLowerCase(), level };
}

/**
 * The modes an instrument can have at all.
 *
 * JIRAM sweeps a region frame by frame, so a sequence snapshot and an
 * accumulating sweep are meaningful views of the same frames.  A JunoCam
 * image is already the whole swath, so `frame` is the only level its stacks
 * come in, and offering the other two would offer to build files that cannot
 * exist.
 */
export function availableLevels(instrument: string | null | undefined): StackLevel[] {
  return (instrument ?? '').toUpperCase() === 'JUNOCAM' ? ['frame'] : [...STACK_LEVELS];
}

/**
 * What makes two stacks two views of one thing: region directory, band, orbits.
 *
 * The region is the directory the id is built from rather than the `region`
 * attribute, so a sibling is always a stack the viewer can actually open.
 */
export function familyKey(id: string): string | null {
  const cut = id.lastIndexOf('/');
  if (cut < 0) return null;
  const parsed = parseStem(id.slice(cut + 1));
  return parsed ? `${id.slice(0, cut)}|${parsed.band}|${parsed.orbits}` : null;
}

/** Sibling ids by level -- the server's answer, or the naming convention's. */
export function siblingsOf(
  stack: StackListing | null | undefined,
  stacks: readonly StackListing[],
): Partial<Record<StackLevel, string>> {
  if (!stack) return {};
  if (stack.siblings && Object.keys(stack.siblings).length > 0) {
    const known: Partial<Record<StackLevel, string>> = {};
    for (const [level, id] of Object.entries(stack.siblings)) {
      if (isStackLevel(level) && typeof id === 'string') known[level] = id;
    }
    if (Object.keys(known).length > 0) return known;
  }
  const key = familyKey(stack.id);
  if (key === null) return {};
  const resolved: Partial<Record<StackLevel, string>> = {};
  for (const candidate of stacks) {
    if (familyKey(candidate.id) !== key) continue;
    const parsed = parseStem(candidate.id.slice(candidate.id.lastIndexOf('/') + 1));
    if (parsed && resolved[parsed.level] === undefined) resolved[parsed.level] = candidate.id;
  }
  return resolved;
}

/**
 * The orbits behind a stack id, for building the level it is missing.
 *
 * `all` means every orbit, which the build request says by leaving `orbits`
 * out; anything the token cannot express as numbers is left out too, and the
 * server falls back to the same "every orbit" it would for `all`.
 */
export function orbitsOf(id: string): number[] | undefined {
  const parsed = parseStem(id.slice(id.lastIndexOf('/') + 1));
  if (!parsed || parsed.orbits === 'all') return undefined;
  const orbits = new Set<number>();
  for (const piece of parsed.orbits.split('_')) {
    const range = /^(\d+)-(\d+)$/.exec(piece);
    if (range) {
      const [start, stop] = [Number(range[1]), Number(range[2])];
      if (start > stop) return undefined;
      for (let orbit = start; orbit <= stop; orbit++) orbits.add(orbit);
    } else if (/^\d+$/.test(piece)) {
      orbits.add(Number(piece));
    } else {
      return undefined;
    }
  }
  return orbits.size > 0 ? [...orbits].sort((a, b) => a - b) : undefined;
}

/** Sweep ordinals: sequence ids numbered from one in order of first appearance. */
export function sweepNumbers(perTime: readonly PerTimeRecord[]): Map<string, number> {
  const ordinals = new Map<string, number>();
  for (const record of perTime) {
    const name = record.seq_id ?? '';
    if (!ordinals.has(name)) ordinals.set(name, ordinals.size + 1);
  }
  return ordinals;
}

/**
 * "sweep 3, frame 5 of 12" for a cumulative stack, or null for the others.
 *
 * `seq_index` and `seq_n` are the cumulative stack's own coordinates, so the
 * readout says nothing a frame or sequence stack cannot back up.
 */
export function sweepReadout(meta: StackMeta | null | undefined, t: number): string | null {
  if (!meta || meta.level !== 'cumulative') return null;
  const record = meta.per_time?.[t];
  if (!record || record.seq_index === undefined || record.seq_index === null) return null;
  if (record.seq_n === undefined || record.seq_n === null) return null;
  const sweep = sweepNumbers(meta.per_time).get(record.seq_id ?? '') ?? 1;
  return `sweep ${sweep}, frame ${record.seq_index + 1} of ${record.seq_n}`;
}
