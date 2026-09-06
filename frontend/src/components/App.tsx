import { useEffect } from 'react';
import styles from './App.module.css';
import { TopBar } from './TopBar';
import { SelectionTray } from './SelectionTray';
import { Toasts } from './Toasts';
import { DebugState } from './DebugState';
import { CatalogView } from '../views/CatalogView';
import { PolesView } from '../views/PolesView';
import { StripsView } from '../views/StripsView';
import { useStore, type TabName } from '../store/store';

const TABS: { id: TabName; label: string }[] = [
  { id: 'catalog', label: 'Catalog' },
  { id: 'poles', label: 'Poles' },
  { id: 'strips', label: 'Strips' },
];

export function App() {
  const tab = useStore((s) => s.tab);
  const setTab = useStore((s) => s.setTab);
  const init = useStore((s) => s.init);

  useEffect(() => {
    void init();
  }, [init]);

  return (
    <div className={styles.app}>
      <TopBar />
      <div className={styles.body}>
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
        </main>
        <SelectionTray />
      </div>
      <Toasts />
      <DebugState />
    </div>
  );
}
