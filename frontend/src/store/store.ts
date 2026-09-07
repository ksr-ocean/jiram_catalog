/**
 * The whole application state, in one zustand store.
 *
 * v1 kept per-tab state in Panel widgets and the views drifted apart from
 * each other; here every view reads the same store and the selection tray is
 * a view of it rather than a destination you "send" things to.  Filters and
 * the working selection are mirrored into `localStorage` so a reload lands
 * back where the user was.
 */
import { create } from 'zustand';
import { api, ApiError, fetchImage } from '../api/client';
import type {
  AppConfig,
  CatalogSummary,
  FrameDetail,
  ImagePayload,
  JobRecord,
  NormName,
  SelectionRecord,
  StackListing,
  StackMeta,
  StretchMode,
  StripMeta,
  StripStats,
} from '../api/types';
import {
  bandsByInstrument,
  columnsFromIPC,
  EMPTY_COLUMNS,
  type CatalogColumns,
} from '../lib/catalogTable';
import {
  bandForChannel,
  DEFAULT_FLAT_SIGMA,
  DEFAULT_MINNAERT_K,
  hasRgb,
  normLabel,
  normName,
  resolveStretch,
  splitBands,
  stretchFor,
  stretchForNorm,
  uniqueBands,
} from '../lib/bands';
import { computeFootprints, EMPTY_FOOTPRINTS, type FootprintSet } from '../lib/footprints';
import { composePayloads } from '../lib/rgb';
import {
  DEFAULT_FILTERS,
  filterIndices,
  filtersToParams,
  type CatalogFilters,
} from '../lib/filters';
import { Lru } from '../lib/lru';
import type { ColorMapName } from '../lib/lut';
import { projectColumns, type ViewMode } from '../lib/projection';
import { loadPersisted, savePersisted } from './persist';
import { parseStrips, type StripRow } from '../lib/stripsTable';

export type TabName = 'catalog' | 'poles' | 'strips' | 'coverage' | 'compare';
export type ColorBy = 'orbit' | 'year' | 'pixel' | 'emission' | 'instrument';

export interface Toast {
  id: number;
  kind: 'error' | 'info';
  text: string;
}

/** `product_id` and detector half identify one catalog row. */
export function frameKey(productId: string, half: string): string {
  return `${productId}|${half}`;
}

export interface SelectionStats {
  n: number;
  orbits: number[];
  latMin: number;
  latMax: number;
  bands: string[];
  /** How many of the selected rows came from each instrument. */
  byInstrument: Record<string, number>;
}

interface State {
  config: AppConfig | null;
  loading: Record<string, boolean>;
  toasts: Toast[];
  tab: TabName;

  columns: CatalogColumns;
  keyIndex: Map<string, number>;
  filters: CatalogFilters;
  filtered: Uint32Array;
  viewMode: ViewMode;
  colorBy: ColorBy;
  positions: Float32Array;
  /** JunoCam outlines for the filtered rows, in the current projection. */
  footprints: FootprintSet;
  /** Which band names each instrument has, for the band filter's options. */
  bandsByInstrument: Record<string, string[]>;
  summary: CatalogSummary | null;
  hovered: number | null;
  detail: FrameDetail | null;
  page: number;

  selectionKeys: Set<string>;
  savedSelections: SelectionRecord[];
  selectionName: string;

  stacks: StackListing[];
  stackId: string | null;
  stackMeta: StackMeta | null;
  t: number;
  playing: boolean;
  fps: number;
  cmap: ColorMapName;
  vmin: number;
  vmax: number;
  /** The bands of the open stack; empty when it has no `band` dimension. */
  stackBands: string[];
  band: string | null;
  /** True while the viewer is showing the server's RGB composite. */
  composite: boolean;
  /** One stretch pair for all three channels, or one pair per band. */
  linkBands: boolean;
  bandStretch: Record<string, [number, number]>;
  /**
   * The illumination model the Poles viewer asks the server to divide out,
   * its two parameters, and the PNG mapping.  Initialised from the stack's
   * `meta.norm_default`, so a JunoCam stack opens Lambert-corrected and a
   * JIRAM one opens raw.
   */
  norm: NormName;
  normK: number;
  normSigma: number;
  stretchMode: StretchMode;
  showGraticule: boolean;
  emissionAlpha: number;
  frameImage: ImagePayload | null;
  emissionImage: ImagePayload | null;
  frameLoaded: boolean;
  /**
   * Whether the image on screen is a composite, as opposed to whether the
   * viewer is in composite mode.  The two differ while a request is in
   * flight, and drawing a single-band frame as though it were three bands
   * would flash it grey; this is the flag the viewer draws by.
   */
  frameComposite: boolean;

