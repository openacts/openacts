import { apiRequest, OpenActsApiError } from "@/lib/api";
import type { MetaData } from "@/lib/contracts";

export async function ReleaseIdentity() {
  let corpusRelease: string | null;

  try {
    const response = await apiRequest<MetaData>("/v1/meta");
    corpusRelease = response.meta.corpus_release;
  } catch (error) {
    if (!(error instanceof OpenActsApiError)) {
      throw error;
    }
    corpusRelease = null;
  }

  return (
    <p className="id">
      Corpus {corpusRelease ?? "unavailable"}
    </p>
  );
}
