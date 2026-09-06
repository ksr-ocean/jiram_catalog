/** The filter row of the catalog toolbar; every control is labelled. */
import styles from './Views.module.css';
import { useStore } from '../store/store';
import { DEFAULT_FILTERS } from '../lib/filters';
import { LAT_BAND_NAMES, latBandLimits } from '../lib/latbands';

function numberOrNull(text: string): number | null {
  if (text.trim() === '') return null;
  const value = Number(text);
  return Number.isFinite(value) ? value : null;
}

export function CatalogFilters() {
  const filters = useStore((s) => s.filters);
  const setFilters = useStore((s) => s.setFilters);
  const resetFilters = useStore((s) => s.resetFilters);
  const hasTrackability = useStore((s) => s.config?.has_trackability ?? false);

  const bandName =
    LAT_BAND_NAMES.find((name) => {
      const [lo, hi] = latBandLimits(name);
      return lo === filters.latMin && hi === filters.latMax;
    }) ?? 'all';

  return (
    <div className={styles.toolbar} data-testid="catalog-filters">
      <div className={styles.group}>
        <label htmlFor="f-orbit-min">orbit</label>
        <input
          id="f-orbit-min"
          data-testid="filter-orbit-min"
          type="number"
          style={{ width: 58 }}
          value={filters.orbitMin}
          onChange={(e) => setFilters({ orbitMin: Number(e.target.value) })}
        />
        <span className={styles.muted}>to</span>
        <input
          id="f-orbit-max"
          data-testid="filter-orbit-max"
          type="number"
          style={{ width: 58 }}
          value={filters.orbitMax}
          onChange={(e) => setFilters({ orbitMax: Number(e.target.value) })}
        />
      </div>
      <div className={styles.sep} />
      <div className={styles.group}>
        <label htmlFor="f-half">band half</label>
        <select
          id="f-half"
          data-testid="filter-half"
          value={filters.half}
          onChange={(e) => setFilters({ half: e.target.value as 'all' | 'L' | 'M' })}
        >
          <option value="all">all</option>
          <option value="L">L</option>
          <option value="M">M</option>
        </select>
      </div>
      <div className={styles.group}>
        <label htmlFor="f-pixel">pixel &lt;= km</label>
        <input
          id="f-pixel"
          data-testid="filter-pixel-max"
          type="number"
          style={{ width: 66 }}
          placeholder="any"
          value={filters.pixelMaxKm ?? ''}
          onChange={(e) => setFilters({ pixelMaxKm: numberOrNull(e.target.value) })}
        />
      </div>
      <div className={styles.group}>
        <label htmlFor="f-emission">emission &lt;= deg</label>
        <input
          id="f-emission"
          data-testid="filter-emission-max"
          type="number"
          style={{ width: 66 }}
          placeholder="any"
          value={filters.emissionMax ?? ''}
          onChange={(e) => setFilters({ emissionMax: numberOrNull(e.target.value) })}
        />
      </div>
      <div className={styles.group}>
        <label htmlFor="f-onplanet">on-planet &gt;=</label>
        <input
          id="f-onplanet"
          data-testid="filter-on-planet-min"
          type="number"
          step="0.05"
          style={{ width: 66 }}
          placeholder="any"
          value={filters.onPlanetMin ?? ''}
          onChange={(e) => setFilters({ onPlanetMin: numberOrNull(e.target.value) })}
        />
      </div>
      <div className={styles.sep} />
      <div className={styles.group}>
        <label htmlFor="f-latband">latitude band</label>
        <select
          id="f-latband"
          data-testid="filter-lat-band"
          value={bandName}
          onChange={(e) => {
            const [lo, hi] = e.target.value === 'all' ? [-90, 90] : latBandLimits(e.target.value);
            setFilters({ latMin: lo, latMax: hi });
          }}
        >
          <option value="all">all</option>
          {LAT_BAND_NAMES.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.group}>
        <label>
          <input
            type="checkbox"
            data-testid="filter-dayside"
            checked={filters.daysideOnly}
            onChange={(e) => setFilters({ daysideOnly: e.target.checked })}
          />
          dayside only
        </label>
      </div>
      {hasTrackability && (
        <div className={styles.group}>
          <label>
            <input
              type="checkbox"
              data-testid="filter-revisit"
              checked={filters.revisitOnly}
              onChange={(e) => setFilters({ revisitOnly: e.target.checked })}
            />
            same-pass revisit only
          </label>
        </div>
      )}
      <div className={styles.sep} />
      <button
        data-testid="reset-filters"
        onClick={resetFilters}
        disabled={JSON.stringify(filters) === JSON.stringify(DEFAULT_FILTERS)}
      >
        reset filters
      </button>
      <span className={styles.muted}>
        thresholds exclude, they do not require: a frame with no emission angle stays on the map
      </span>
    </div>
  );
}
