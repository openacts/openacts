import type { ApiResponse, SearchData, SearchItem } from "./contracts";

// The API normalizes to NFC, strips, then measures 1–256 CODE POINTS. The same
// rules are applied here so the message a person sees agrees with the one the
// API would return, and so String.length (UTF-16) never disagrees with it.
export const SEARCH_MAX_CODE_POINTS = 256;

export function normalizeQuery(raw: string): string {
  return raw.normalize("NFC").trim();
}

export function queryLength(raw: string): number {
  return Array.from(normalizeQuery(raw)).length;
}

export function validateQuery(raw: string): string | null {
  const length = queryLength(raw);
  if (length === 0) {
    return "Type an Act title, a citation, a section number, or wording you remember.";
  }
  if (length > SEARCH_MAX_CODE_POINTS) {
    return `A search can be at most ${SEARCH_MAX_CODE_POINTS} characters. This one is ${length}.`;
  }
  return null;
}

const EXACT_MATCH_KINDS = new Set<SearchItem["match_kind"]>([
  "exact_act_id",
  "exact_provision_id",
  "exact_act_citation",
  "exact_act_title",
  "exact_act_alias",
  "exact_provision_reference",
]);

export function isExactMatch(item: SearchItem): boolean {
  return EXACT_MATCH_KINDS.has(item.match_kind);
}

// Why a result matched, in the reader's words. No score is shown, because the
// API deliberately does not expose one.
const MATCH_REASON: Record<SearchItem["match_kind"], string> = {
  exact_act_id: "matched an Act identifier",
  exact_provision_id: "matched a Provision identifier",
  exact_act_citation: "matched an Act citation",
  exact_act_title: "matched an Act title",
  exact_act_alias: "matched a known name for this Act",
  exact_provision_reference: "matched a Provision reference",
  lexical: "contains your words",
};

export function matchReason(item: SearchItem): string {
  return MATCH_REASON[item.match_kind];
}

export interface SearchSplit {
  exact: SearchItem[];
  lexical: SearchItem[];
}

// The API already returns exact hits ahead of lexical ones; splitting keeps the
// two visibly distinguished rather than blending them into one ranked list.
export function splitResults(items: readonly SearchItem[]): SearchSplit {
  return {
    exact: items.filter(isExactMatch),
    lexical: items.filter((item) => !isExactMatch(item)),
  };
}

export type SearchOutcome =
  | { status: "results"; items: SearchItem[] }
  | { status: "invalid"; message: string }
  | { status: "unavailable"; message: string };

function apiBase(): string | null {
  return process.env.NEXT_PUBLIC_OPENACTS_API_URL ?? null;
}

// Called from the browser. The query travels in a bounded POST body and is
// never placed in a URL, storage, or a log (frontend-spec §2, decision 0011).
export async function runSearch(
  raw: string,
  signal: AbortSignal,
): Promise<SearchOutcome> {
  const invalid = validateQuery(raw);
  if (invalid) {
    return { status: "invalid", message: invalid };
  }

  const base = apiBase();
  if (!base) {
    return { status: "unavailable", message: "Search is not configured." };
  }

  const response = await fetch(
    new URL("/v1/search", base.endsWith("/") ? base : `${base}/`),
    {
      method: "POST",
      cache: "no-store",
      signal,
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ query: normalizeQuery(raw), limit: 20 }),
    },
  );

  if (!response.ok) {
    if (response.status === 400) {
      return {
        status: "invalid",
        message: "That search could not be read. Try different wording.",
      };
    }
    return {
      status: "unavailable",
      message: "The corpus could not be searched just now.",
    };
  }

  const body = (await response.json()) as ApiResponse<SearchData>;
  return { status: "results", items: body.data.items };
}

// `excerpt` is the head of the projection's derived searchable text, which for
// a short Provision is just its label and heading again — the result would then
// state the same thing twice. Whitespace is collapsed because this is generated
// search text, not canonical wording, so no source line break is being lost.
export function excerptBeyondTitle(
  excerpt: string | null,
  title: string,
): string | null {
  if (!excerpt) {
    return null;
  }

  const collapse = (value: string) => value.replace(/\s+/g, " ").trim();
  const text = collapse(excerpt);
  const heading = collapse(title);

  if (text.length === 0 || heading.includes(text)) {
    return null;
  }
  if (text.startsWith(heading)) {
    const rest = text.slice(heading.length).trim();
    return rest.length > 0 ? rest : null;
  }
  return text;
}
