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
  SelectionRecord,
  StackListing,
  StackMeta,
  StripMeta,
  StripStats,
} from '../api/types';
import { bandsByInstrument, columnsFromIPC, EMPTY_COLUMNS, type CatalogColumns } from '../lib/catalogTable';
import { bandForChannel, hasRgb, resolveStretch, splitBands, uniqueBands } from '../lib/bands';
import { computeFootprints, EMPTY_FOOTPRINTS, type FootprintSet } from '../lib/footprints';
import { composePayloads } from '../lib/rgb';
import { DEFAULT_FILTERS, filterIndices, filtersToParams, type CatalogFilters } from '../lib/filters';
import { Lru } from '../lib/lru';
import type { ColorMapName } from '../lib/lut';
import { projectColumns, type ViewMode } from '../lib/projection';
import { loadPersisted, savePersisted } from './persist';
import { parseStrips, type StripRow } from '../lib/stripsTable';

export type TabName = 'catalog' | 'poles' | 'strips';
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
  setShowGraticule(show: boolean): void;
  setEmissionAlpha(alpha: number): void;
  loadFrame(t: number): Promise<void>;

  loadStrips(): Promise<void>;
  openStrip(id: string): Promise<void>;
  setStripBand(band: string): void;
  setStripComposite(composite: boolean): void;
  loadStripImage(): Promise<void>;
  setStripOrbitFilter(orbits: number[] | null): void;
  setStatsVisible(visible: boolean): void;

  pollJobs(): Promise<void>;
  watchJob(id: string, onDone: (job: JobRecord) => void): void;
}

export type Store = State & Actions;

const persistedFilters = loadPersisted<CatalogFilters>('filters', DEFAULT_FILTERS);
const persistedSelection = loadPersisted<{ keys: string[]; name: string }>('selection', { keys: [], name: '' });
// The statistics panel costs three Plotly figures and a server round trip, so
// it starts hidden and the choice is remembered per browser.
const persistedUi = loadPersisted<{ statsVisible: boolean }>('ui', { statsVisible: false });

let toastId = 0;

/** Frame PNGs are heavy; twenty is enough for a play loop plus a prefetch. */
const frameCache = new Lru<string, ImagePayload>(20, (value) => value.bitmap.close());

