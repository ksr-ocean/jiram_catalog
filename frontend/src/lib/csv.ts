/** Client-side CSV export: no server round trip, no temporary file. */

export function toCsv(header: string[], rows: (string | number)[][]): string {
  const escape = (value: string | number): string => {
    const text = String(value ?? '');
    return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [header.map(escape).join(','), ...rows.map((row) => row.map(escape).join(','))].join('\n');
}

export function downloadText(filename: string, text: string, type = 'text/csv'): void {
  const blob = new Blob([text], { type });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
