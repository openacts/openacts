import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { actTag, apiRequest, CORPUS_TAG, OpenActsApiError } from "./api";
import type { MetaData } from "./contracts";

const metaResponse = {
  meta: {
    api_version: "v1",
    application_revision: "a".repeat(40),
    corpus_release: "corpus-v0.0.0",
  },
  data: {
    corpus_commit: "b".repeat(40),
    canonical_schema_versions: ["0.1.0"],
  },
};

describe("apiRequest", () => {
  beforeEach(() => {
    process.env.NEXT_PUBLIC_OPENACTS_API_URL = "http://127.0.0.1:8000";
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.NEXT_PUBLIC_OPENACTS_API_URL;
  });

  // Decision 0024: corpus reads are cached and tagged, and expire on no timer.
  // Release activation purges them; nothing else may.
  it("requests the configured API with a tagged, non-expiring cache", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(metaResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiRequest<MetaData>("/v1/meta")).resolves.toEqual(metaResponse);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0]?.[0].toString()).toBe(
      "http://127.0.0.1:8000/v1/meta",
    );
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      next: { revalidate: false, tags: [CORPUS_TAG] },
    });
  });

  it("tags an Act-scoped read with its Act so it can be purged alone", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(metaResponse), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await apiRequest<MetaData>("/v1/meta", {}, [
      CORPUS_TAG,
      actTag("ng-federal-act-2023-37"),
    ]);
    expect(fetchMock.mock.calls[0]?.[1]).toMatchObject({
      next: { tags: [CORPUS_TAG, "act:ng-federal-act-2023-37"] },
    });
  });

  it("preserves typed API errors", async () => {
    const body = {
      meta: { ...metaResponse.meta, corpus_release: null },
      error: {
        code: "projection_unavailable",
        message: "Corpus projection is unavailable.",
        retryable: true,
        request_id: "request-1",
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify(body), {
          status: 503,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    const error = await apiRequest<MetaData>("/v1/meta").catch(
      (caught: unknown) => caught,
    );
    expect(error).toBeInstanceOf(OpenActsApiError);
    expect(error).toMatchObject({
      status: 503,
      code: "projection_unavailable",
      retryable: true,
      requestId: "request-1",
    });
  });

  it("maps connection failures to a safe retryable error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("private detail")));

    await expect(apiRequest<MetaData>("/v1/meta")).rejects.toMatchObject({
      status: 0,
      code: "network_error",
      message: "OpenActs API is unavailable.",
      retryable: true,
      requestId: null,
    });
  });
});
