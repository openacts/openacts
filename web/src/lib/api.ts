import { unstable_rethrow } from "next/navigation";

import type { ApiErrorResponse, ApiResponse } from "./contracts";
import type {
  ActContentsData,
  ActDetail,
  ActSummaryListData,
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

export async function apiRequest<T>(
  path: string,
  init: RequestInit = {},
): Promise<ApiResponse<T>> {
  const headers = new Headers(init.headers);
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }

  const url = apiUrl(path);
  let response: Response;
  try {
    response = await fetch(url, {
      cache: "no-store",
      ...init,
      headers,
    });
  } catch (cause) {
    // Next signals control flow (notFound, redirect, dynamic-rendering bailout)
    // by throwing internal errors through fetch. Wrapping those as a network
    // failure would break the framework, so hand them straight back.
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

export async function fetchActs(offset = 0, limit = 50) {
  const path = apiPath("/v1/acts", { offset, limit });
  return apiRequest<ActSummaryListData>(path);
}

export async function fetchActDetail(actId: string) {
  const encoded = encodePathSegment(actId);
  return apiRequest<ActDetail>(`/v1/acts/${encoded}`);
}

export async function fetchActContents(actId: string) {
  const encoded = encodePathSegment(actId);
  return apiRequest<ActContentsData>(`/v1/acts/${encoded}/contents`);
}
