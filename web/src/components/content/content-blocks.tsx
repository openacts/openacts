import type { ContentBlock, ProvisionCitation } from "@/lib/contracts";

import { ListBlockView } from "./list-block";
import { TableBlockView } from "./table-block";
import { TextBlockView } from "./text-block";

export function ContentBlocks({
  blocks,
  provisionId,
  citations,
}: {
  blocks: readonly ContentBlock[];
  provisionId: string;
  citations: readonly ProvisionCitation[];
}) {
  if (blocks.length === 0) {
    return null;
  }

  return (
    <div className="space-y-4">
      {blocks.map((block) => {
        if (block.kind === "list") {
          return (
            <ListBlockView
              key={block.block_id}
              block={block}
              provisionId={provisionId}
              citations={citations}
            />
          );
        }
        if (block.kind === "table") {
          return (
            <TableBlockView
              key={block.block_id}
              block={block}
              provisionId={provisionId}
              citations={citations}
            />
          );
        }
        return (
          <TextBlockView
            key={block.block_id}
            block={block}
            provisionId={provisionId}
            citations={citations}
          />
        );
      })}
    </div>
  );
}
