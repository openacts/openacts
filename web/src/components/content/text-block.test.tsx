// @vitest-environment jsdom
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ACT_ID, citation, PROVISION_ID, textBlock } from "@/test/fixtures";

import { TextBlockView } from "./text-block";

const SUPREMACY =
  "This Constitution is supreme and its provisions shall have binding force.";

function renderText(block = textBlock(SUPREMACY), citations = [] as ReturnType<typeof citation>[]) {
  return render(
    <TextBlockView block={block} provisionId={PROVISION_ID} citations={citations} />,
  );
}

describe("TextBlockView", () => {
  it("renders the wording exactly", () => {
    const { container } = renderText();
    expect(container.textContent).toBe(SUPREMACY);
  });

  it("renders quoted text as a blockquote and the others as paragraphs", () => {
    expect(
      renderText(textBlock("quoted", "quoted_text")).container.querySelector(
        "blockquote",
      ),
    ).toBeTruthy();
    for (const kind of ["text", "formula", "signature"] as const) {
      const { container } = renderText(textBlock("x", kind));
      expect(container.querySelector("p")).toBeTruthy();
      expect(container.querySelector("blockquote")).toBeNull();
    }
  });

  it("preserves a source-significant line break instead of collapsing it", () => {
    const { container } = renderText(textBlock("first line\nsecond line"));
    const paragraph = container.querySelector("p");
    expect(paragraph?.className).toContain("whitespace-pre-line");
    expect(paragraph?.textContent).toBe("first line\nsecond line");
  });

  it("links a cited range without altering a character of the wording", () => {
    const { container } = renderText(textBlock(SUPREMACY), [citation(0, 17)]);
    const link = container.querySelector("a");
    expect(link?.textContent).toBe("This Constitution");
    expect(link?.getAttribute("href")).toBe(`/acts/${ACT_ID}/section-2`);
    expect(container.textContent).toBe(SUPREMACY);
  });

  it("links an Act-level citation to the Act when it names no Provision", () => {
    const { container } = renderText(textBlock(SUPREMACY), [citation(0, 4, null)]);
    expect(container.querySelector("a")?.getAttribute("href")).toBe(
      `/acts/${ACT_ID}`,
    );
  });

  it("leaves wording unlinked when the citation belongs to another block", () => {
    const other = citation(0, 4);
    other.citation.source_block_id = "block-99";
    const { container } = renderText(textBlock(SUPREMACY), [other]);
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toBe(SUPREMACY);
  });

  it("renders nothing linked when there are no citations", () => {
    expect(renderText().container.querySelector("a")).toBeNull();
  });
});
