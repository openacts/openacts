import { describe, expect, it } from "vitest";

import { citationsForBlock, segmentBlockText } from "./citations";
import type { ProvisionCitation } from "./contracts";

const ACT = "ng-federal-act-1999-constitution";

function citation(
  start: number,
  end: number,
  overrides: { provision?: string; block?: string; id?: string } = {},
): ProvisionCitation {
  return {
    citation: {
      citation_id: overrides.id ?? `citation:${ACT}:000001`,
      source_provision_id: overrides.provision ?? `${ACT}:section-1`,
      source_block_id: overrides.block ?? "block-1",
      text_range: { start, end },
      target: { act_id: ACT, provision_id: `${ACT}:section-2` },
    },
    target: {
      act: {
        act_id: ACT,
        official_title: "Constitution of the Federal Republic of Nigeria 1999",
        short_title: "1999 Constitution",
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

describe("segmentBlockText", () => {
  it("splits a cited range out of the surrounding wording", () => {
    // "See section 1." with the fixture range from tests/fixtures/valid/citation.json
    const segments = segmentBlockText("See section 1.", [citation(4, 13)]);
    expect(segments).toEqual([
      { text: "See ", citation: null },
      { text: "section 1", citation: expect.anything() },
      { text: ".", citation: null },
    ]);
  });

  it("never alters the wording it segments", () => {
    const text = "This Constitution is supreme and its provisions shall have";
    for (const cites of [[], [citation(5, 17)], [citation(0, 4), citation(21, 28)]]) {
      expect(segmentBlockText(text, cites).map((s) => s.text).join("")).toBe(text);
    }
  });

  it("counts code points, not UTF-16 units", () => {
    // The gothic letter is one code point but two UTF-16 units; slice(0, 2)
    // would cut it in half and slice(2, 6) would start mid-character.
    const text = "\u{10330}abc def";
    const segments = segmentBlockText(text, [citation(1, 4)]);
    expect(segments[0]).toEqual({ text: "\u{10330}", citation: null });
    expect(segments[1]?.text).toBe("abc");
    expect(segments.map((s) => s.text).join("")).toBe(text);
  });

  it("returns one plain segment when there are no citations", () => {
    expect(segmentBlockText("Plain wording.", [])).toEqual([
      { text: "Plain wording.", citation: null },
    ]);
  });

  it("drops ranges that are inverted, out of bounds, or overlapping", () => {
    const text = "See section 1.";
    expect(segmentBlockText(text, [citation(9, 4)])).toEqual([
      { text, citation: null },
    ]);
    expect(segmentBlockText(text, [citation(4, 999)])).toEqual([
      { text, citation: null },
    ]);
    const overlapping = segmentBlockText(text, [
      citation(4, 13),
      citation(6, 11, { id: "citation:x:000002" }),
    ]);
    expect(overlapping.map((s) => s.text).join("")).toBe(text);
    expect(overlapping.filter((s) => s.citation !== null)).toHaveLength(1);
  });
});

describe("citationsForBlock", () => {
  it("selects only the citations anchored to that provision and block", () => {
    const all = [
      citation(0, 3),
      citation(0, 3, { block: "block-2" }),
      citation(0, 3, { provision: `${ACT}:section-9` }),
    ];
    expect(citationsForBlock(all, `${ACT}:section-1`, "block-1")).toHaveLength(1);
  });
});
