/** Selection-store operations, including a save round trip against a fake API. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { columnsFromTable } from '../src/lib/catalogTable';
import { DEFAULT_FILTERS, filterIndices } from '../src/lib/filters';
import { frameKey, selectionStats, useStore } from '../src/store/store';
import { syntheticTable } from './synthetic';

const columns = columnsFromTable(syntheticTable());

function loadCatalogIntoStore() {
  const keyIndex = new Map<string, number>();
  for (let i = 0; i < columns.n; i++) keyIndex.set(frameKey(columns.productId[i], columns.half[i]), i);
  useStore.setState({
    columns,
    keyIndex,
    filters: DEFAULT_FILTERS,
    filtered: filterIndices(columns, DEFAULT_FILTERS),
    selectionKeys: new Set(),
    selectionName: '',
    savedSelections: [],
    toasts: [],
  });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

beforeEach(loadCatalogIntoStore);
afterEach(() => vi.unstubAllGlobals());

describe('working selection', () => {
  it('adds rows by index and keys them by product and half', () => {
    useStore.getState().addToSelection(Uint32Array.of(0, 1));
    expect(useStore.getState().selectionKeys).toEqual(new Set(['P0|L', 'P1|M']));
  });

  it('adding is idempotent', () => {
    useStore.getState().addToSelection(Uint32Array.of(0));
    useStore.getState().addToSelection(Uint32Array.of(0, 2));
    expect(useStore.getState().selectionKeys.size).toBe(2);
  });

  it('removes rows', () => {
    useStore.getState().addToSelection(Uint32Array.of(0, 1, 2));
    useStore.getState().removeFromSelection(Uint32Array.of(1));
    expect([...useStore.getState().selectionKeys]).toEqual(['P0|L', 'P2|L']);
  });

  it('replaces rather than merges', () => {
    useStore.getState().addToSelection(Uint32Array.of(0, 1));
    useStore.getState().replaceSelection(Uint32Array.of(4));
    expect([...useStore.getState().selectionKeys]).toEqual(['P4|L']);
  });

  it('clears', () => {
    useStore.getState().addToSelection(Uint32Array.of(0, 1));
    useStore.getState().clearSelection();
    expect(useStore.getState().selectionKeys.size).toBe(0);
  });

  it('takes everything the filters pass', () => {
    useStore.getState().selectAllFiltered();
    expect(useStore.getState().selectionKeys.size).toBe(columns.n);
  });

  it('summarises frames, orbits, latitude range and band halves', () => {
    useStore.getState().addToSelection(Uint32Array.of(0, 1, 4));
    const stats = selectionStats(columns, useStore.getState().keyIndex, useStore.getState().selectionKeys);
    expect(stats.n).toBe(3);
    expect(stats.orbits).toEqual([4, 38]);
    expect(stats.latMin).toBeCloseTo(-75, 4);
    expect(stats.latMax).toBeCloseTo(75, 4);
    expect(stats.bands).toEqual(['L', 'M']);
  });

  it('ignores unknown keys when summarising', () => {
    const stats = selectionStats(columns, useStore.getState().keyIndex, new Set(['nope|L']));
    expect(stats.n).toBe(1);
    expect(stats.orbits).toEqual([]);
    expect(Number.isNaN(stats.latMin)).toBe(true);
  });
});

describe('saved selections', () => {
  it('posts the selection and reloads the list', async () => {
    const posted: { url: string; body: unknown }[] = [];
    const record = {
      id: 'north-polar-ab12',
      name: 'north polar',
      created: '2026-09-06T00:00:00Z',
      n_frames: 2,
      n_orbits: 2,
      lat_min: 65,
      lat_max: 75,
      note: null,
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string, init?: RequestInit) => {
        const url = String(input);
        if (init?.method === 'POST') {
          posted.push({ url, body: JSON.parse(String(init.body)) });
          return jsonResponse(record);
        }
        return jsonResponse([record]);
      }),
    );

    useStore.getState().addToSelection(Uint32Array.of(0, 4));
    useStore.getState().setSelectionName('north polar');
    const saved = await useStore.getState().saveSelection();

    expect(saved?.id).toBe('north-polar-ab12');
    expect(posted).toHaveLength(1);
    expect(posted[0].url).toContain('/api/selections');
    expect(posted[0].body).toEqual({
      name: 'north polar',
      product_ids: ['P0', 'P4'],
      halves: ['L', 'L'],
    });
    expect(useStore.getState().savedSelections).toEqual([record]);
  });

  it('refuses to save nothing and says so', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({})));
    const saved = await useStore.getState().saveSelection();
    expect(saved).toBeUndefined();
    expect(useStore.getState().toasts.at(-1)?.text).toBe('nothing selected');
  });

  it('loads a saved selection back, keeping only keys the catalog knows', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse({
          id: 'x',
          name: 'reloaded',
          created: '',
          n_frames: 3,
          n_orbits: 2,
          lat_min: null,
          lat_max: null,
          product_ids: ['P0', 'P5', 'GONE'],
          halves: ['L', 'M', 'L'],
        }),
      ),
    );
    await useStore.getState().loadSelection('x');
    expect([...useStore.getState().selectionKeys]).toEqual(['P0|L', 'P5|M']);
    expect(useStore.getState().selectionName).toBe('reloaded');
  });

  it('turns a failed request into a toast instead of an exception', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'no such selection' }, 404)));
    await useStore.getState().loadSelection('missing');
    expect(useStore.getState().toasts.at(-1)?.text).toContain('no such selection');
    expect(useStore.getState().selectionKeys.size).toBe(0);
  });
});

describe('the server summary beside the client filter', () => {
  it('keeps a response only while the filters it was asked for still stand', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ n: 42, by_lat_band: [], by_orbit: [], by_month: [] })));
    await useStore.getState().refreshSummary();
    expect(useStore.getState().summary?.n).toBe(42);
  });

  it('drops the summary when the request fails, rather than showing a stale count', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ n: 42, by_lat_band: [], by_orbit: [], by_month: [] })));
    await useStore.getState().refreshSummary();
    expect(useStore.getState().summary).not.toBeNull();
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'summary failed' }, 500)));
    await useStore.getState().refreshSummary();
    // A caption reading "server agrees: 42" under different filters would be
    // exactly the disagreement this store exists to prevent.
    expect(useStore.getState().summary).toBeNull();
    expect(useStore.getState().toasts.at(-1)?.text).toContain('summary failed');
  });
});
