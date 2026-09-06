/** localStorage helpers that never throw (private windows, quota, tests). */
const PREFIX = 'jiram.v2.';

export function loadPersisted<T>(key: string, fallback: T): T {
  try {
    const raw = globalThis.localStorage?.getItem(PREFIX + key);
    if (!raw) return fallback;
    return { ...fallback, ...(JSON.parse(raw) as object) } as T;
  } catch {
    return fallback;
  }
}

export function savePersisted(key: string, value: unknown): void {
  try {
    globalThis.localStorage?.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* persistence is a convenience, never a requirement */
  }
}
