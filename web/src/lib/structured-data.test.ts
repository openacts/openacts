import { describe, expect, it } from "vitest";

import type { ActRecord, ActSummary, ProvisionSummary } from "./contracts";
import {
  actLegislation,
  breadcrumbList,
  provisionLegislation,
  serializeJsonLd,
} from "./structured-data";

const ACT = "ng-federal-act-1999-constitution";

function summary(overrides: Partial<ActSummary> = {}): ActSummary {
  return {
    act_id: ACT,
    official_title: "Constitution of the Federal Republic of Nigeria 1999",
    short_title: "1999 Constitution",
    year: 1999,
    number: null,
    citation: "Constitution of the Federal Republic of Nigeria 1999",
    text_kind: "consolidated",
    status: "unknown",
    checked_through_date: null,
    ...overrides,
  };
}

function record(overrides: Partial<ActRecord> = {}): ActRecord {
  return {
    act_id: ACT,
    jurisdiction: "ng-federal",
    country_code: "NG",
    titles: {
      official: "Constitution of the Federal Republic of Nigeria 1999",
      short: "1999 Constitution",
      long: null,
    },
    year: 1999,
    number: null,
    citation: "Constitution of the Federal Republic of Nigeria 1999",
    dates: {
      assent: { date: null, null_reason: "not_researched", source_ids: [] },
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

describe("breadcrumbList", () => {
  it("numbers positions from one, in order", () => {
    const data = breadcrumbList([
      { name: "Constitution", url: "https://x/acts/c" },
      { name: "CHAPTER I", url: "https://x/acts/c/chapter-1" },
    ]);
    expect(data["@type"]).toBe("BreadcrumbList");
    expect(data.itemListElement.map((i) => i.position)).toEqual([1, 2]);
    expect(data.itemListElement[1]?.item).toBe("https://x/acts/c/chapter-1");
  });
});

describe("actLegislation", () => {
  it("omits legal force when the status is unknown", () => {
    const data = actLegislation(record(), summary(), "https://x/acts/c");
    expect(data.legislationLegalForce).toBeUndefined();
  });

  it("omits legal force for a spent Act rather than guessing", () => {
    const data = actLegislation(
      record({ status: "spent" }),
      summary({ status: "spent" }),
      "https://x/acts/c",
    );
    expect(data.legislationLegalForce).toBeUndefined();
  });

  it("states legal force only for statuses that map cleanly", () => {
    expect(
      actLegislation(
        record({ status: "in_force" }),
        summary({ status: "in_force" }),
        "https://x",
      ).legislationLegalForce,
    ).toBe("https://schema.org/InForce");
    expect(
      actLegislation(
        record({ status: "repealed" }),
        summary({ status: "repealed" }),
        "https://x",
      ).legislationLegalForce,
    ).toBe("https://schema.org/NotInForce");
    expect(
      actLegislation(
        record({ status: "mixed" }),
        summary({ status: "mixed" }),
        "https://x",
      ).legislationLegalForce,
    ).toBe("https://schema.org/PartiallyInForce");
  });

  it("omits the date when none is recorded, and prefers commencement", () => {
    expect(actLegislation(record(), summary(), "https://x").legislationDate).toBeUndefined();

    const dated = record();
    dated.dates.assent = { date: "1999-05-05", null_reason: null, source_ids: [] };
    dated.dates.commencement = { date: "1999-05-29", null_reason: null, source_ids: [] };
    expect(actLegislation(dated, summary(), "https://x").legislationDate).toBe(
      "1999-05-29",
    );
  });

  it("falls back to the Act id when there is no citation", () => {
    expect(
      actLegislation(record(), summary({ citation: null }), "https://x")
        .legislationIdentifier,
    ).toBe(ACT);
  });
});

describe("provisionLegislation", () => {
  const provision: ProvisionSummary = {
    provision_id: `${ACT}:section-1`,
    act_id: ACT,
    node_type: "section",
    display_label: "1.",
    heading: "Supremacy of the Constitution",
  };

  it("identifies the Provision and points at its Act", () => {
    const data = provisionLegislation(
      provision,
      "1. Supremacy of the Constitution",
      summary(),
      "https://x/acts/c/section-1",
      "https://x/acts/c",
    );
    expect(data.legislationIdentifier).toBe(`${ACT}:section-1`);
    expect(data.isPartOf).toEqual({
      "@type": "Legislation",
      name: "Constitution of the Federal Republic of Nigeria 1999",
      url: "https://x/acts/c",
    });
  });
});

describe("serializeJsonLd", () => {
  it("escapes characters that could close the script tag", () => {
    const out = serializeJsonLd({ name: "</script><script>alert(1)</script>" });
    expect(out).not.toContain("</script>");
    expect(out).not.toContain("<");
    expect(JSON.parse(out).name).toBe("</script><script>alert(1)</script>");
  });
});
