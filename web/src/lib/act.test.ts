import { describe, expect, it } from "vitest";

import { actTextKind, currentnessText, textKindLabel } from "./act";
import type { ActRecord } from "./contracts";

function actRecord(overrides: Partial<ActRecord> = {}): ActRecord {
  return {
    act_id: "ng-federal-act-2023-37",
    jurisdiction: "ng-federal",
    country_code: "NG",
    titles: {
      official: "Nigeria Data Protection Act, 2023",
      short: "Nigeria Data Protection Act",
      long: null,
    },
    year: 2023,
    number: "37",
    citation: "Act No. 37 of 2023",
    dates: {
      assent: { date: "2023-06-12", null_reason: null, source_ids: [] },
      publication: { date: null, null_reason: "not_researched", source_ids: [] },
      commencement: { date: null, null_reason: "not_researched", source_ids: [] },
      repeal: { date: null, null_reason: "not_researched", source_ids: [] },
    },
    aliases: [],
    status: "unknown",
    checked_through_date: null,
    ...overrides,
  };
}

describe("actTextKind", () => {
  it("defaults an omitted text_kind to as_enacted", () => {
    expect(actTextKind(actRecord())).toBe("as_enacted");
  });

  it("preserves an explicit text_kind", () => {
    expect(actTextKind(actRecord({ text_kind: "consolidated" }))).toBe(
      "consolidated",
    );
  });
});

describe("textKindLabel", () => {
  it("renders known kinds as prose", () => {
    expect(textKindLabel("as_enacted")).toBe("As enacted");
    expect(textKindLabel("consolidated")).toBe("Consolidated");
  });

  it("passes an unrecognised kind through unchanged", () => {
    expect(textKindLabel("amended_in_place")).toBe("amended_in_place");
  });
});

describe("currentnessText", () => {
  it("keeps text kind, status and checked-through as separate claims", () => {
    expect(currentnessText(actRecord({ status: "in_force" }))).toBe(
      "As enacted · status in_force · currentness not established",
    );
  });

  it("reports a recorded currentness boundary verbatim", () => {
    expect(
      currentnessText(actRecord({ checked_through_date: "2024-01-31" })),
    ).toBe("As enacted · status unknown · checked through 2024-01-31");
  });
});
