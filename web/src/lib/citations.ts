import type { ProvisionCitation } from "./contracts";

export interface TextSegment {
  text: string;
  citation: ProvisionCitation | null;
}

// docs/data-model.md is binding: Citation.text_range counts Unicode CODE POINTS
// over the block's NFC-normalized text, start inclusive and end exclusive.
// String.prototype.slice indexes UTF-16 code units, so it drifts on any astral
// character and can split a surrogate pair. Array.from iterates code points.
function codePoints(text: string): string[] {
  return Array.from(text);
}

// Citations for a Provision and all its descendants arrive in one flat array,
// so the renderer groups them itself before segmenting a block.
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

// Splits text into plain and cited runs without inserting, deleting, or
// normalizing a single character: joining the result reproduces the input
// exactly. A range that is out of bounds, inverted, or overlapping one already
// applied is dropped — displaying the wording correctly outranks linking it.
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
