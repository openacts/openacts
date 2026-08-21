import Link from "next/link";

import { citationsForBlock, segmentBlockText } from "@/lib/citations";
import type { ProvisionCitation, TextBlock } from "@/lib/contracts";
import { provisionHref } from "@/lib/provision";

const KIND_CLASS: Record<TextBlock["kind"], string> = {
  text: "",
  quoted_text: "border-l border-line pl-5 italic",
  formula: "font-id text-[0.9375rem] not-italic",
  signature: "text-muted",
};

export function TextBlockView({
  block,
  provisionId,
  citations,
}: {
  block: TextBlock;
  provisionId: string;
  citations: readonly ProvisionCitation[];
}) {
  const segments = segmentBlockText(
    block.text,
    citationsForBlock(citations, provisionId, block.block_id),
  );

  const body = segments.map((segment, index) => {
    if (!segment.citation) {
      return <span key={index}>{segment.text}</span>;
    }
    const target = segment.citation.citation.target;
    const href = target.provision_id
      ? provisionHref(target.provision_id)
      : `/acts/${encodeURIComponent(target.act_id)}`;
    return (
      <Link
        key={index}
        href={href}
        className="rounded-sm text-action underline decoration-line underline-offset-2 hover:text-action-strong focus:outline-none focus:ring-2 focus:ring-focus focus:ring-offset-2"
      >
        {segment.text}
      </Link>
    );
  });

  const Tag = block.kind === "quoted_text" ? "blockquote" : "p";

  return (
    <Tag className={`law whitespace-pre-line text-ink ${KIND_CLASS[block.kind]}`}>
      {body}
    </Tag>
  );
}
