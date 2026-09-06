import type { ReactNode } from 'react';
import styles from './App.module.css';

export function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  return (
    <div className={styles.modalBackdrop} onClick={onClose} role="presentation">
      <div
        className={styles.modal}
        role="dialog"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className={styles.row} style={{ justifyContent: 'space-between', marginBottom: 8 }}>
          <h2>{title}</h2>
          <button aria-label="close dialog" onClick={onClose}>
            close
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
