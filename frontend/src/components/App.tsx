import { useEffect, useState } from 'react';
import styles from './App.module.css';
import { TopBar } from './TopBar';
import { SelectionTray } from './SelectionTray';
import { Toasts } from './Toasts';
import { DebugState } from './DebugState';
import { CatalogView } from '../views/CatalogView';
import { PolesView } from '../views/PolesView';
import { CoverageView } from '../views/CoverageView';
import { CompareView } from '../views/CompareView';
import { loadPersisted, savePersisted } from '../store/persist';
import { DetailCard } from '../views/DetailCard';
import { StripsView } from '../views/StripsView';
import { useStore, type TabName } from '../store/store';

const TABS: { id: TabName; label: string }[] = [
  { id: 'catalog', label: 'Explore' },
  { id: 'poles', label: 'Time series' },
  { id: 'strips', label: 'Image library' },
  { id: 'compare', label: 'Compare' },
  { id: 'coverage', label: 'Coverage' },
];

export function App() {
  const [trayOpen, setTrayOpen] = useState(() => loadPersisted('tray', { open: true }).open);
  const count = useStore((s) => s.selectionKeys.size);
  const tab = useStore((s) => s.tab);
  const setTab = useStore((s) => s.setTab);
  const init = useStore((s) => s.init);

  useEffect(() => {
    void init();
  }, [init]);

  return (
    <div className={styles.app}>
      <TopBar />
      <div
        className={styles.body}
        style={{
          gridTemplateColumns: trayOpen ? '126px minmax(0,1fr) 254px' : '126px minmax(0,1fr)',
        }}
      >
        <nav className={styles.tabs} aria-label="views">
          {TABS.map(({ id, label }) => (
            <button
              key={id}
              className={`${styles.tab} ${tab === id ? styles.tabActive : ''}`}
              aria-pressed={tab === id}
              data-testid={`tab-${id}`}
              onClick={() => setTab(id)}
            >
              {label}
            </button>
          ))}
          <button
            className={styles.trayToggle}
            data-testid="toggle-selection-tray"
            aria-expanded={trayOpen}
            onClick={() => {
              setTrayOpen(!trayOpen);
              savePersisted('tray', { open: !trayOpen });
            }}
          >
            {trayOpen ? 'Hide selection' : 'Selection'} ({count})
          </button>
        </nav>
        <main className={styles.main} data-testid={`view-${tab}`}>
          {/* All three views stay mounted: switching tabs must not throw away
              a loaded stack, and v1's "panels stopped repainting" came from
              rebuilding them.  Only the active one is displayed. */}
          <div style={{ display: tab === 'catalog' ? 'block' : 'none', height: '100%' }}>
            <CatalogView active={tab === 'catalog'} />
          </div>
          <div style={{ display: tab === 'poles' ? 'block' : 'none', height: '100%' }}>
            <PolesView active={tab === 'poles'} />
          </div>
          <div style={{ display: tab === 'strips' ? 'block' : 'none', height: '100%' }}>
            <StripsView active={tab === 'strips'} />
          </div>
          <div style={{ display: tab === 'coverage' ? 'block' : 'none', height: '100%' }}>
            <CoverageView active={tab === 'coverage'} />
          </div>
          <div style={{ display: tab === 'compare' ? 'block' : 'none', height: '100%' }}>
            <CompareView active={tab === 'compare'} />
          </div>
        </main>
        {trayOpen && <SelectionTray />}
      </div>
      <DetailCard />
      <Toasts />
      <DebugState />
    </div>
  );
}
