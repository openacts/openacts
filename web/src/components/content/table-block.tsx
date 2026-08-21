import type {
  ProvisionCitation,
  TableBlock,
  TableCell,
  TableRowGroup,
} from "@/lib/contracts";

import { ContentBlocks } from "./content-blocks";

const LAYOUT_NOTE: Record<TableBlock["layout_status"], string | null> = {
  faithfully_reconstructed: null,
  reconstruction_uncertain:
    "The layout of this table could not be reconstructed with certainty from the Source.",
  source_conflict:
    "Sources disagree about the layout of this table. Both readings are preserved in the corpus.",
};

function CellView({
  cell,
  provisionId,
  citations,
}: {
  cell: TableCell;
  provisionId: string;
  citations: readonly ProvisionCitation[];
}) {
  const shared = {
    id: cell.cell_id,
    rowSpan: cell.row_span > 1 ? cell.row_span : undefined,
    colSpan: cell.column_span > 1 ? cell.column_span : undefined,
    className:
      "border-b border-line px-3 py-2 align-top text-[0.9375rem] leading-6",
  };

  const body = cell.blank ? null : (
    <ContentBlocks
      blocks={cell.content_blocks}
      provisionId={provisionId}
      citations={citations}
    />
  );

  if (cell.role === "header") {
    return (
      <th {...shared} scope={cell.scope ?? undefined} className={`${shared.className} text-left font-semibold text-ink`}>
        {body}
      </th>
    );
  }

  return (
    <td
      {...shared}
      headers={cell.header_cell_ids.length > 0 ? cell.header_cell_ids.join(" ") : undefined}
    >
      {body}
    </td>
  );
}

function RowGroup({
  group,
  provisionId,
  citations,
}: {
  group: TableRowGroup;
  provisionId: string;
  citations: readonly ProvisionCitation[];
}) {
  const rows = group.rows.map((row) => (
    <tr key={row.row_id}>
      {row.cells.map((cell) => (
        <CellView
          key={cell.cell_id}
          cell={cell}
          provisionId={provisionId}
          citations={citations}
        />
      ))}
    </tr>
  ));

  if (group.role === "header") return <thead>{rows}</thead>;
  if (group.role === "footer") return <tfoot>{rows}</tfoot>;
  return <tbody>{rows}</tbody>;
}

export function TableBlockView({
  block,
  provisionId,
  citations,
}: {
  block: TableBlock;
  provisionId: string;
  citations: readonly ProvisionCitation[];
}) {
  const note = LAYOUT_NOTE[block.layout_status];

  return (
    <div className="my-6">
      <div className="overflow-x-auto border-t border-line">
        <table className="w-full border-collapse text-left">
          {block.caption ? (
            <caption className="py-3 text-left font-reading text-[1.0625rem] text-ink">
              {block.caption.text}
            </caption>
          ) : null}
          {block.row_groups.map((group) => (
            <RowGroup
              key={group.group_id}
              group={group}
              provisionId={provisionId}
              citations={citations}
            />
          ))}
        </table>
      </div>
      {note ? (
        <p className="id mt-2 text-muted">{note}</p>
      ) : null}
      {block.notes.length > 0 ? (
        <div className="mt-3 text-[0.9375rem] text-muted">
          <ContentBlocks
            blocks={block.notes}
            provisionId={provisionId}
            citations={citations}
          />
        </div>
      ) : null}
    </div>
  );
}
