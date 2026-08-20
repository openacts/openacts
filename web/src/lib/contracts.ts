export interface ApiMeta {
  api_version: string;
  application_revision: string;
  corpus_release: string | null;
}

export interface ActSummary {
  act_id: string;
  official_title: string;
  short_title: string | null;
  year: number;
  number: string | null;
  citation: string | null;
  text_kind: string;
  status: string;
  checked_through_date: string | null;
}

export interface Pagination {
  offset: number;
  limit: number;
  total: number;
}

export interface ActSummaryListData {
  items: ActSummary[];
  pagination: Pagination;
}

// The detail endpoint returns the canonical record, not ActSummary: titles are
// nested and there is no official_title. See schemas/act.schema.json.
export interface ActRecord {
  act_id: string;
  jurisdiction: string;
  country_code: string;
  titles: {
    official: string;
    short: string | null;
    long: string | null;
  };
  year: number;
  number: string | null;
  citation: string | null;
  text_kind?: "as_enacted" | "consolidated";
  dates: Record<ActDateKind, DateClaim>;
  aliases: string[];
  status: string;
  checked_through_date: string | null;
}

export type ActDateKind = "assent" | "publication" | "commencement" | "repeal";

export interface DateClaim {
  date: string | null;
  null_reason: string | null;
  source_ids: string[];
}

export interface SourceRecord {
  source_id: string;
  document_title: string | null;
  document_publisher: string | null;
  source_class: string | null;
  page_count: number | null;
}

export interface ActDetail {
  act: ActRecord;
  sources: SourceRecord[];
}

export interface ProvisionOutlineItem {
  provision_id: string;
  parent_provision_id: string | null;
  node_type: string;
  display_label: string | null;
  heading: string | null;
  order: number;
  sequence: number;
  depth: number;
  has_content: boolean;
  has_children: boolean;
}

export interface ActContentsData {
  items: ProvisionOutlineItem[];
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
