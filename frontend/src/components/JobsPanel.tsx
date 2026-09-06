/** The jobs indicator and its list: the only long-running work the GUI starts. */
import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import styles from './App.module.css';
import { useStore } from '../store/store';
import { api } from '../api/client';

export function JobsIndicator() {
  const jobs = useStore((s) => s.jobs);
  const pollJobs = useStore((s) => s.pollJobs);
  const [open, setOpen] = useState(false);
  const active = jobs.filter((job) => job.status === 'running' || job.status === 'queued').length;

  useEffect(() => {
    void pollJobs();
    const timer = setInterval(() => void pollJobs(), 2000);
    return () => clearInterval(timer);
  }, [pollJobs]);

  return (
    <>
      <button data-testid="jobs-button" aria-label="jobs" onClick={() => setOpen(true)}>
        jobs {active > 0 ? `(${active} running)` : `(${jobs.length})`}
      </button>
      {open && (
        <Modal title="Background jobs" onClose={() => setOpen(false)}>
          {jobs.length === 0 ? (
            <p style={{ color: 'var(--muted)' }}>No jobs have been started in this session.</p>
          ) : (
            <table data-testid="jobs-table">
              <thead>
                <tr>
                  <th>kind</th>
                  <th>status</th>
                  <th>progress</th>
                  <th>message</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td>{job.kind}</td>
                    <td className={job.status === 'failed' ? styles.error : undefined}>{job.status}</td>
                    <td>{Math.round((job.progress ?? 0) * 100)}%</td>
                    <td style={{ whiteSpace: 'normal', maxWidth: 320 }}>{job.message ?? ''}</td>
                    <td>
                      {job.status === 'queued' && (
                        <button onClick={() => void api.cancelJob(job.id).then(() => pollJobs())}>cancel</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Modal>
      )}
    </>
  );
}
