export function humanRegion(id: string): string {
  const known: Record<string, string> = {
    north_pole_paper: 'North pole · published reference grid',
    north_pole: 'North pole',
    south_pole: 'South pole',
    neb_15n: 'North Equatorial Belt · 15°N',
  };
  return known[id] ?? id.replace(/_/g, ' ').replace(/\b\w/g, (s) => s.toUpperCase());
}
export function observationId(id: string): string {
  return id.replace(/_V\d+$/i, '');
}
export function humanProduct(id: string, orbit?: number, time?: number): string {
  const instrument = /JNC|JUNO/i.test(id) ? 'JunoCam' : 'JIRAM';
  const sequence = id.match(/_(\d{5})_V\d+$/)?.[1];
  return `${instrument}${Number.isFinite(orbit) ? ` · PJ${orbit}` : ''}${sequence ? ` · image ${Number(sequence)}` : Number.isFinite(time) ? ` · ${new Date(time!).toISOString().slice(11, 19)} UTC` : ''}`;
}
