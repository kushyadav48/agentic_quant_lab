/** Exact presentation only; never round or compute authoritative financial totals. */
export function decimal(value: unknown): string {
  if (value === null || value === undefined) return 'Unavailable';
  if (typeof value !== 'string' || !/^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$/.test(value)) return 'Invalid decimal contract';
  // Scientific notation remains exact and is never expanded through Number.
  if (/[eE]/.test(value)) return value;
  const [integer, fraction] = value.split('.');
  return integer.replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (fraction === undefined ? '' : '.' + fraction);
}
export function utc(value: string): string {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return 'Invalid UTC timestamp';
  const fraction = value.match(/\.(\d+)(?:Z|[+-]\d{2}:\d{2})$/)?.[1];
  let normalized = date.toISOString();
  if (fraction) normalized = normalized.replace(/\.\d{3}Z$/, `.${fraction}Z`);
  return normalized.replace('T', ' ').replace('Z', ' UTC');
}
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : 'The request could not be completed.';
}