  strips: StripRow[];
  stripId: string | null;
  stripMeta: StripMeta | null;
  stripImage: ImagePayload | null;
  /** As `frameComposite`, for the strip viewer. */
  stripImageComposite: boolean;
  stripStats: StripStats | null;
  stripOrbitFilter: number[] | null;
  stripBands: string[];
  stripBand: string | null;
  stripComposite: boolean;
  /** The Strips viewer's own illumination state; see `norm` above. */
  stripNorm: NormName;
  stripNormK: number;
  stripNormSigma: number;
  stripStretchMode: StretchMode;
  statsVisible: boolean;

  jobs: JobRecord[];
}

interface Actions {
  setTab(tab: TabName): void;
  pushToast(kind: Toast['kind'], text: string): void;
  dismissToast(id: number): void;
  run<T>(key: string, work: () => Promise<T>): Promise<T | undefined>;

  init(): Promise<void>;
  loadCatalog(): Promise<void>;
  setFilters(patch: Partial<CatalogFilters>): void;
  resetFilters(): void;
  setViewMode(mode: ViewMode): void;
  setColorBy(colorBy: ColorBy): void;
  setHovered(index: number | null): void;
  openDetail(productId: string): Promise<void>;
  closeDetail(): void;
  setPage(page: number): void;
  refreshSummary(): Promise<void>;

  addToSelection(indices: ArrayLike<number>): void;
  replaceSelection(indices: ArrayLike<number>): void;
  removeFromSelection(indices: ArrayLike<number>): void;
  clearSelection(): void;
  selectAllFiltered(): void;
  setSelectionName(name: string): void;
  saveSelection(): Promise<SelectionRecord | undefined>;
  loadSavedSelections(): Promise<void>;
  loadSelection(id: string): Promise<void>;
  deleteSelection(id: string): Promise<void>;

  loadStacks(): Promise<void>;
  openStack(id: string): Promise<void>;
  setTime(t: number): void;
  stepTime(delta: number): void;
  setPlaying(playing: boolean): void;
  setFps(fps: number): void;
  setCmap(cmap: ColorMapName): void;
  setStretch(vmin: number, vmax: number): void;
  setBand(band: string): void;
  setComposite(composite: boolean): void;
  setLinkBands(linked: boolean): void;
  setBandStretch(band: string, vmin: number, vmax: number): void;
  setNorm(norm: NormName, k?: number, sigma?: number): void;
  setStretchMode(mode: StretchMode): void;
  setShowGraticule(show: boolean): void;
  setEmissionAlpha(alpha: number): void;
  loadFrame(t: number): Promise<void>;

  loadStrips(): Promise<void>;
  openStrip(id: string): Promise<void>;
  setStripBand(band: string): void;
  setStripComposite(composite: boolean): void;
  setStripNorm(norm: NormName, k?: number, sigma?: number): void;
  setStripStretchMode(mode: StretchMode): void;
  refreshStripStats(): Promise<void>;
  loadStripImage(): Promise<void>;
  setStripOrbitFilter(orbits: number[] | null): void;
  setStatsVisible(visible: boolean): void;

  pollJobs(): Promise<void>;
  watchJob(id: string, onDone: (job: JobRecord) => void): void;
}

export type Store = State & Actions;

const persistedFilters = loadPersisted<CatalogFilters>('filters', DEFAULT_FILTERS);
if (persistedFilters.instrument === 'JunoCam') {
  persistedFilters.half = 'all';
  persistedFilters.revisitOnly = false;
}
const persistedSelection = loadPersisted<{ keys: string[]; name: string }>('selection', {
  keys: [],
  name: '',
});
// The statistics panel costs three Plotly figures and a server round trip, so
// it starts hidden and the choice is remembered per browser.
const persistedUi = loadPersisted<{ statsVisible: boolean }>('ui', { statsVisible: false });

