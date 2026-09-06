import styles from './App.module.css';
import { useStore } from '../store/store';
import { useEffect } from 'react';

/** Every failed request lands here; nothing blocks the interface. */
export function Toasts() {
  const toasts = useStore((s) => s.toasts);
  const dismiss = useStore((s) => s.dismissToast);

  useEffect(() => {
    if (toasts.length === 0) return;
    const timer = setTimeout(() => dismiss(toasts[0].id), 8000);
    return () => clearTimeout(timer);
  }, [toasts, dismiss]);

  if (toasts.length === 0) return null;
  return (
    <div className={styles.toasts} role="status" aria-live="polite" data-testid="toasts">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={`${styles.toast} ${toast.kind === 'error' ? styles.toastError : ''}`}
        >
          {toast.text}{' '}
          <button aria-label="dismiss" onClick={() => dismiss(toast.id)}>
            x
          </button>
        </div>
      ))}
    </div>
  );
}
