// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { listBlock, PROVISION_ID, span, textBlock } from "@/test/fixtures";

import { ListBlockView } from "./list-block";

function renderList(block = listBlock()) {
  return render(
    <ListBlockView block={block} provisionId={PROVISION_ID} citations={[]} />,
  );
}

describe("ListBlockView", () => {
  it("shows the printed labels rather than generating markers", () => {
    const { container } = renderList();
    expect(screen.getByText("(a)")).toBeTruthy();
    expect(screen.getByText("(b)")).toBeTruthy();
    expect(container.querySelector("ol")?.className).toContain("list-none");
  });

  it("generates markers only when every item is unlabelled", () => {
    const unlabelled = listBlock();
    unlabelled.items = unlabelled.items.map((item) => ({ ...item, label: null }));
    const { container } = renderList(unlabelled);
    const list = container.querySelector("ol");
    expect(list?.style.listStyleType).toBe("lower-alpha");
  });

  it("applies a declared start value to a generated list", () => {
    const unlabelled = listBlock({ marker_style: "decimal", start: 5 });
    unlabelled.items = unlabelled.items.map((item) => ({ ...item, label: null }));
    const { container } = renderList(unlabelled);
    expect(container.querySelector("ol")?.getAttribute("start")).toBe("5");
  });

  it("ignores a start value when the Source prints its own labels", () => {
    const { container } = renderList(listBlock({ start: 5 }));
    expect(container.querySelector("ol")?.getAttribute("start")).toBeNull();
  });

  it("renders a bullet list as an unordered list", () => {
    const bullets = listBlock({ marker_style: "bullet" });
    bullets.items = bullets.items.map((item) => ({ ...item, label: null }));
    const { container } = renderList(bullets);
    expect(container.querySelector("ul")).toBeTruthy();
  });

  it("uses no generated marker for marker_style none or source", () => {
    for (const style of ["none", "source"] as const) {
      const block = listBlock({ marker_style: style });
      block.items = block.items.map((item) => ({ ...item, label: null }));
      const { container } = renderList(block);
      expect(container.querySelector("ol")?.className).toContain("list-none");
    }
  });

  it("nests a list inside a list item", () => {
    const outer = listBlock();
    outer.items[0]!.content_blocks = [
      textBlock("the first item", "text", "b1"),
      {
        block_id: "inner",
        kind: "list",
        marker_style: "lower_roman",
        start: null,
        items: [
          {
            item_id: "inner-1",
            label: "(i)",
            content_blocks: [textBlock("a nested item", "text", "ib1")],
            source_spans: [span],
          },
        ],
        source_spans: [span],
      },
    ];
    renderList(outer);
    expect(screen.getByText("(i)")).toBeTruthy();
    expect(screen.getByText("a nested item")).toBeTruthy();
  });
});