let toastId = 0;
let initialization: Promise<void> | null = null;

/** Frame PNGs are heavy; twenty is enough for a play loop plus a prefetch. */
const frameCache = new Lru<string, ImagePayload>(20, (value) => value.bitmap.close());

export function selectionStats(
  columns: CatalogColumns,
  keyIndex: Map<string, number>,
  keys: Set<string>,
): SelectionStats {
  const orbits = new Set<number>();
  const bands = new Set<string>();
  const byInstrument: Record<string, number> = {};
  let latMin = Infinity;
  let latMax = -Infinity;
  for (const key of keys) {
    const i = keyIndex.get(key);
    if (i === undefined) continue;
    const orbit = columns.orbit[i];
    if (Number.isFinite(orbit)) orbits.add(orbit);
    // The band of a JIRAM row is its half; a JunoCam row carries several.
    for (const band of splitBands(columns.bands[i] || columns.half[i])) bands.add(band);
    const instrument = columns.instrument[i] || 'JIRAM';
    byInstrument[instrument] = (byInstrument[instrument] ?? 0) + 1;
    const lat = columns.boreLat[i];
    if (Number.isFinite(lat)) {
      if (lat < latMin) latMin = lat;
      if (lat > latMax) latMax = lat;
    }
  }
  return {
    n: keys.size,
    orbits: [...orbits].sort((a, b) => a - b),
    latMin: Number.isFinite(latMin) ? latMin : NaN,
    latMax: Number.isFinite(latMax) ? latMax : NaN,
    bands: [...bands].sort(),
    byInstrument,
  };
}

/**
 * The stretch pairs an RGB request sends.
 *
 * Linked, the one pair on the toolbar goes to all three channels, which is
 * what a first look at a composite wants; unlinked, each band keeps its own
 * pair so the colour balance can be set deliberately.
 */
export function compositeStretch(state: {
  stackBands: string[];
  linkBands: boolean;
  bandStretch: Record<string, [number, number]>;
  vmin: number;
  vmax: number;
}): { r: [number, number]; g: [number, number]; b: [number, number] } {
  const pair = (channel: 'r' | 'g' | 'b'): [number, number] => {
    if (state.linkBands) return [state.vmin, state.vmax];
    const band = bandForChannel(state.stackBands, channel);
    const stretch = band ? state.bandStretch[band] : undefined;
    return stretch ?? [state.vmin, state.vmax];
  };
  return { r: pair('r'), g: pair('g'), b: pair('b') };
}

/** The wire spelling of the Poles viewer's illumination choice. */
export function currentNorm(state: { norm: NormName; normK: number; normSigma: number }): string {
  return normLabel(state.norm, state.normK, state.normSigma);
}

/** The same for the Strips viewer. */
export function currentStripNorm(state: {
  stripNorm: NormName;
  stripNormK: number;
  stripNormSigma: number;
}): string {
  return normLabel(state.stripNorm, state.stripNormK, state.stripNormSigma);
}

/** Exact wire identities keep late responses from replacing newer display choices. */
function frameImageUrl(state: State, t = state.t): string | null {
  if (!state.stackId || !state.stackMeta) return null;
  const norm = currentNorm(state);
  return state.composite
    ? api.stackRgbUrl(state.stackId, t, compositeStretch(state), 1600, norm, state.stretchMode)
    : api.stackFrameUrl(
        state.stackId,
        t,
        state.vmin,
        state.vmax,
        1600,
        state.band,
        norm,
        state.stretchMode,
      );
}

function stripImageUrls(state: State): string[] {
  if (!state.stripId || !state.stripMeta) return [];
  const norm = currentStripNorm(state);
  const bands =
    state.stripComposite && hasRgb(state.stripBands)
      ? (['r', 'g', 'b'] as const).map((channel) => bandForChannel(state.stripBands, channel))
      : [state.stripBand];
  return bands.map((band) => {
    const stretch = stretchFor(state.stripMeta!.stretch, norm, band);
    return api.stripImageUrl(
      state.stripId!,
      stretch.p1,
      stretch.p99,
      1600,
      band,
      norm,
      state.stripStretchMode,
    );
  });
}

