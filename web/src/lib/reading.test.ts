import { describe, expect, it } from "vitest";

import type { ProvisionOutlineItem, ProvisionRecord } from "./contracts";
import { directChildren, relativeDepths, siblingContext } from "./reading";

const ACT = "ng-federal-act-1999-constitution";

function record(path: string, parent: string | null): ProvisionRecord {
  return {
    provision_id: `${ACT}:${path}`,
    node_type: "subsection",
    display_label: null,
    heading: null,
    parent_provision_id: parent === null ? null : `${ACT}:${parent}`,
    order: 1,
    source_spans: [],
    content_blocks: [],
    text_fidelity: "single_reviewed",
  };
}

describe("directChildren", () => {
  it("selects one level only, not the whole subtree", () => {
    const children = directChildren(`${ACT}:chapter-1`, [
      record("part-1", "chapter-1"),
      record("part-1.section-1", "part-1"),
      record("part-2", "chapter-1"),
    ]);
    expect(children.map((c) => c.provision_id)).toEqual([
      `${ACT}:part-1`,
      `${ACT}:part-2`,
    ]);
  });

  it("is empty for a leaf", () => {
    expect(directChildren(`${ACT}:section-1`, [])).toEqual([]);
  });
});

describe("relativeDepths", () => {
  it("measures depth from the requested Provision, which is zero", () => {
    const depths = relativeDepths(`${ACT}:section-1`, [
      record("section-1.subsection-1", "section-1"),
      record("section-1.subsection-1.paragraph-a", "section-1.subsection-1"),
      record("section-1.subsection-2", "section-1"),
    ]);
    expect(depths.get(`${ACT}:section-1`)).toBe(0);
    expect(depths.get(`${ACT}:section-1.subsection-1`)).toBe(1);
    expect(depths.get(`${ACT}:section-1.subsection-1.paragraph-a`)).toBe(2);
    expect(depths.get(`${ACT}:section-1.subsection-2`)).toBe(1);
  });

  it("places a node whose parent is outside the subtree at depth one", () => {
    const depths = relativeDepths(`${ACT}:section-1`, [
      record("orphan", "somewhere-else"),
    ]);
    expect(depths.get(`${ACT}:orphan`)).toBe(1);
  });
});

function outlineItem(
  path: string,
  parent: string | null,
  sequence: number,
): ProvisionOutlineItem {
  return {
    provision_id: `${ACT}:${path}`,
    parent_provision_id: parent === null ? null : `${ACT}:${parent}`,
    node_type: "subsection",
    display_label: `(${sequence})`,
    heading: null,
    order: sequence,
    sequence,
    depth: parent === null ? 0 : 1,
    has_content: true,
    has_children: false,
  };
}

describe("siblingContext", () => {
  const outline = [
    outlineItem("section-1", null, 1),
    outlineItem("section-1.subsection-1", "section-1", 2),
    outlineItem("section-1.subsection-2", "section-1", 3),
    outlineItem("section-1.subsection-3", "section-1", 4),
    outlineItem("section-2", null, 5),
  ];

  it("finds the siblings sharing a parent, in document order", () => {
    const context = siblingContext(outline, `${ACT}:section-1.subsection-2`);
    expect(context?.siblings.map((s) => s.provision_id)).toEqual([
      `${ACT}:section-1.subsection-1`,
      `${ACT}:section-1.subsection-2`,
      `${ACT}:section-1.subsection-3`,
    ]);
    expect(context?.currentIndex).toBe(1);
    expect(context?.parent?.provision_id).toBe(`${ACT}:section-1`);
  });

  it("treats the other roots as siblings of a root Provision", () => {
    const context = siblingContext(outline, `${ACT}:section-1`);
    expect(context?.parent).toBeNull();
    expect(context?.siblings.map((s) => s.provision_id)).toEqual([
      `${ACT}:section-1`,
      `${ACT}:section-2`,
    ]);
  });

  it("returns nothing when the Provision is not in the outline", () => {
    expect(siblingContext(outline, `${ACT}:missing`)).toBeNull();
  });
});
