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

export interface SourceSpan {
  source_id: string;
  pdf_page: number;
  printed_page?: string;
}

export type TextBlockKind = "text" | "quoted_text" | "formula" | "signature";

export interface TextBlock {
  block_id: string;
  kind: TextBlockKind;
  text: string;
  source_spans: SourceSpan[];
}

export type ListMarkerStyle =
  | "decimal"
  | "lower_alpha"
  | "upper_alpha"
  | "lower_roman"
  | "upper_roman"
  | "bullet"
  | "none"
  | "source";

export interface ListItem {
  item_id: string;
  label: string | null;
  content_blocks: ContentBlock[];
  source_spans: SourceSpan[];
}

export interface ListBlock {
  block_id: string;
  kind: "list";
  marker_style: ListMarkerStyle;
  start: number | null;
  items: ListItem[];
  source_spans: SourceSpan[];
}

export type TableCellRole = "header" | "data";
export type TableCellScope = "row" | "column" | "row_group" | "column_group";
export type TableRowGroupRole = "header" | "body" | "footer";
export type TableLayoutStatus =
  | "faithfully_reconstructed"
  | "reconstruction_uncertain"
  | "source_conflict";

export interface TableCell {
  cell_id: string;
  column_start: number;
  role: TableCellRole;
  scope: TableCellScope | null;
  row_span: number;
  column_span: number;
  header_cell_ids: string[];
  blank: boolean;
  content_blocks: ContentBlock[];
  source_spans: SourceSpan[];
}

export interface TableRow {
  row_id: string;
  cells: TableCell[];
}

export interface TableRowGroup {
  group_id: string;
  role: TableRowGroupRole;
  rows: TableRow[];
}

export interface TableCaption {
  text: string;
  source_spans: SourceSpan[];
}

export interface TableSourceSegment {
  segment_id: string;
  row_ids: string[];
  repeated_header_row_ids: string[];
  source_spans: SourceSpan[];
}

export interface TableBlock {
  block_id: string;
  kind: "table";
  caption: TableCaption | null;
  column_count: number;
  row_groups: TableRowGroup[];
  notes: ContentBlock[];
  source_segments: TableSourceSegment[];
  layout_status: TableLayoutStatus;
  source_spans: SourceSpan[];
}

export type ContentBlock = TextBlock | ListBlock | TableBlock;

export type TextFidelity =
  | "machine_extracted"
  | "single_reviewed"
  | "double_reviewed"
  | "source_conflict";

export interface ProvisionRecord {
  provision_id: string;
  node_type: string;
  display_label: string | null;
  heading: string | null;
  parent_provision_id: string | null;
  order: number;
  source_spans: SourceSpan[];
  content_blocks: ContentBlock[];
  text_fidelity: TextFidelity;
}

export interface ProvisionSummary {
  provision_id: string;
  act_id: string;
  node_type: string;
  display_label: string | null;
  heading: string | null;
}

export interface ProvisionNavigation {
  previous: ProvisionSummary | null;
  next: ProvisionSummary | null;
}

export interface TextRange {
  start: number;
  end: number;
}

export interface CitationRecord {
  citation_id: string;
  source_provision_id: string;
  source_block_id: string;
  text_range: TextRange;
  target: {
    act_id: string;
    provision_id: string | null;
  };
}

export interface ProvisionCitation {
  citation: CitationRecord;
  target: {
    act: ActSummary;
    provision: ProvisionSummary | null;
  };
}

export interface SourceLocation {
  url: string;
  provider_name: string | null;
  retrieved_at: string | null;
  http_last_modified: string | null;
  notes: string | null;
}

export interface SourcePublication {
  name: string | null;
  date: string | null;
  number: string | null;
  volume: string | null;
  notice_number: string | null;
  page_range: string | null;
}

export interface SourceDocument {
  source_id: string;
  document_title: string | null;
  document_publisher: string | null;
  language: string | null;
  source_class: string;
  publication: SourcePublication | null;
  media_type: string;
  byte_length: number;
  page_count: number;
  text_layer: string;
  locations: SourceLocation[];
  redistribution: {
    status: string;
    license: string | null;
    notes: string | null;
  };
  document_notes: string[];
}

export interface ProvisionDetail {
  act: ActSummary;
  provision: ProvisionRecord;
  descendants: ProvisionRecord[];
  ancestors: ProvisionSummary[];
  navigation: ProvisionNavigation;
  sources: SourceDocument[];
  citations: ProvisionCitation[];
}

export interface SourceDetailData {
  source: SourceDocument;
}

export type SearchMatchKind =
  | "exact_act_id"
  | "exact_provision_id"
  | "exact_act_citation"
  | "exact_act_title"
  | "exact_act_alias"
  | "exact_provision_reference"
  | "lexical";

export interface SearchItem {
  kind: "act" | "provision";
  match_kind: SearchMatchKind;
  act: ActSummary;
  provision: ProvisionSummary | null;
  breadcrumb: ProvisionSummary[];
  excerpt: string | null;
}

export interface SearchData {
  items: SearchItem[];
}
