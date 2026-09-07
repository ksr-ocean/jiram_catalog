import styles from './App.module.css';
import { useStore } from '../store/store';
import { JobsIndicator } from './JobsPanel';

export function TopBar() {
  const config = useStore((s) => s.config);
  const loading = useStore((s) => s.loading);
  const busy = Object.entries(loading)
    .filter(([, value]) => value)
    .map(([key]) => key);

  return (
    <header className={styles.topbar}>
      <span className={styles.brand}>Juno science workspace</span>
      <span className={styles.meta} data-testid="config-meta">
        {config ? (
          <>

            <span>{config.counts.frames_on_planet.toLocaleString()} frames on planet</span>
            {config.counts.junocam_images ? (
              <span data-testid="config-junocam">
                {config.counts.junocam_images.toLocaleString()} JunoCam images
              </span>
            ) : null}
            <span>{config.counts.stacks} stacks</span>
            <span>{config.counts.strips} strips</span>

            <span>v{config.version}</span>
          </>
        ) : (
          <span>connecting to the API...</span>
        )}
      </span>
      <span className={styles.spacer} />
      {busy.length > 0 && (
        <span className={styles.spinner} data-testid="busy">
          loading {busy.join(', ')}...
        </span>
      )}
      <JobsIndicator />
    </header>
  );
}
