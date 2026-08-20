import { describe, expect, it } from "vitest";

import {
  actHref,
  decodeRouteParam,
  inheritedHeading,
  isContainerNode,
  joinProvisionId,
  nodeTypeLabel,
  provisionHref,
  provisionTitle,
  splitProvisionId,
} from "./provision";

const CONSTITUTION = "ng-federal-act-1999-constitution";

describe("splitProvisionId", () => {
  it("splits a canonical id on its first colon", () => {
    expect(splitProvisionId(`${CONSTITUTION}:section-1.subsection-1`)).toEqual({
      actId: CONSTITUTION,
      path: "section-1.subsection-1",
    });
  });

  it("keeps a collision suffix in the path", () => {
    expect(splitProvisionId(`${CONSTITUTION}:section-1~2`)?.path).toBe(
      "section-1~2",
    );
  });

  it("rejects an id with no colon, an empty act, or an empty path", () => {
    expect(splitProvisionId(CONSTITUTION)).toBeNull();
    expect(splitProvisionId(":section-1")).toBeNull();
    expect(splitProvisionId(`${CONSTITUTION}:`)).toBeNull();
  });

  it("round-trips through joinProvisionId", () => {
    const id = `${CONSTITUTION}:schedule-1.part-1`;
    const parts = splitProvisionId(id);
    expect(parts && joinProvisionId(parts.actId, parts.path)).toBe(id);
  });
});

describe("provisionHref", () => {
  it("builds the Act-scoped route without percent-encoding the path", () => {
    expect(provisionHref(`${CONSTITUTION}:section-1.subsection-1`)).toBe(
      `/acts/${CONSTITUTION}/section-1.subsection-1`,
    );
  });

  it("falls back to the identity route when the id is malformed", () => {
    expect(provisionHref("not-a-provision")).toBe("/provisions/not-a-provision");
  });
});

describe("actHref", () => {
  it("percent-encodes the act id", () => {
    expect(actHref(CONSTITUTION)).toBe(`/acts/${CONSTITUTION}`);
  });
});

describe("isContainerNode", () => {
  it("treats structural divisions as containers", () => {
    for (const type of ["chapter", "part", "schedule", "schedule_part"]) {
      expect(isContainerNode(type)).toBe(true);
    }
  });

  it("treats a section and everything below it as continuous text", () => {
    for (const type of ["section", "subsection", "paragraph", "definition"]) {
      expect(isContainerNode(type)).toBe(false);
    }
  });
});

describe("provisionTitle", () => {
  it("joins a label to its heading", () => {
    expect(
      provisionTitle({
        display_label: "1.",
        heading: "Supremacy of the Constitution",
        node_type: "section",
      }),
    ).toBe("1. Supremacy of the Constitution");
  });

  it("falls back to the node type when both are absent", () => {
    expect(
      provisionTitle({ display_label: null, heading: null, node_type: "preamble" }),
    ).toBe("Preamble");
  });
});

describe("nodeTypeLabel", () => {
  it("renders an unrecognised type readably", () => {
    expect(nodeTypeLabel("explanatory_note")).toBe("Explanatory note");
    expect(nodeTypeLabel("some_future_type")).toBe("some future type");
  });
});

describe("decodeRouteParam", () => {
  it("decodes the colon Next leaves encoded in a route param", () => {
    expect(decodeRouteParam("ng-federal-act-1999-constitution%3Asection-1")).toBe(
      "ng-federal-act-1999-constitution:section-1",
    );
    expect(
      decodeRouteParam("sha256%3Adfb4e3f4e8c68384673423263bf8208536486ebaf"),
    ).toBe("sha256:dfb4e3f4e8c68384673423263bf8208536486ebaf");
  });

  it("leaves an already-decoded value alone", () => {
    expect(decodeRouteParam("section-1.subsection-1")).toBe(
      "section-1.subsection-1",
    );
  });

  it("returns null for a malformed escape rather than throwing", () => {
    expect(decodeRouteParam("%E0%A4%A")).toBeNull();
  });
});

describe("inheritedHeading", () => {
  const section = {
    display_label: "1.",
    heading: "Supremacy of the Constitution",
    node_type: "section",
  };
  const part = {
    display_label: "PART I",
    heading: "Federal Republic of Nigeria",
    node_type: "part",
  };
  const subsection = {
    display_label: "(1)",
    heading: null,
    node_type: "subsection",
  };

  it("prefers the Provision's own heading", () => {
    expect(inheritedHeading(section, [part])).toBe(
      "Supremacy of the Constitution",
    );
  });

  it("falls back to the nearest ancestor that has one", () => {
    expect(inheritedHeading(subsection, [part, section])).toBe(
      "Supremacy of the Constitution",
    );
  });

  it("skips unheaded ancestors on the way up", () => {
    const unheaded = { display_label: "A", heading: null, node_type: "division" };
    expect(inheritedHeading(subsection, [part, unheaded])).toBe(
      "Federal Republic of Nigeria",
    );
  });

  it("returns null when nothing in the chain has a heading", () => {
    expect(inheritedHeading(subsection, [])).toBeNull();
  });
});
