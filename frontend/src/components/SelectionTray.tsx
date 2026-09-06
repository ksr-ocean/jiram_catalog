/**
 * The selection tray: the object every view writes into.
 *
 * v1 had "Send to Poles" and "Send to Strips" buttons whose effect was
 * invisible -- the user could not see what had been sent, or that anything
 * had.  Here the selection is a permanent column of the window: what is in
 * it, where it came from, and the two things you can do with it.
 */
import { useMemo, useState } from 'react';
import styles from './App.module.css';
import { Modal } from './Modal';
import { api } from '../api/client';
import { fmtOrbits, fmtRange } from '../lib/format';
import { selectionStats, useStore } from '../store/store';

/** The four names in `configs/regions.yaml`, plus whatever stacks exist. */
const REGISTRY_REGIONS = ['north_pole_paper', 'north_pole', 'south_pole', 'neb_15n'];

export function SelectionTray() {
  const columns = useStore((s) => s.columns);
  const keyIndex = useStore((s) => s.keyIndex);
  const selectionKeys = useStore((s) => s.selectionKeys);
  const savedSelections = useStore((s) => s.savedSelections);
  const selectionName = useStore((s) => s.selectionName);
  const setSelectionName = useStore((s) => s.setSelectionName);
  const saveSelection = useStore((s) => s.saveSelection);
  const loadSelection = useStore((s) => s.loadSelection);
  const deleteSelection = useStore((s) => s.deleteSelection);
  const clearSelection = useStore((s) => s.clearSelection);
  const selectAllFiltered = useStore((s) => s.selectAllFiltered);
  const filtered = useStore((s) => s.filtered);
  const stacks = useStore((s) => s.stacks);
  const setTab = useStore((s) => s.setTab);
  const setStripOrbitFilter = useStore((s) => s.setStripOrbitFilter);
  const pushToast = useStore((s) => s.pushToast);
  const watchJob = useStore((s) => s.watchJob);
  const loadStacks = useStore((s) => s.loadStacks);

  const [buildOpen, setBuildOpen] = useState(false);

  const stats = useMemo(
    () => selectionStats(columns, keyIndex, selectionKeys),
    [columns, keyIndex, selectionKeys],
  );

  const regions = useMemo(() => {
    const names = new Set(REGISTRY_REGIONS);
    for (const stack of stacks) names.add(stack.region);
    return [...names].sort();
  }, [stacks]);

  return (
    <aside className={styles.tray} data-testid="selection-tray" aria-label="Selection tray">
      <h3>Working selection</h3>
      <div className={styles.trayStat}>
        <span>frames</span>
        <b data-testid="selection-count">{stats.n.toLocaleString()}</b>
      </div>
      <div className={styles.trayStat}>
        <span>orbits</span>
        <b>{fmtOrbits(stats.orbits)}</b>
      </div>
      <div className={styles.trayStat}>
        <span>latitude</span>
        <b>{fmtRange(stats.latMin, stats.latMax)}</b>
      </div>
      <div className={styles.trayStat}>
        <span>band halves</span>
        <b>{stats.bands.length ? stats.bands.join(', ') : '--'}</b>
      </div>

      <div className={styles.row}>
        <button data-testid="select-all-filtered" onClick={selectAllFiltered} disabled={filtered.length === 0}>
          all {filtered.length.toLocaleString()} filtered
        </button>
        <button data-testid="clear-selection" onClick={clearSelection} disabled={stats.n === 0}>
          clear
        </button>
      </div>

      <label htmlFor="selection-name">name</label>
      <input
        id="selection-name"
        data-testid="selection-name"
        value={selectionName}
        placeholder="north polar, orbits 4-6"
        onChange={(event) => setSelectionName(event.target.value)}
      />
      <button data-testid="save-selection" onClick={() => void saveSelection()} disabled={stats.n === 0}>
        Save selection
      </button>

      <h3>Actions</h3>
      <button data-testid="open-build-stack" onClick={() => setBuildOpen(true)} disabled={stats.n === 0}>
        Build stack...
      </button>
      <button
        data-testid="show-in-strips"
        disabled={stats.n === 0}
        onClick={() => {
          setStripOrbitFilter(stats.orbits);
          setTab('strips');
        }}
      >
        Show in Strips
      </button>

      <h3>Saved selections</h3>
      <div className={styles.savedList} data-testid="saved-selections">
        {savedSelections.length === 0 && (
          <div className={styles.savedItem} style={{ color: 'var(--muted)' }}>
            none yet
          </div>
        )}
        {savedSelections.map((record) => (
          <div className={styles.savedItem} key={record.id}>
            <span className={styles.savedName} title={`${record.n_frames} frames, ${record.n_orbits} orbits`}>
              {record.name}
            </span>
            <button aria-label={`load ${record.name}`} onClick={() => void loadSelection(record.id)}>
              load
            </button>
            <button aria-label={`delete ${record.name}`} onClick={() => void deleteSelection(record.id)}>
              del
            </button>
          </div>
        ))}
      </div>

      {buildOpen && (
        <BuildStackDialog
          regions={regions}
          onClose={() => setBuildOpen(false)}
          onSubmit={async (region, band, level, maxEmission) => {
            const record = await saveSelection();
            if (!record) return;
            try {
              const { job_id } = await api.buildStack({
                region,
                band,
                level,
                selection_id: record.id,
                max_emission: maxEmission,
              });
              pushToast('info', `stack build started (job ${job_id})`);
              watchJob(job_id, (job) => {
                pushToast(job.status === 'done' ? 'info' : 'error', `stack build ${job.status}: ${job.message ?? ''}`);
                void loadStacks();
              });
            } catch (error) {
              pushToast('error', `build stack: ${(error as Error).message}`);
            }
            setBuildOpen(false);
          }}
        />
      )}
    </aside>
  );
}

function BuildStackDialog({
  regions,
  onClose,
  onSubmit,
}: {
  regions: string[];
  onClose: () => void;
  onSubmit: (region: string, band: string, level: string, maxEmission: number) => Promise<void>;
}) {
  const [region, setRegion] = useState(regions[0] ?? 'north_pole_paper');
  const [band, setBand] = useState('M');
  const [level, setLevel] = useState('sequence');
  const [maxEmission, setMaxEmission] = useState(80);

  return (
    <Modal title="Build a stack from the selection" onClose={onClose}>
      <p style={{ color: 'var(--muted)', marginTop: 0 }}>
        The selection is saved first, and the job is restricted to its product ids.
      </p>
      <div className={styles.row}>
        <label htmlFor="build-region">region</label>
        <select id="build-region" value={region} onChange={(event) => setRegion(event.target.value)}>
          {regions.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </div>
      <div className={styles.row}>
        <label htmlFor="build-band">band</label>
        <select id="build-band" value={band} onChange={(event) => setBand(event.target.value)}>
          <option value="M">M</option>
          <option value="L">L</option>
        </select>
        <label htmlFor="build-level">level</label>
        <select id="build-level" value={level} onChange={(event) => setLevel(event.target.value)}>
          <option value="sequence">sequence</option>
          <option value="frame">frame</option>
        </select>
      </div>
      <div className={styles.row}>
        <label htmlFor="build-emission">max emission (deg)</label>
        <input
          id="build-emission"
          type="number"
          min={0}
          max={90}
          value={maxEmission}
          onChange={(event) => setMaxEmission(Number(event.target.value))}
        />
      </div>
      <div className={styles.row} style={{ marginTop: 10 }}>
        <button data-testid="submit-build" onClick={() => void onSubmit(region, band, level, maxEmission)}>
          Start build
        </button>
        <button onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}
