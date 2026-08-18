import { connection } from "next/server";

import { apiRequest, OpenActsApiError } from "@/lib/api";
import type { MetaData } from "@/lib/contracts";

function webRevision(): string {
  return (
    process.env.OPENACTS_WEB_REVISION ??
    process.env.VERCEL_GIT_COMMIT_SHA ??
    "development"
  );
}

export async function ReleaseIdentity() {
  await connection();
  const revision = webRevision();
  let identity: { corpus: string | null; api: string } | null;

  try {
    const response = await apiRequest<MetaData>("/v1/meta");
    identity = {
      corpus: response.meta.corpus_release,
      api: response.meta.application_revision,
    };
  } catch (error) {
    if (!(error instanceof OpenActsApiError)) {
      throw error;
    }
    identity = null;
  }

  return (
    <p className="flex flex-wrap gap-x-3 gap-y-1 font-mono tabular-nums [overflow-wrap:anywhere]">
      <span>Corpus {identity?.corpus ?? "unavailable"}</span>
      {identity && <span>API {identity.api}</span>}
      <span>Web {revision}</span>
    </p>
  );
}
