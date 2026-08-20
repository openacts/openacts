import { unstable_rethrow } from "next/navigation";
import { cache } from "react";

import type { ApiErrorResponse, ApiResponse } from "./contracts";
import type {
  ActContentsData,
  ActDetail,
  ActSummaryListData,
  ProvisionDetail,
  SourceDetailData,
} from "./contracts";

export class OpenActsApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly retryable: boolean,
    readonly requestId: string | null,
    options?: ErrorOptions,
  ) {
    super(message, options);
    this.name = "OpenActsApiError";
  }
}

function apiUrl(path: string): URL {
  const baseUrl = process.env.NEXT_PUBLIC_OPENACTS_API_URL;
  if (!baseUrl) {
    throw new Error("NEXT_PUBLIC_OPENACTS_API_URL is required");
  }

  const url = new URL(path, baseUrl.endsWith("/") ? baseUrl : `${baseUrl}/`);
  if (url.protocol !== "http:" && url.protocol !== "https:") {
    throw new Error("NEXT_PUBLIC_OPENACTS_API_URL must use HTTP or HTTPS");
  }
  return url;
}

function isApiErrorResponse(value: unknown): value is ApiErrorResponse {
  if (!value || typeof value !== "object" || !("error" in value)) {
    return false;
  }
  const error = value.error;
  return (
    !!error &&
    typeof error === "object" &&
    "code" in error &&
    typeof error.code === "string" &&
    "message" in error &&
    typeof error.message === "string" &&
    "retryable" in error &&
    typeof error.retryable === "boolean" &&
    "request_id" in error &&
    typeof error.request_id === "string"
  );
}

// Decision 0024: corpus reads are cached and invalidated by release activation,
// never by a timer, because nothing changes on a timer. Every read carries the
// `corpus` tag; Act-scoped reads also carry `act:<act_id>`.
export const CORPUS_TAG = "corpus";

export function actTag(actId: string): string {
  return `act:${actId}`;
}

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
  tags: readonly string[] = [CORPUS_TAG],
): Promise<ApiResponse<T>> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }

  const url = apiUrl(path);
  let response: Response;
  try {
    response = await fetch(url, {
      next: { revalidate: false, tags: [...tags] },
      ...init,
      headers,
    });
  } catch (cause) {
    // Next throws notFound/redirect/dynamic-bailout through fetch.
    unstable_rethrow(cause);
    throw new OpenActsApiError(
      0,
      "network_error",
      "OpenActs API is unavailable.",
      true,
      null,
      { cause },
    );
  }

  if (!response.ok) {
    let body: unknown;
    try {
      body = await response.json();
    } catch {
      body = null;
    }

    if (isApiErrorResponse(body)) {
      throw new OpenActsApiError(
        response.status,
        body.error.code,
        body.error.message,
        body.error.retryable,
        body.error.request_id,
      );
    }

    throw new OpenActsApiError(
      response.status,
      "api_error",
      "OpenActs API returned an unexpected response.",
      response.status >= 500,
      null,
    );
  }

  return (await response.json()) as ApiResponse<T>;
}

export function apiPath(path: string, params?: Record<string, string | number>): string {
  const pathWithQuery = new URL(path, "http://localhost");
  if (params && Object.keys(params).length > 0) {
    Object.entries(params).forEach(([key, value]) => {
      pathWithQuery.searchParams.set(key, String(value));
    });
  }
  return `${pathWithQuery.pathname}${pathWithQuery.search}`;
}

export function encodePathSegment(value: string): string {
  return encodeURIComponent(value);
}

// cache() dedupes within one render pass, so generateMetadata and the page body
// share a single request instead of calling the API twice per navigation.
export const fetchActs = cache(async (offset = 0, limit = 50) => {
  const path = apiPath("/v1/acts", { offset, limit });
  return apiRequest<ActSummaryListData>(path, {}, [CORPUS_TAG]);
});

export const fetchActDetail = cache(async (actId: string) => {
  const encoded = encodePathSegment(actId);
  return apiRequest<ActDetail>(`/v1/acts/${encoded}`, {}, [
    CORPUS_TAG,
    actTag(actId),
  ]);
});

// Positional primitives rather than an options object: cache() compares
// arguments by identity, and a fresh object literal per call would defeat it.
// Unfiltered, this returns every node of the Act — 803 KB for the Constitution
// — so callers that need one level ask for it.
export const fetchActContents = cache(
  async (actId: string, parentProvisionId?: string, maxDepth?: number) => {
    const encoded = encodePathSegment(actId);
    const params: Record<string, string | number> = {};
    if (parentProvisionId !== undefined) {
      params.parent_provision_id = parentProvisionId;
    }
    if (maxDepth !== undefined) {
      params.max_depth = maxDepth;
    }
    const path = apiPath(`/v1/acts/${encoded}/contents`, params);
    return apiRequest<ActContentsData>(path, {}, [CORPUS_TAG, actTag(actId)]);
  },
);

export const fetchProvision = cache(async (provisionId: string) => {
  const encoded = encodePathSegment(provisionId);
  // The Act half of a Provision id is its owning Act; see decision 0022.
  const colon = provisionId.indexOf(":");
  const tags =
    colon > 0
      ? [CORPUS_TAG, actTag(provisionId.slice(0, colon))]
      : [CORPUS_TAG];
  return apiRequest<ProvisionDetail>(`/v1/provisions/${encoded}`, {}, tags);
});

// Sources are corpus-global and owned by no Act (decision 0016).
export const fetchSource = cache(async (sourceId: string) => {
  const encoded = encodePathSegment(sourceId);
  return apiRequest<SourceDetailData>(`/v1/sources/${encoded}`, {}, [
    CORPUS_TAG,
  ]);
});
