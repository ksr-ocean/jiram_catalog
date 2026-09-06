/** The three viewing modes: labels, sibling resolution, orbits, readout. */
import { describe, expect, it } from 'vitest';
import type { StackListing, StackMeta } from '../src/api/types';
import {
  LEVEL_LABELS,
  STACK_LEVELS,
  familyKey,
  levelLabel,
  orbitsOf,
  parseStem,
  siblingsOf,
  sweepNumbers,
  sweepReadout,
} from '../src/lib/stackModes';

function listing(id: string, level: string, extra: Partial<StackListing> = {}): StackListing {
  return {
    id,
    region: id.slice(0, id.indexOf('/')),
    band: 'M',
    level,
    path: `/mirror/regions/${id}.nc`,
    n_time: 25,
    shape: [3000, 3200],
    km_per_px: 15,
    size_bytes: 1,
    has_movie: false,
    ...extra,
  };
}

const FAMILY = [
  listing('north_pole_paper/M_orbits4_sequence', 'sequence'),
  listing('north_pole_paper/M_orbits4_cumulative', 'cumulative'),
  listing('north_pole_paper/M_orbits4_frame', 'frame'),
];

describe('label mapping', () => {
  it('names the three levels the way the backend does', () => {
    expect(levelLabel('sequence')).toBe('Region snapshots');
    expect(levelLabel('cumulative')).toBe('Accumulating sweep');
    expect(levelLabel('frame')).toBe('Instrument frames');
    expect(STACK_LEVELS.map((level) => LEVEL_LABELS[level])).toEqual([
      'Region snapshots',
      'Accumulating sweep',
      'Instrument frames',
    ]);
  });

  it('lets an unknown level name itself rather than going blank', () => {
    expect(levelLabel('mosaic')).toBe('mosaic');
    expect(levelLabel(null)).toBe('');
    expect(levelLabel(undefined)).toBe('');
  });
});

describe('stem parsing', () => {
  it('splits band, orbit token and level', () => {
    expect(parseStem('M_orbits4_frame')).toEqual({ band: 'M', orbits: '4', level: 'frame' });
    expect(parseStem('L_orbits4_5_6_cumulative')?.orbits).toBe('4_5_6');
    expect(parseStem('M_orbitsall_sequence')?.orbits).toBe('all');
  });

  it('refuses a stem that is not one of the three levels', () => {
    expect(parseStem('scratch')).toBeNull();
    expect(parseStem('M_orbits4_mosaic')).toBeNull();
    expect(familyKey('M_orbits4_frame')).toBeNull(); // no region directory
    expect(familyKey('north_pole_paper/M_orbits4_frame')).toBe('north_pole_paper|M|4');
  });
});

describe('sibling resolution', () => {
  it('prefers what the server said', () => {
    const served = listing('north_pole_paper/M_orbits4_sequence', 'sequence', {
      siblings: {
        frame: 'north_pole_paper/M_orbits4_frame',
        cumulative: 'north_pole_paper/M_orbits4_cumulative',
        sequence: 'north_pole_paper/M_orbits4_sequence',
      },
    });
    expect(siblingsOf(served, [])).toEqual({
      sequence: 'north_pole_paper/M_orbits4_sequence',
      cumulative: 'north_pole_paper/M_orbits4_cumulative',
      frame: 'north_pole_paper/M_orbits4_frame',
    });
  });

  it('drops a level the server invented', () => {
    const served = listing('north_pole_paper/M_orbits4_sequence', 'sequence', {
      siblings: { mosaic: 'north_pole_paper/M_orbits4_mosaic' },
    });
    expect(siblingsOf(served, [])).toEqual({});
  });

  it('falls back to the naming convention when the server is silent', () => {
    expect(siblingsOf(FAMILY[0], FAMILY)).toEqual({
      sequence: 'north_pole_paper/M_orbits4_sequence',
      cumulative: 'north_pole_paper/M_orbits4_cumulative',
      frame: 'north_pole_paper/M_orbits4_frame',
    });
  });

  it('keeps other regions, bands and orbit sets out of the family', () => {
    const others = [
      ...FAMILY,
      listing('south_pole/M_orbits4_frame', 'frame'),
      listing('north_pole_paper/L_orbits4_frame', 'frame', { band: 'L' }),
      listing('north_pole_paper/M_orbits7_frame', 'frame'),
      listing('north_pole_paper/scratch', 'frame'),
    ];
    expect(siblingsOf(FAMILY[1], others)).toEqual({
      sequence: 'north_pole_paper/M_orbits4_sequence',
      cumulative: 'north_pole_paper/M_orbits4_cumulative',
      frame: 'north_pole_paper/M_orbits4_frame',
    });
    expect(siblingsOf(listing('north_pole_paper/scratch', 'frame'), others)).toEqual({});
    expect(siblingsOf(null, others)).toEqual({});
  });
});

describe('orbits behind a stack', () => {
  it('reads the token the builder wrote', () => {
    expect(orbitsOf('north_pole_paper/M_orbits4_frame')).toEqual([4]);
    expect(orbitsOf('north_pole_paper/M_orbits4_5_6_frame')).toEqual([4, 5, 6]);
    expect(orbitsOf('north_pole_paper/M_orbits4-6_frame')).toEqual([4, 5, 6]);
  });

  it('says "every orbit" by leaving the field out', () => {
    expect(orbitsOf('north_pole_paper/M_orbitsall_frame')).toBeUndefined();
    expect(orbitsOf('north_pole_paper/scratch')).toBeUndefined();
  });
});

describe('the cumulative time readout', () => {
  const meta = (level: string): StackMeta => ({
    id: 'north_pole_paper/M_orbits4_cumulative',
    region: 'north_pole_paper',
    band: 'M',
    level,
    km_per_px: 15,
    x_km: [0, 1],
    y_km: [0, 1],
    shape: [4, 4],
    times: ['a', 'b', 'c', 'd'],
    per_time: [
      { i: 0, seq_id: 's1', seq_index: 0, seq_n: 2 },
      { i: 1, seq_id: 's1', seq_index: 1, seq_n: 2 },
      { i: 2, seq_id: 's2', seq_index: 0, seq_n: 2 },
      { i: 3, seq_id: 's2', seq_index: 1, seq_n: 2 },
    ],
    stretch: { p1: 0, p99: 1 },
    graticule: { type: 'FeatureCollection', features: [] },
  });

  it('numbers the sweeps from one in the order they appear', () => {
    expect([...sweepNumbers(meta('cumulative').per_time)]).toEqual([
      ['s1', 1],
      ['s2', 2],
    ]);
  });

  it('says which frame of which sweep is on screen', () => {
    expect(sweepReadout(meta('cumulative'), 0)).toBe('sweep 1, frame 1 of 2');
    expect(sweepReadout(meta('cumulative'), 3)).toBe('sweep 2, frame 2 of 2');
  });

  it('says nothing for the other two levels, or past the end', () => {
    expect(sweepReadout(meta('sequence'), 0)).toBeNull();
    expect(sweepReadout(meta('frame'), 0)).toBeNull();
    expect(sweepReadout(meta('cumulative'), 9)).toBeNull();
    expect(sweepReadout(null, 0)).toBeNull();
  });
});
