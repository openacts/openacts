export interface ApiMeta {
  api_version: string;
  application_revision: string;
  corpus_release: string | null;
}

export interface ApiResponse<T> {
  meta: ApiMeta;
  data: T;
}

export interface ApiErrorResponse {
  meta: ApiMeta;
  error: {
    code: string;
    message: string;
    retryable: boolean;
    request_id: string;
  };
}

export interface MetaData {
  corpus_commit: string;
  canonical_schema_versions: string[];
}
