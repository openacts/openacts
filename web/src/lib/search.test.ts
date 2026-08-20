import { describe, expect, it } from "vitest";

import type { SearchItem } from "./contracts";
import {
  excerptBeyondTitle,
  isExactMatch,
  matchReason,
  normalizeQuery,
  queryLength,
  splitResults,
  validateQuery,
} from "./search";

function item(match_kind: SearchItem["match_kind"]): SearchItem {
  return {
    kind: "provision",
    match_kind,
    act: {
      act_id: "ng-federal-act-2023-37",
      official_title: "Nigeria Data Protection Act, 2023",
      short_title: null,
      year: 2023,
      number: "37",
      citation: "Act No. 37 of 2023",
      text_kind: "as_enacted",
      status: "unknown",
      checked_through_date: null,
    },
    provision: null,
    breadcrumb: [],
    excerpt: null,
  };
}

describe("normalizeQuery", () => {
  it("normalizes to NFC and trims, as the API does", () => {
    // "Café" decomposed (e plus combining acute) must fold to the composed form.
    expect(normalizeQuery("  Café  ")).toBe("Café");
  });
});

describe("queryLength", () => {
  it("counts code points, not UTF-16 units", () => {
    expect(queryLength("\u{10330}")).toBe(1);
    expect("\u{10330}".length).toBe(2);
  });
});

describe("validateQuery", () => {
  it("rejects a query that is empty after trimming", () => {
    expect(validateQuery("   ")).toMatch(/Type an Act title/);
  });

  it("accepts a query the API would accept", () => {
    expect(validateQuery("section 1")).toBeNull();
    expect(validateQuery("a".repeat(256))).toBeNull();
  });

  it("rejects a query past the API's 256 code-point limit", () => {
    expect(validateQuery("a".repeat(257))).toMatch(/at most 256/);
  });
});

describe("splitResults", () => {
  it("keeps exact matches visibly separate from lexical ones", () => {
    const split = splitResults([
      item("exact_act_title"),
      item("lexical"),
      item("exact_provision_id"),
    ]);
    expect(split.exact.map((i) => i.match_kind)).toEqual([
      "exact_act_title",
      "exact_provision_id",
    ]);
    expect(split.lexical.map((i) => i.match_kind)).toEqual(["lexical"]);
  });
});

describe("isExactMatch", () => {
  it("treats every exact_ kind as exact and lexical as not", () => {
    for (const kind of [
      "exact_act_id",
      "exact_provision_id",
      "exact_act_citation",
      "exact_act_title",
      "exact_act_alias",
      "exact_provision_reference",
    ] as const) {
      expect(isExactMatch(item(kind))).toBe(true);
    }
    expect(isExactMatch(item("lexical"))).toBe(false);
  });
});

describe("matchReason", () => {
  it("explains the match without inventing a score", () => {
    expect(matchReason(item("exact_act_citation"))).toBe(
      "matched an Act citation",
    );
    expect(matchReason(item("lexical"))).toBe("contains your words");
  });
});

describe("excerptBeyondTitle", () => {
  const title = "1. Supremacy of the Constitution";

  it("suppresses an excerpt that only repeats the title", () => {
    // What the projection actually returns for a short Provision.
    expect(excerptBeyondTitle("1.\nSupremacy of the Constitution", title)).toBeNull();
  });

  it("keeps only the part that adds something", () => {
    expect(
      excerptBeyondTitle(
        "1. Supremacy of the Constitution This Constitution is supreme",
        title,
      ),
    ).toBe("This Constitution is supreme");
  });

  it("keeps an excerpt that is genuinely different wording", () => {
    expect(excerptBeyondTitle("If any other law is inconsistent", title)).toBe(
      "If any other law is inconsistent",
    );
  });

  it("returns null for an absent or blank excerpt", () => {
    expect(excerptBeyondTitle(null, title)).toBeNull();
    expect(excerptBeyondTitle("   ", title)).toBeNull();
  });
});
