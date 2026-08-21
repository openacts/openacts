import type {
  ListBlock,
  ProvisionCitation,
  SourceSpan,
  TableBlock,
  TableCell,
  TextBlock,
  TextBlockKind,
} from "@/lib/contracts";

export const ACT_ID = "ng-federal-act-1999-constitution";
export const PROVISION_ID = `${ACT_ID}:section-1`;

export const span: SourceSpan = { source_id: "sha256:" + "a".repeat(64), pdf_page: 23 };

export function textBlock(
  text: string,
  kind: TextBlockKind = "text",
  blockId = "block-1",
): TextBlock {
  return { block_id: blockId, kind, text, source_spans: [span] };
}

export function listBlock(overrides: Partial<ListBlock> = {}): ListBlock {
  return {
    block_id: "block-1",
    kind: "list",
    marker_style: "lower_alpha",
    start: null,
    items: [
      {
        item_id: "block-1-item-1",
        label: "(a)",
        content_blocks: [textBlock("the first item", "text", "b1")],
        source_spans: [span],
      },
      {
        item_id: "block-1-item-2",
        label: "(b)",
        content_blocks: [textBlock("the second item", "text", "b2")],
        source_spans: [span],
      },
    ],
    source_spans: [span],
    ...overrides,
  };
}

export function cell(overrides: Partial<TableCell> = {}): TableCell {
  return {
    cell_id: "c1",
    column_start: 1,
    role: "data",
    scope: null,
    row_span: 1,
    column_span: 1,
    header_cell_ids: [],
    blank: false,
    content_blocks: [textBlock("value", "text", "cb1")],
    source_spans: [span],
    ...overrides,
  };
}

export function tableBlock(overrides: Partial<TableBlock> = {}): TableBlock {
  return {
    block_id: "block-1",
    kind: "table",
    caption: { text: "Area Councils", source_spans: [span] },
    column_count: 2,
    row_groups: [
      {
        group_id: "g-head",
        role: "header",
        rows: [
          {
            row_id: "r1",
            cells: [
              cell({
                cell_id: "h1",
                role: "header",
                scope: "column",
                content_blocks: [textBlock("State", "text", "hb1")],
              }),
              cell({
                cell_id: "h2",
                column_start: 2,
                role: "header",
                scope: "column",
                content_blocks: [textBlock("Capital", "text", "hb2")],
              }),
            ],
          },
        ],
      },
      {
        group_id: "g-body",
        role: "body",
        rows: [
          {
            row_id: "r2",
            cells: [
              cell({
                cell_id: "d1",
                header_cell_ids: ["h1"],
                content_blocks: [textBlock("Abia", "text", "db1")],
              }),
              cell({
                cell_id: "d2",
                column_start: 2,
                header_cell_ids: ["h2"],
                content_blocks: [textBlock("Umuahia", "text", "db2")],
              }),
            ],
          },
        ],
      },
    ],
    notes: [],
    source_segments: [],
    layout_status: "faithfully_reconstructed",
    source_spans: [span],
    ...overrides,
  };
}

export function citation(
  start: number,
  end: number,
  targetProvision: string | null = `${ACT_ID}:section-2`,
): ProvisionCitation {
  return {
    citation: {
      citation_id: `citation:${ACT_ID}:000001`,
      source_provision_id: PROVISION_ID,
      source_block_id: "block-1",
      text_range: { start, end },
      target: { act_id: ACT_ID, provision_id: targetProvision },
    },
    target: {
      act: {
        act_id: ACT_ID,
        official_title: "Constitution of the Federal Republic of Nigeria 1999",
        short_title: null,
        year: 1999,
        number: null,
        citation: null,
        text_kind: "consolidated",
        status: "unknown",
        checked_through_date: null,
      },
      provision: null,
    },
  };
}
