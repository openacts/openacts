import { describe, expect, it } from "vitest";

import type { ProvisionOutlineItem } from "./contracts";
import { outlineNodeCount, topLevelOutline } from "./outline";

const ACT = "ng-federal-act-1999-constitution";

function node(
  path: string,
  depth: number,
  sequence: number,
  parent: string | null,
  overrides: Partial<ProvisionOutlineItem> = {},
): ProvisionOutlineItem {
  return {
    provision_id: `${ACT}:${path}`,
    parent_provision_id: parent === null ? null : `${ACT}:${parent}`,
    node_type: depth === 0 ? "chapter" : "part",
    display_label: path.toUpperCase(),
    heading: null,
    order: 1,
    sequence,
    depth,
    has_content: false,
    has_children: depth === 0,
    ...overrides,
  };
}

describe("topLevelOutline", () => {
  it("keeps only the top two levels and nests depth 1 under its parent", () => {
    const sections = topLevelOutline([
      node("chapter-1", 0, 1, null),
      node("part-1", 1, 2, "chapter-1"),
      node("section-1", 2, 3, "part-1"),
      node("section-1.subsection-1", 3, 4, "section-1"),
      node("chapter-2", 0, 5, null),
    ]);

    expect(sections.map((s) => s.node.provision_id)).toEqual([
      `${ACT}:chapter-1`,
      `${ACT}:chapter-2`,
    ]);
    expect(sections[0]?.children.map((c) => c.provision_id)).toEqual([
      `${ACT}:part-1`,
    ]);
    expect(outlineNodeCount(sections)).toBe(3);
  });

  it("orders by document sequence, not by input order", () => {
    const sections = topLevelOutline([
      node("chapter-2", 0, 9, null),
      node("chapter-1", 0, 1, null),
    ]);
    expect(sections.map((s) => s.node.provision_id)).toEqual([
      `${ACT}:chapter-1`,
      `${ACT}:chapter-2`,
    ]);
  });

  it("promotes an unparented depth-1 node rather than dropping it", () => {
    const sections = topLevelOutline([node("part-9", 1, 2, "chapter-missing")]);
    expect(sections).toHaveLength(1);
    expect(sections[0]?.node.provision_id).toBe(`${ACT}:part-9`);
  });

  it("returns nothing for an Act with no outline", () => {
    expect(topLevelOutline([])).toEqual([]);
  });
});