/**
 * The per-band stretch pairs a product's metadata gives for one norm.
 *
 * Every model moves the histogram -- dividing by `cos(i)` at 80 degrees is a
 * factor of six -- so the pairs have to be re-read whenever the norm changes
 * rather than carried over, which is what made the first Lambert view of the
 * orbit-4 stack come out white.
 */
export function bandStretchFor(
  stretch: StackMeta['stretch'] | StripMeta['stretch'] | null | undefined,
  norm: string,
  bands: string[],
): Record<string, [number, number]> {
  const out: Record<string, [number, number]> = {};
  const forNorm = stretchForNorm(stretch, norm);
  for (const name of bands) {
    const pair = resolveStretch(forNorm, name);
    out[name] = [pair.p1, pair.p99];
  }
  return out;
}

export const useStore = create<Store>((set, get) => ({
  config: null,
  loading: {},
  toasts: [],
  tab: 'catalog',

  columns: EMPTY_COLUMNS,
  keyIndex: new Map(),
  filters: persistedFilters,
  filtered: new Uint32Array(0),
  viewMode: 'cyl',
  colorBy: 'orbit',
  positions: new Float32Array(0),
  footprints: EMPTY_FOOTPRINTS,
  bandsByInstrument: {},
  summary: null,
  hovered: null,
  detail: null,
  page: 0,

  selectionKeys: new Set(persistedSelection.keys ?? []),
  savedSelections: [],
  selectionName: persistedSelection.name ?? '',

  stacks: [],
  stackId: null,
  stackMeta: null,
  t: 0,
  playing: false,
  fps: 4,
  cmap: 'gray',
  vmin: 0,
  vmax: 1,
  stackBands: [],
  band: null,
  composite: false,
  linkBands: true,
  bandStretch: {},
  norm: 'none',
  normK: DEFAULT_MINNAERT_K,
  normSigma: DEFAULT_FLAT_SIGMA,
  stretchMode: 'linear',
  showGraticule: true,
  emissionAlpha: 0,
  frameImage: null,
  emissionImage: null,
  frameLoaded: false,
  frameComposite: false,

  strips: [],
  stripId: null,
  stripMeta: null,
  stripImage: null,
  stripImageComposite: false,
  stripStats: null,
  stripOrbitFilter: null,
  stripBands: [],
  stripBand: null,
  stripComposite: false,
  stripNorm: 'none',
  stripNormK: DEFAULT_MINNAERT_K,
  stripNormSigma: DEFAULT_FLAT_SIGMA,
  stripStretchMode: 'linear',
  statsVisible: persistedUi.statsVisible,

  jobs: [],

  setTab: (tab) => set({ tab }),

  pushToast: (kind, text) =>
    set((state) => ({ toasts: [...state.toasts, { id: ++toastId, kind, text }].slice(-4) })),

  dismissToast: (id) => set((state) => ({ toasts: state.toasts.filter((t) => t.id !== id) })),

  async run(key, work) {
    set((state) => ({ loading: { ...state.loading, [key]: true } }));
    try {
      return await work();
    } catch (error) {
      const message =
        error instanceof ApiError ? `${key}: ${error.message}` : `${key}: ${String(error)}`;
      get().pushToast('error', message);
      return undefined;
    } finally {
      set((state) => ({ loading: { ...state.loading, [key]: false } }));
    }
  },

  async init() {
    if (initialization) return initialization;
    initialization = (async () => {
      const configRequest = get().run('config', async () => {
        const config = await api.config();
        set({ config });
      });
      // Map and configuration can load independently. Cataloging the same
      // NetCDF products again is unnecessary until configuration has finished.
      await Promise.all([configRequest, get().loadCatalog(), get().loadSavedSelections()]);
      await Promise.all([get().loadStacks(), get().loadStrips()]);
      await get().refreshSummary();
    })();
    return initialization;
  },

  async loadCatalog() {
    await get().run('catalog', async () => {
      const bytes = await api.framesArrow();
      const columns = columnsFromIPC(bytes);
      const keyIndex = new Map<string, number>();
      for (let i = 0; i < columns.n; i++)
        keyIndex.set(frameKey(columns.productId[i], columns.half[i]), i);
      const { filters, viewMode } = get();
      const filtered = filterIndices(columns, filters);
      set({
        columns,
        keyIndex,
        filtered,
        positions: projectColumns(columns.boreLat, columns.boreLonEast, viewMode),
        footprints: computeFootprints(columns, filtered, viewMode),
        bandsByInstrument: bandsByInstrument(columns),
      });
    });
  },

  setFilters(patch) {
    const filters = { ...get().filters, ...patch };
    if (filters.instrument === 'JunoCam') {
      filters.half = 'all';
      filters.revisitOnly = false;
    }
    savePersisted('filters', filters);
    const { columns, viewMode } = get();
    const filtered = filterIndices(columns, filters);
    set({
      filters,
      filtered,
      footprints: computeFootprints(columns, filtered, viewMode),
      page: 0,
    });
    void get().refreshSummary();
  },

  resetFilters() {
    get().setFilters(DEFAULT_FILTERS);
  },

  setViewMode(mode) {
    const { columns, filtered } = get();
    set({
      viewMode: mode,
      positions: projectColumns(columns.boreLat, columns.boreLonEast, mode),
      footprints: computeFootprints(columns, filtered, mode),
    });
  },

  setColorBy: (colorBy) => set({ colorBy }),
  setHovered: (hovered) => set({ hovered }),

  async openDetail(productId) {
    await get().run('frame detail', async () => {
      const detail = await api.frame(productId);
      set({ detail });
    });
  },

  closeDetail: () => set({ detail: null }),
  setPage: (page) => set({ page }),

  /**
   * The server's count for the current filters.
   *
   * Two things here are about the same property, viz. that the map and the
   * charts can never quietly disagree.  A failed request drops the summary
   * rather than leaving the previous filters' count in the caption, since a
   * stale "server agrees" is worse than no number at all; and a response is
   * only kept when the filters have not moved on since it was asked for, so
   * two quick filter changes cannot leave the earlier answer on screen.
   */
  async refreshSummary() {
    const params = filtersToParams(get().filters);
    const summary = await get().run('summary', () => api.summary(params));
    if (summary === undefined) {
      set({ summary: null });
      return;
    }
    const current = filtersToParams(get().filters);
    if (JSON.stringify(current) === JSON.stringify(params)) set({ summary });
  },

  addToSelection(indices) {
    const { columns, selectionKeys, selectionName } = get();
    const keys = new Set(selectionKeys);
    for (let k = 0; k < indices.length; k++) {
      const i = indices[k];
      keys.add(frameKey(columns.productId[i], columns.half[i]));
    }
    savePersisted('selection', { keys: [...keys], name: selectionName });
    set({ selectionKeys: keys });
  },

  replaceSelection(indices) {
    const { columns, selectionName } = get();
    const keys = new Set<string>();
    for (let k = 0; k < indices.length; k++) {
      const i = indices[k];
      keys.add(frameKey(columns.productId[i], columns.half[i]));
    }
    savePersisted('selection', { keys: [...keys], name: selectionName });
    set({ selectionKeys: keys });
  },

  removeFromSelection(indices) {
    const { columns, selectionKeys, selectionName } = get();
    const keys = new Set(selectionKeys);
    for (let k = 0; k < indices.length; k++) {
      const i = indices[k];
      keys.delete(frameKey(columns.productId[i], columns.half[i]));
    }
    savePersisted('selection', { keys: [...keys], name: selectionName });
    set({ selectionKeys: keys });
  },

  clearSelection() {
    savePersisted('selection', { keys: [], name: get().selectionName });
    set({ selectionKeys: new Set() });
  },

  selectAllFiltered() {
    get().replaceSelection(get().filtered);
  },

  setSelectionName(name) {
    savePersisted('selection', { keys: [...get().selectionKeys], name });
    set({ selectionName: name });
  },

  async saveSelection() {
    const { selectionKeys, selectionName, columns, keyIndex } = get();
    if (selectionKeys.size === 0) {
      get().pushToast('info', 'nothing selected');
      return undefined;
    }
    const productIds: string[] = [];
    const halves: string[] = [];
    for (const key of selectionKeys) {
      const i = keyIndex.get(key);
      if (i === undefined) continue;
      productIds.push(columns.productId[i]);
      halves.push(columns.half[i]);
    }
    return get().run('save selection', async () => {
      const record = await api.createSelection({
        name: selectionName.trim() || `selection ${new Date().toISOString().slice(0, 19)}`,
        product_ids: productIds,
        halves,
      });
      await get().loadSavedSelections();
      get().pushToast('info', `saved "${record.name}" (${record.n_frames} frames)`);
      return record;
    });
  },

  async loadSavedSelections() {
    await get().run('selections', async () => {
      set({ savedSelections: await api.selections() });
    });
  },

  async loadSelection(id) {
    await get().run('selection', async () => {
      const detail = await api.selection(id);
      const halves = detail.halves ?? [];
      const keys = new Set<string>();
      detail.product_ids.forEach((productId, i) => {
        const half = halves[i];
        if (half) keys.add(frameKey(productId, half));
        else for (const h of ['L', 'M']) keys.add(frameKey(productId, h));
      });
      const known = new Set([...keys].filter((key) => get().keyIndex.has(key)));
      savePersisted('selection', { keys: [...known], name: detail.name });
      set({ selectionKeys: known, selectionName: detail.name });
    });
  },

  async deleteSelection(id) {
    await get().run('delete selection', async () => {
      await api.deleteSelection(id);
      await get().loadSavedSelections();
    });
  },

  async loadStacks() {
    await get().run('stacks', async () => {
      set({ stacks: await api.stacks() });
    });
  },

  async openStack(id) {
    set({
      stackId: id,
      stackMeta: null,
      frameImage: null,
      emissionImage: null,
      stackBands: [],
      band: null,
      t: 0,
      playing: false,
      composite: false,
    });
    await get().run('stack', async () => {
      const meta = await api.stackMeta(id);
      if (get().stackId !== id) return;
      // A stack with a `band` dimension opens on its first band and in single
      // band mode: a composite is a choice, not a default, because it needs
      // three requests and hides the colour maps.
      const bands = uniqueBands(meta.bands);
      const band = bands.length > 0 ? bands[0] : null;
      // The product says how it wants to be read; the viewer opens that way
      // and the user overrides it from the Illumination selector.
      const norm = normName(meta.norm_default);
      const label = normLabel(norm, DEFAULT_MINNAERT_K, DEFAULT_FLAT_SIGMA);
      const stretch = stretchFor(meta.stretch, label, band);
      // Three JunoCam colour strips differ in throughput by tens of per cent
      // and in illumination by where each crossed the terminator, so their
      // stretches start apart rather than linked; one JIRAM band has nothing
      // to link to and keeps the old default either way.
      const junocam = String(meta.instrument ?? 'JIRAM').toLowerCase() === 'junocam';
      set({
        stackId: id,
        stackMeta: meta,
        t: 0,
        vmin: stretch.p1,
        vmax: stretch.p99,
        stackBands: bands,
        band,
        composite: false,
        bandStretch: bandStretchFor(meta.stretch, label, bands),
        norm,
        normK: DEFAULT_MINNAERT_K,
        normSigma: DEFAULT_FLAT_SIGMA,
        linkBands: !junocam,
        frameImage: null,
        emissionImage: null,
        frameLoaded: false,
        frameComposite: false,
        playing: false,
      });
      frameCache.clear();
      await get().loadFrame(0);
    });
  },

  setTime(t) {
    const meta = get().stackMeta;
    const last = meta ? meta.times.length - 1 : 0;
    const clamped = Math.max(0, Math.min(last, t));
    set({ t: clamped, ...(clamped !== get().t ? { emissionImage: null } : {}) });
    void get().loadFrame(clamped);
  },

  stepTime(delta) {
    const meta = get().stackMeta;
    if (!meta) return;
    const n = meta.times.length;
    get().setTime((get().t + delta + n) % n);
  },

  setPlaying: (playing) => set({ playing }),
  setFps: (fps) => set({ fps }),
  setCmap: (cmap) => set({ cmap }),

  setStretch(vmin, vmax) {
    const { band } = get();
    const bandStretch = band
      ? { ...get().bandStretch, [band]: [vmin, vmax] as [number, number] }
      : get().bandStretch;
    set({ vmin, vmax, bandStretch });
    frameCache.clear();
    void get().loadFrame(get().t);
  },

  setBand(band) {
    const state = get();
    const pair =
      state.bandStretch[band] ??
      (() => {
        const stretch = stretchFor(state.stackMeta?.stretch, currentNorm(state), band);
        return [stretch.p1, stretch.p99] as [number, number];
      })();
    set({ band, vmin: pair[0], vmax: pair[1] });
    frameCache.clear();
    void get().loadFrame(get().t);
  },

  setComposite(composite) {
    set({ composite });
    frameCache.clear();
    void get().loadFrame(get().t);
  },

  setLinkBands(linkBands) {
    set({ linkBands });
    if (get().composite) {
      frameCache.clear();
      void get().loadFrame(get().t);
    }
  },

  setBandStretch(band, vmin, vmax) {
    set({ bandStretch: { ...get().bandStretch, [band]: [vmin, vmax] } });
    if (get().composite) {
      frameCache.clear();
      void get().loadFrame(get().t);
    }
  },

  /**
   * Change the illumination model, and re-read every stretch under it.
   *
   * Keeping the old limits would be the same mistake the raw stack made:
   * a Lambert-corrected band near the terminator runs to twenty times the
   * radiance of the raw one, and the picture would arrive white.
   */
  setNorm(norm, k, sigma) {
    const state = get();
    const normK = k ?? state.normK;
    const normSigma = sigma ?? state.normSigma;
    const label = normLabel(norm, normK, normSigma);
    const stretch = stretchFor(state.stackMeta?.stretch, label, state.band);
    set({
      norm,
      normK,
      normSigma,
      vmin: stretch.p1,
      vmax: stretch.p99,
      bandStretch: bandStretchFor(state.stackMeta?.stretch, label, state.stackBands),
    });
    frameCache.clear();
    void get().loadFrame(get().t);
  },

  setStretchMode(stretchMode) {
    set({ stretchMode });
    frameCache.clear();
    void get().loadFrame(get().t);
  },

  setShowGraticule: (showGraticule) => set({ showGraticule }),
  setEmissionAlpha: (emissionAlpha) => set({ emissionAlpha }),

  async loadFrame(t) {
    const state = get();
    const { stackId, composite } = state;
    if (!stackId) return;
    // The composite is one request per time step, not three: the contract's
    // `rgb.png` reads the three bands out of the same file server-side.
    const target = frameImageUrl(state, t);
    if (!target) return;
    const isCurrent = () => frameImageUrl(get()) === target;
    const cached = frameCache.get(target);
    if (cached) {
      if (isCurrent()) set({ frameImage: cached, frameLoaded: true, frameComposite: composite });
      return;
    }
    if (isCurrent()) set({ frameImage: null, frameLoaded: false });
    await get().run('frame', async () => {
      const payload = await fetchImage(target);
      frameCache.set(target, payload);
      if (isCurrent()) {
        set({ frameImage: payload, frameLoaded: true, frameComposite: composite });
      }
      if (!isCurrent()) return;
      // Prefetch the next two frames so playback does not stutter.
      const meta = get().stackMeta;
      if (meta) {
        for (const ahead of [1, 2]) {
          const next = (t + ahead) % meta.times.length;
          const nextUrl = frameImageUrl(state, next);
          if (nextUrl && !frameCache.has(nextUrl)) {
            void fetchImage(nextUrl)
              .then((image) => frameCache.set(nextUrl, image))
              .catch(() => undefined);
          }
        }
      }
    });
    if (get().emissionAlpha > 0 && !get().emissionImage) {
      void fetchImage(api.stackEmissionUrl(stackId, t))
        .then((image) => {
          if (get().stackId === stackId && get().t === t) set({ emissionImage: image });
        })
        .catch(() => undefined);
    }
  },

  async loadStrips() {
    await get().run('strips', async () => {
      const bytes = await api.stripsArrow();
      set({ strips: parseStrips(bytes) });
    });
  },

  async openStrip(id) {
    set({
      stripId: id,
      stripMeta: null,
      stripImage: null,
      stripStats: null,
      stripBands: [],
      stripBand: null,
      stripComposite: false,
    });
    await get().run('strip', async () => {
      const meta = await api.stripMeta(id);
      if (get().stripId !== id) return;
      const bands = uniqueBands(meta.bands);
      set({
        stripId: id,
        stripMeta: meta,
        stripImage: null,
        stripImageComposite: false,
        stripStats: null,
        stripBands: bands,
        stripBand: bands.length > 0 ? bands[0] : null,
        stripComposite: false,
        stripNorm: normName(meta.norm_default),
        stripNormK: DEFAULT_MINNAERT_K,
        stripNormSigma: DEFAULT_FLAT_SIGMA,
      });
      await get().loadStripImage();
    });
    if (get().stripId !== id || !get().stripMeta) return;
    void get().refreshStripStats();
  },

  /**
   * The strip image, single band or composed here.
   *
   * The amendment gives a strip `image.png?band=` and no composite endpoint,
   * so an RGB strip is three requests combined in the browser -- the same
   * picture the stack viewer gets from the server, by the only route the
   * contract offers.
   */
  async loadStripImage() {
    const state = get();
    const urls = stripImageUrls(state);
    if (urls.length === 0) return;
    const identity = JSON.stringify(urls);
    const isCurrent = () => JSON.stringify(stripImageUrls(get())) === identity;
    set({ stripImage: null });
    await get().run('strip image', async () => {
      if (urls.length === 3) {
        const channels = await Promise.all(urls.map(fetchImage));
        const composed = composePayloads(channels[0], channels[1], channels[2]);
        if (!isCurrent()) return;
        if (composed) {
          set({ stripImage: composed, stripImageComposite: true });
          return;
        }
        get().pushToast('error', 'the three band images differ in size; showing one band');
        set({ stripComposite: false, stripImage: channels[0], stripImageComposite: false });
        return;
      }
      const image = await fetchImage(urls[0]);
      if (isCurrent()) set({ stripImage: image, stripImageComposite: false });
    });
  },

  setStripBand(band) {
    set({ stripBand: band, stripStats: null });
    void get().loadStripImage();
    void get().refreshStripStats();
  },

  setStripComposite(stripComposite) {
    set({ stripComposite });
    void get().loadStripImage();
  },

  /**
   * Change the strip viewer's illumination model.
   *
   * Both the picture and the statistics follow it: a spectrum of a
   * limb-darkened swath and a spectrum of the same swath corrected are two
   * different measurements, and the panel must never show one under the
   * other's label.
   */
  setStripNorm(stripNorm, k, sigma) {
    set({
      stripNorm,
      stripStats: null,
      stripNormK: k ?? get().stripNormK,
      stripNormSigma: sigma ?? get().stripNormSigma,
    });
    void get().loadStripImage();
    void get().refreshStripStats();
  },

  setStripStretchMode(stripStretchMode) {
    set({ stripStretchMode });
    void get().loadStripImage();
  },

  async refreshStripStats() {
    const { stripId, stripBand, stripMeta, statsVisible } = get();
    if (!stripId || !stripMeta || !statsVisible) return;
    const norm = currentStripNorm(get());
    await get().run('strip stats', async () => {
      const stats = await api.stripStats(stripId, stripBand, norm);
      const now = get();
      if (
        now.stripId === stripId &&
        now.stripBand === stripBand &&
        currentStripNorm(now) === norm
      ) {
        set({ stripStats: stats });
      }
    });
  },

  setStripOrbitFilter: (stripOrbitFilter) => set({ stripOrbitFilter }),

  setStatsVisible(statsVisible) {
    savePersisted('ui', { statsVisible });
    set({ statsVisible });
    if (statsVisible && !get().stripStats) void get().refreshStripStats();
  },

  async pollJobs() {
    try {
      set({ jobs: await api.jobs() });
    } catch {
      /* the jobs indicator is not worth a toast on every poll */
    }
  },

  watchJob(id, onDone) {
    const timer = setInterval(async () => {
      try {
        const job = await api.job(id);
        set((state) => ({ jobs: [job, ...state.jobs.filter((j) => j.id !== id)] }));
        if (job.status === 'done' || job.status === 'failed') {
          clearInterval(timer);
          onDone(job);
        }
      } catch {
        clearInterval(timer);
      }
    }, 2000);
  },
}));

export { frameCache };
