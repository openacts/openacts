// GET /v1/acts caps limit at 100 and rejects limit < 1 with 400 invalid_request.
export const ACTS_PAGE_SIZE = 50;

// searchParams values are user-controlled and may arrive repeated. Anything that
// is not a non-negative integer resets to the first page rather than reaching
// the API and coming back as a 400.
export function parseOffset(value: string | string[] | undefined): number {
  const raw = Array.isArray(value) ? value[0] : value;
  if (raw === undefined || raw.trim() === "") {
    return 0;
  }

  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed) || parsed < 0) {
    return 0;
  }
  return parsed;
}

export function previousOffset(offset: number, limit: number): number | null {
  return offset <= 0 ? null : Math.max(0, offset - limit);
}

export function nextOffset(
  offset: number,
  limit: number,
  total: number,
): number | null {
  const next = offset + limit;
  return next < total ? next : null;
}