export function selectionStats(columns: CatalogColumns, keyIndex: Map<string, number>, keys: Set<string>): SelectionStats {
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
      const message = error instanceof ApiError ? `${key}: ${error.message}` : `${key}: ${String(error)}`;
      get().pushToast('error', message);
      return undefined;
    } finally {
      set((state) => ({ loading: { ...state.loading, [key]: false } }));
    }
  },

  async init() {
    await get().run('config', async () => {
      const config = await api.config();
      set({ config });
    });
    await get().loadCatalog();
    await Promise.all([get().loadSavedSelections(), get().loadStacks(), get().loadStrips()]);
    await get().refreshSummary();
  },

  async loadCatalog() {
    await get().run('catalog', async () => {
      const bytes = await api.framesArrow();
      const columns = columnsFromIPC(bytes);
      const keyIndex = new Map<string, number>();
      for (let i = 0; i < columns.n; i++) keyIndex.set(frameKey(columns.productId[i], columns.half[i]), i);
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
    await get().run('stack', async () => {
      const meta = await api.stackMeta(id);
      // A stack with a `band` dimension opens on its first band and in single
      // band mode: a composite is a choice, not a default, because it needs
      // three requests and hides the colour maps.
      const bands = uniqueBands(meta.bands);
      const band = bands.length > 0 ? bands[0] : null;
      const stretch = resolveStretch(meta.stretch, band);
      const bandStretch: Record<string, [number, number]> = {};
      for (const name of bands) {
        const pair = resolveStretch(meta.stretch, name);
        bandStretch[name] = [pair.p1, pair.p99];
      }
      set({
        stackId: id,
        stackMeta: meta,
        t: 0,
        vmin: stretch.p1,
        vmax: stretch.p99,
        stackBands: bands,
        band,
        composite: false,
        bandStretch,
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
    set({ t: clamped });
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
    const bandStretch = band ? { ...get().bandStretch, [band]: [vmin, vmax] as [number, number] } : get().bandStretch;
    set({ vmin, vmax, bandStretch });
    frameCache.clear();
    void get().loadFrame(get().t);
  },

  setBand(band) {
    const { stackMeta } = get();
    const pair = get().bandStretch[band] ?? (() => {
      const stretch = resolveStretch(stackMeta?.stretch, band);
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

  setShowGraticule: (showGraticule) => set({ showGraticule }),
  setEmissionAlpha: (emissionAlpha) => set({ emissionAlpha }),

  async loadFrame(t) {
    const state = get();
    const { stackId, vmin, vmax, band, composite } = state;
    if (!stackId) return;
    // The composite is one request per time step, not three: the contract's
    // `rgb.png` reads the three bands out of the same file server-side.
    const frameUrl = (step: number): string =>
      composite
        ? api.stackRgbUrl(stackId, step, compositeStretch(state))
        : api.stackFrameUrl(stackId, step, vmin, vmax, 1600, band);
    const target = frameUrl(t);
    const cached = frameCache.get(target);
    if (cached) {
      if (get().t === t) set({ frameImage: cached, frameLoaded: true, frameComposite: composite });
      return;
    }
    await get().run('frame', async () => {
      const payload = await fetchImage(target);
      frameCache.set(target, payload);
      if (get().t === t && get().stackId === stackId) {
        set({ frameImage: payload, frameLoaded: true, frameComposite: composite });
      }
      // Prefetch the next two frames so playback does not stutter.
      const meta = get().stackMeta;
      if (meta) {
        for (const ahead of [1, 2]) {
          const next = (t + ahead) % meta.times.length;
          const nextUrl = frameUrl(next);
          if (!frameCache.has(nextUrl)) {
            void fetchImage(nextUrl).then((image) => frameCache.set(nextUrl, image)).catch(() => undefined);
          }
        }
      }
    });
    if (get().emissionAlpha > 0 && !get().emissionImage) {
      void fetchImage(api.stackEmissionUrl(stackId, t)).then((image) => set({ emissionImage: image })).catch(() => undefined);
    }
  },

  async loadStrips() {
    await get().run('strips', async () => {
      const bytes = await api.stripsArrow();
      set({ strips: parseStrips(bytes) });
    });
  },

  async openStrip(id) {
    await get().run('strip', async () => {
      const meta = await api.stripMeta(id);
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
      });
      await get().loadStripImage();
    });
    void get().run('strip stats', async () => {
      const stats = await api.stripStats(id, get().stripBand);
      if (get().stripId === id) set({ stripStats: stats });
    });
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
    const { stripId, stripMeta, stripBand, stripComposite, stripBands } = get();
    if (!stripId || !stripMeta) return;
    await get().run('strip image', async () => {
      if (stripComposite && hasRgb(stripBands)) {
        const channels = await Promise.all(
          (['r', 'g', 'b'] as const).map((channel) => {
            const name = bandForChannel(stripBands, channel);
            const stretch = resolveStretch(stripMeta.stretch, name);
            return fetchImage(api.stripImageUrl(stripId, stretch.p1, stretch.p99, 1600, name));
          }),
        );
        const composed = composePayloads(channels[0], channels[1], channels[2]);
        if (get().stripId !== stripId) return;
        if (composed) {
          set({ stripImage: composed, stripImageComposite: true });
          return;
        }
        get().pushToast('error', 'the three band images differ in size; showing one band');
        set({ stripComposite: false, stripImage: channels[0], stripImageComposite: false });
        return;
      }
      const stretch = resolveStretch(stripMeta.stretch, stripBand);
      const image = await fetchImage(api.stripImageUrl(stripId, stretch.p1, stretch.p99, 1600, stripBand));
      if (get().stripId === stripId) set({ stripImage: image, stripImageComposite: false });
    });
  },

  setStripBand(band) {
    set({ stripBand: band });
    void get().loadStripImage();
    const id = get().stripId;
    if (!id) return;
    void get().run('strip stats', async () => {
      const stats = await api.stripStats(id, band);
      if (get().stripId === id && get().stripBand === band) set({ stripStats: stats });
    });
  },

  setStripComposite(stripComposite) {
    set({ stripComposite });
    void get().loadStripImage();
  },

  setStripOrbitFilter: (stripOrbitFilter) => set({ stripOrbitFilter }),

  setStatsVisible(statsVisible) {
    savePersisted('ui', { statsVisible });
    set({ statsVisible });
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
