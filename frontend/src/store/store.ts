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
import { columnsFromIPC, EMPTY_COLUMNS, type CatalogColumns } from '../lib/catalogTable';
import { DEFAULT_FILTERS, filterIndices, filtersToParams, type CatalogFilters } from '../lib/filters';
import { Lru } from '../lib/lru';
import type { ColorMapName } from '../lib/lut';
import { projectColumns, type ViewMode } from '../lib/projection';
import { loadPersisted, savePersisted } from './persist';
import { parseStrips, type StripRow } from '../lib/stripsTable';

export type TabName = 'catalog' | 'poles' | 'strips';
export type ColorBy = 'orbit' | 'year' | 'pixel' | 'emission';

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
  showGraticule: boolean;
  emissionAlpha: number;
  frameImage: ImagePayload | null;
  emissionImage: ImagePayload | null;
  frameLoaded: boolean;

  strips: StripRow[];
  stripId: string | null;
  stripMeta: StripMeta | null;
  stripImage: ImagePayload | null;
  stripStats: StripStats | null;
  stripOrbitFilter: number[] | null;
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
  setShowGraticule(show: boolean): void;
  setEmissionAlpha(alpha: number): void;
  loadFrame(t: number): Promise<void>;

  loadStrips(): Promise<void>;
  openStrip(id: string): Promise<void>;
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
  let latMin = Infinity;
  let latMax = -Infinity;
  for (const key of keys) {
    const i = keyIndex.get(key);
    if (i === undefined) continue;
    const orbit = columns.orbit[i];
    if (Number.isFinite(orbit)) orbits.add(orbit);
    if (columns.half[i]) bands.add(columns.half[i]);
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
  };
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
  showGraticule: true,
  emissionAlpha: 0,
  frameImage: null,
  emissionImage: null,
  frameLoaded: false,

  strips: [],
  stripId: null,
  stripMeta: null,
  stripImage: null,
  stripStats: null,
  stripOrbitFilter: null,
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
      set({
        columns,
        keyIndex,
        filtered: filterIndices(columns, filters),
        positions: projectColumns(columns.boreLat, columns.boreLonEast, viewMode),
      });
    });
  },

  setFilters(patch) {
    const filters = { ...get().filters, ...patch };
    savePersisted('filters', filters);
    set({ filters, filtered: filterIndices(get().columns, filters), page: 0 });
    void get().refreshSummary();
  },

  resetFilters() {
    get().setFilters(DEFAULT_FILTERS);
  },

  setViewMode(mode) {
    const { columns } = get();
    set({ viewMode: mode, positions: projectColumns(columns.boreLat, columns.boreLonEast, mode) });
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

  async refreshSummary() {
    const params = filtersToParams(get().filters);
    await get().run('summary', async () => {
      const summary = await api.summary(params);
      set({ summary });
    });
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
      set({
        stackId: id,
        stackMeta: meta,
        t: 0,
        vmin: meta.stretch.p1,
        vmax: meta.stretch.p99,
        frameImage: null,
        emissionImage: null,
        frameLoaded: false,
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
    set({ vmin, vmax });
    frameCache.clear();
    void get().loadFrame(get().t);
  },

  setShowGraticule: (showGraticule) => set({ showGraticule }),
  setEmissionAlpha: (emissionAlpha) => set({ emissionAlpha }),

  async loadFrame(t) {
    const { stackId, vmin, vmax } = get();
    if (!stackId) return;
    const target = api.stackFrameUrl(stackId, t, vmin, vmax);
    const cached = frameCache.get(target);
    if (cached) {
      if (get().t === t) set({ frameImage: cached, frameLoaded: true });
      return;
    }
    await get().run('frame', async () => {
      const payload = await fetchImage(target);
      frameCache.set(target, payload);
      if (get().t === t && get().stackId === stackId) set({ frameImage: payload, frameLoaded: true });
      // Prefetch the next two frames so playback does not stutter.
      const meta = get().stackMeta;
      if (meta) {
        for (const ahead of [1, 2]) {
          const next = (t + ahead) % meta.times.length;
          const nextUrl = api.stackFrameUrl(stackId, next, vmin, vmax);
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
      set({ stripId: id, stripMeta: meta, stripImage: null, stripStats: null });
      const image = await fetchImage(api.stripImageUrl(id, meta.stretch.p1, meta.stretch.p99));
      if (get().stripId === id) set({ stripImage: image });
    });
    void get().run('strip stats', async () => {
      const stats = await api.stripStats(id);
      if (get().stripId === id) set({ stripStats: stats });
    });
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
