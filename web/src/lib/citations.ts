import type { ProvisionCitation } from "./contracts";

export interface TextSegment {
  text: string;
  citation: ProvisionCitation | null;
}

function codePoints(text: string): string[] {
  return Array.from(text);
}

export function citationsForBlock(
  citations: readonly ProvisionCitation[],
  provisionId: string,
  blockId: string,
): ProvisionCitation[] {
  return citations.filter(
    (entry) =>
      entry.citation.source_provision_id === provisionId &&
      entry.citation.source_block_id === blockId,
  );
}

export function segmentBlockText(
  text: string,
  citations: readonly ProvisionCitation[],
): TextSegment[] {
  const chars = codePoints(text);
  const ordered = [...citations].sort(
    (a, b) => a.citation.text_range.start - b.citation.text_range.start,
  );

  const segments: TextSegment[] = [];
  let cursor = 0;

  for (const entry of ordered) {
    const { start, end } = entry.citation.text_range;

    if (
      !Number.isInteger(start) ||
      !Number.isInteger(end) ||
      start < cursor ||
      end <= start ||
      end > chars.length
    ) {
      continue;
    }

    if (start > cursor) {
      segments.push({ text: chars.slice(cursor, start).join(""), citation: null });
    }
    segments.push({ text: chars.slice(start, end).join(""), citation: entry });
    cursor = end;
  }

  if (cursor < chars.length) {
    segments.push({ text: chars.slice(cursor).join(""), citation: null });
  }

  return segments;
}
