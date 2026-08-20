import type { ListBlock, ProvisionCitation } from "@/lib/contracts";

import { ContentBlocks } from "./content-blocks";

const MARKER_STYLE: Record<ListBlock["marker_style"], string> = {
  decimal: "decimal",
  lower_alpha: "lower-alpha",
  upper_alpha: "upper-alpha",
  lower_roman: "lower-roman",
  upper_roman: "upper-roman",
  bullet: "disc",
  none: "none",
  source: "none",
};

// A printed label is source data and always wins over a generated marker. The
// CSS marker is used only when every item is unlabelled and the list declares a
// real style, which is also the only case where `start` can mean anything.
export function ListBlockView({
  block,
  provisionId,
  citations,
}: {
  block: ListBlock;
  provisionId: string;
  citations: readonly ProvisionCitation[];
}) {
  const generated =
    block.items.every((item) => item.label === null) &&
    block.marker_style !== "none" &&
    block.marker_style !== "source";

  if (generated) {
    const Tag = block.marker_style === "bullet" ? "ul" : "ol";
    return (
      <Tag
        className="law ml-6 list-outside space-y-3 text-ink"
        style={{ listStyleType: MARKER_STYLE[block.marker_style] }}
        start={Tag === "ol" && block.start !== null ? block.start : undefined}
      >
        {block.items.map((item) => (
          <li key={item.item_id}>
            <ContentBlocks
              blocks={item.content_blocks}
              provisionId={provisionId}
              citations={citations}
            />
          </li>
        ))}
      </Tag>
    );
  }

  return (
    <ol className="law list-none space-y-3 text-ink">
      {block.items.map((item) => (
        <li key={item.item_id} className="flex gap-3">
          <span className="min-w-8 flex-none font-medium text-action-strong">
            {item.label}
          </span>
          <div className="min-w-0 flex-1">
            <ContentBlocks
              blocks={item.content_blocks}
              provisionId={provisionId}
              citations={citations}
            />
          </div>
        </li>
      ))}
    </ol>
  );
}
