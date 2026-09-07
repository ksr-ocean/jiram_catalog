import { useEffect, useState } from 'react';
import { Modal } from '../components/Modal';
import { useStore } from '../store/store';
import { researchApi, type Matches } from '../api/research';
import { humanProduct } from '../lib/labels';
import styles from './Views.module.css';
export function DetailCard() {
  const detail = useStore((s) => s.detail),
    close = useStore((s) => s.closeDetail),
    open = useStore((s) => s.openDetail),
    keyIndex = useStore((s) => s.keyIndex),
    add = useStore((s) => s.addToSelection);
  const [matches, setMatches] = useState<Matches | null>(null),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [previewError, setPreviewError] = useState(false);
  useEffect(() => {
    setMatches(null);
    setError('');
    setPreviewError(false);
  }, [detail?.product_id]);
  if (!detail) return null;
  const quality = detail.quality_assessment as { status?: string; reasons?: string[] } | undefined;
  const allowed =
    quality?.status === 'eligible' ||
    (detail.instrument === 'JIRAM' && quality?.status !== 'excluded');
  const bands = Array.isArray(detail.bands)
    ? (detail.bands as string[])
    : String(detail.bands ?? '')
        .split(';')
        .filter(Boolean);
  const versions = Array.isArray(detail.versions) ? (detail.versions as string[]) : [];
  const productId = detail.product_id;
  const indices = (detail.halves ?? ['']).flatMap((half) => {
    const index = keyIndex.get(`${productId}|${half}`);
    return index === undefined ? [] : [index];
  });
  return (
    <Modal title={humanProduct(productId, Number(detail.orbit))} onClose={close}>
      <div data-testid="frame-detail">
        <h3>Observation identity</h3>
        <p className={styles.identifier}>{productId}</p>
        <div className={styles.toolbar}>
          {Boolean(detail.source_url) && (
            <a href={String(detail.source_url)} target="_blank" rel="noreferrer">
              PDS source
            </a>
          )}
          {Boolean(detail.label_url) && (
            <a href={String(detail.label_url)} target="_blank" rel="noreferrer">
              PDS label
            </a>
          )}
          <span>Version {String(detail.version ?? detail.native_version ?? '—')}</span>
        </div>
        <p>
          <b>{quality?.status ?? 'Quality unassessed'}</b> ·{' '}
          {(quality?.reasons ?? []).join('; ') ||
            String(detail.rationale ?? 'See processing evidence below.')}
        </p>
        {allowed && !previewError ? (
          <figure>
            <img
              src={`/api/catalog/frame/${encodeURIComponent(productId)}/thumbnail.png${bands[0] ? `?band=${encodeURIComponent(bands[0])}` : ''}`}
              alt={`Instrument framelets for ${productId}`}
              style={{ maxWidth: '100%', maxHeight: 260, objectFit: 'contain' }}
              onError={() => setPreviewError(true)}
            />
            <figcaption className={styles.muted}>
              {String(detail.thumbnail_kind ?? 'Instrument preview')} · {bands[0] ?? 'native band'}{' '}
              · display preview, not a map
            </figcaption>
          </figure>
        ) : (
          <p className={styles.muted}>
            {allowed
              ? 'No local instrument preview is available.'
              : 'Pixels are withheld by the instrument quality policy. Exclusion metadata remains available.'}
          </p>
        )}
        <p>
          Independent observation: <code>{String(detail.observation_id ?? productId)}</code>
        </p>
        {versions.length > 1 && (
          <label>
            Processing version{' '}
            <select value={productId} onChange={(e) => void open(e.target.value)}>
              {versions.map((version) => (
                <option key={version} value={version}>
                  {version}
                </option>
              ))}
            </select>
          </label>
        )}
        <details>
          <summary>Processing and provenance</summary>
          <pre>
            {JSON.stringify(
              {
                quality_assessment: quality,
                provenance: detail.provenance,
                rationale: detail.rationale,
              },
              null,
              2,
            )}
          </pre>
        </details>
        <details>
          <summary>All catalog metadata</summary>
          <div className={styles.kv}>
            {Object.entries(detail)
              .filter(([, value]) => typeof value !== 'object' || value === null)
              .map(([name, value]) => (
                <div style={{ display: 'contents' }} key={name}>
                  <span>{name.replace(/_/g, ' ')}</span>
                  <b>{value === null ? '—' : String(value)}</b>
                </div>
              ))}
          </div>
        </details>
        <div className={styles.toolbar}>
          <button
            data-testid="detail-add-to-selection"
            disabled={!allowed || !indices.length}
            onClick={() => {
              add(Uint32Array.from(indices));
              close();
            }}
          >
            Add observation to selection
          </button>
          <button
            disabled={busy || !allowed}
            onClick={() => {
              setBusy(true);
              setError('');
              researchApi
                .matches(productId)
                .then(setMatches)
                .catch((e) => setError(e.message))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? 'Finding context…' : 'Find cross-instrument context'}
          </button>
        </div>
        {error && <p role="alert">{error}</p>}
        {matches && (
          <section>
            <h3>Candidate context within one hour</h3>
            <p>
              {Array.isArray(matches.limitations)
                ? matches.limitations.join(' ')
                : matches.limitations}
            </p>
            {matches.items.length === 0 ? (
              <p>No overlapping candidates satisfy the time and footprint limits.</p>
            ) : (
              <div className={styles.tableWrap}>
                <table>
                  <thead>
                    <tr>
                      <th>Observation</th>
                      <th>Δt (s)</th>
                      <th>Overlap</th>
                      <th>km/px</th>
                    </tr>
                  </thead>
                  <tbody>
                    {matches.items.map((item) => (
                      <tr key={item.product_id}>
                        <td>
                          <button onClick={() => void open(item.product_id)}>
                            {item.instrument} · {item.product_id}
                          </button>
                        </td>
                        <td>{item.dt_s.toFixed(1)}</td>
                        <td>
                          {(item.overlap_fraction * 100).toFixed(1)}% ({item.overlap_method})
                        </td>
                        <td>{item.native_pixel_km ?? '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        )}
      </div>
    </Modal>
  );
}
