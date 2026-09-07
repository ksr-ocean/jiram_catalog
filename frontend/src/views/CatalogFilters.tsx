import { useMemo } from 'react';
import styles from './Views.module.css';
import { useStore } from '../store/store';
import { DEFAULT_FILTERS, CATALOG_PRESETS } from '../lib/filters';
import { LAT_BAND_NAMES, latBandLimits } from '../lib/latbands';
import { bandOptions, INSTRUMENTS, type Instrument } from '../lib/bands';
const numberOrNull = (text: string) =>
  text.trim() === '' || !Number.isFinite(Number(text)) ? null : Number(text);
export function CatalogFilters() {
  const filters = useStore((s) => s.filters),
    setFilters = useStore((s) => s.setFilters),
    reset = useStore((s) => s.resetFilters),
    hasTrackability = useStore((s) => s.config?.has_trackability ?? false),
    byInstrument = useStore((s) => s.bandsByInstrument);
  const bands = useMemo(
    () => bandOptions(byInstrument, filters.instrument),
    [byInstrument, filters.instrument],
  );
  const junocam = filters.instrument === 'JunoCam';
  const latitudeBand =
    LAT_BAND_NAMES.find((name) => {
      const [lo, hi] = latBandLimits(name);
      return lo === filters.latMin && hi === filters.latMax;
    }) ?? 'all';
  return (
    <div className={styles.card} data-testid="catalog-filters">
      <div className={styles.toolbar}>
        <label>
          Workflow{' '}
          <select
            data-testid="catalog-preset"
            value=""
            onChange={(e) => {
              const preset = CATALOG_PRESETS.find((p) => p.id === e.target.value);
              if (preset) setFilters({ ...DEFAULT_FILTERS, ...preset.filters });
            }}
          >
            <option value="">Choose a starting point</option>
            {CATALOG_PRESETS.map((preset) => (
              <option key={preset.id} value={preset.id}>
                {preset.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Instrument{' '}
          <select
            id="f-instrument"
            data-testid="filter-instrument"
            value={filters.instrument}
            onChange={(e) => {
              const instrument = e.target.value as 'all' | Instrument;
              setFilters({
                instrument,
                band:
                  filters.band && bandOptions(byInstrument, instrument).includes(filters.band)
                    ? filters.band
                    : null,
                ...(instrument === 'JunoCam' ? { half: 'all', revisitOnly: false } : {}),
              });
            }}
          >
            <option value="all">Both instruments</option>
            {INSTRUMENTS.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          Band{' '}
          <select
            id="f-band"
            data-testid="filter-band"
            value={filters.band ?? 'all'}
            onChange={(e) => setFilters({ band: e.target.value === 'all' ? null : e.target.value })}
          >
            <option value="all">All</option>
            {bands.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          Pass{' '}
          <input
            data-testid="filter-orbit-min"
            aria-label="First pass"
            type="number"
            style={{ width: 64 }}
            value={filters.orbitMin}
            onChange={(e) => setFilters({ orbitMin: Number(e.target.value) })}
          />
        </label>
        <label>
          to{' '}
          <input
            data-testid="filter-orbit-max"
            aria-label="Last pass"
            type="number"
            style={{ width: 64 }}
            value={filters.orbitMax}
            onChange={(e) => setFilters({ orbitMax: Number(e.target.value) })}
          />
        </label>
        <label>
          Latitude{' '}
          <select
            data-testid="filter-lat-band"
            value={latitudeBand}
            onChange={(e) => {
              const [latMin, latMax] =
                e.target.value === 'all' ? [-90, 90] : latBandLimits(e.target.value);
              setFilters({ latMin, latMax });
            }}
          >
            <option value="all">All</option>
            {LAT_BAND_NAMES.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
        </label>
        <label>
          Match{' '}
          <select
            data-testid="filter-latitude-mode"
            value={filters.latitudeMode}
            onChange={(e) =>
              setFilters({ latitudeMode: e.target.value as 'coverage' | 'boresight' })
            }
          >
            <option value="coverage">Footprint overlap</option>
            <option value="boresight">Boresight centre</option>
          </select>
        </label>
        <button data-testid="reset-filters" onClick={reset}>
          Reset filters
        </button>
      </div>
      <details>
        <summary>Advanced geometry and time filters</summary>
        <div className={styles.toolbar}>
          {!junocam && (
            <label>
              JIRAM half{' '}
              <select
                data-testid="filter-half"
                value={filters.half}
                onChange={(e) => setFilters({ half: e.target.value as 'all' | 'L' | 'M' })}
              >
                <option value="all">All</option>
                <option>L</option>
                <option>M</option>
              </select>
            </label>
          )}
          <label>
            Pixel ≤ km{' '}
            <input
              data-testid="filter-pixel-max"
              type="number"
              value={filters.pixelMaxKm ?? ''}
              placeholder="Any"
              style={{ width: 85 }}
              onChange={(e) => setFilters({ pixelMaxKm: numberOrNull(e.target.value) })}
            />
          </label>
          <label>
            Emission ≤ °{' '}
            <input
              data-testid="filter-emission-max"
              type="number"
              value={filters.emissionMax ?? ''}
              placeholder="Any"
              style={{ width: 85 }}
              onChange={(e) => setFilters({ emissionMax: numberOrNull(e.target.value) })}
            />
          </label>
          <label>
            On planet ≥{' '}
            <input
              data-testid="filter-on-planet-min"
              type="number"
              min="0"
              max="1"
              step="0.05"
              value={filters.onPlanetMin ?? ''}
              placeholder="Any"
              style={{ width: 85 }}
              onChange={(e) => setFilters({ onPlanetMin: numberOrNull(e.target.value) })}
            />
          </label>
          <label>
            From (UTC){' '}
            <input
              type="date"
              value={filters.timeMin ? new Date(filters.timeMin).toISOString().slice(0, 10) : ''}
              onChange={(e) =>
                setFilters({ timeMin: e.target.value ? Date.parse(e.target.value) : null })
              }
            />
          </label>
          <label>
            Through (UTC){' '}
            <input
              type="date"
              value={filters.timeMax ? new Date(filters.timeMax).toISOString().slice(0, 10) : ''}
              onChange={(e) =>
                setFilters({
                  timeMax: e.target.value ? Date.parse(e.target.value) + 86399999 : null,
                })
              }
            />
          </label>
          <label>
            <input
              data-testid="filter-dayside"
              type="checkbox"
              checked={filters.daysideOnly}
              onChange={(e) => setFilters({ daysideOnly: e.target.checked })}
            />
            Dayside only
          </label>
          {hasTrackability && !junocam && (
            <label>
              <input
                data-testid="filter-revisit"
                type="checkbox"
                checked={filters.revisitOnly}
                onChange={(e) =>
                  setFilters({
                    revisitOnly: e.target.checked,
                    instrument: e.target.checked ? 'JIRAM' : filters.instrument,
                  })
                }
              />
              JIRAM same-pass revisit
            </label>
          )}
        </div>
        <p className={styles.muted}>
          Missing emission or resolution measurements remain included. Latitude is planetocentric;
          footprint search includes swaths whose centres lie outside the region.
        </p>
      </details>
      {junocam && (
        <p className={styles.muted} data-testid="junocam-policy-note">
          JunoCam trackability is unassessed. Only observations eligible under the
          instrument-failure policy are shown; archive metadata is available in Coverage.
        </p>
      )}
    </div>
  );
}
