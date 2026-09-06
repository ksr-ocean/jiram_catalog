/** Small display helpers shared by the tray, the tables and the tooltips. */

export function fmt(value: unknown, digits = 2): string {
  if (value === null || value === undefined) return '--';
  const n = typeof value === 'bigint' ? Number(value) : Number(value);
  if (!Number.isFinite(n)) return '--';
  return n.toFixed(digits);
}

export function fmtTime(ms: number): string {
  if (!Number.isFinite(ms)) return '--';
  return new Date(ms).toISOString().replace('T', ' ').slice(0, 19);
}

export function fmtBytes(bytes: number): string {
  if (!Number.isFinite(bytes)) return '--';
  const units = ['B', 'kB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function fmtRange(low: number, high: number, digits = 1): string {
  if (!Number.isFinite(low) || !Number.isFinite(high)) return '--';
  return `${low.toFixed(digits)} to ${high.toFixed(digits)}`;
}

/** Compact run-length form for a sorted orbit list: `4-6, 11, 20`. */
export function fmtOrbits(orbits: number[]): string {
  if (orbits.length === 0) return '--';
  const parts: string[] = [];
  let start = orbits[0];
  let previous = orbits[0];
  for (const orbit of orbits.slice(1)) {
    if (orbit === previous + 1) {
      previous = orbit;
      continue;
    }
    parts.push(start === previous ? `${start}` : `${start}-${previous}`);
    start = orbit;
    previous = orbit;
  }
  parts.push(start === previous ? `${start}` : `${start}-${previous}`);
  return parts.join(', ');
}
