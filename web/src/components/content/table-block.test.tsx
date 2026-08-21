// @vitest-environment jsdom
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { cell, PROVISION_ID, span, tableBlock, textBlock } from "@/test/fixtures";

import { TableBlockView } from "./table-block";

function renderTable(block = tableBlock()) {
  return render(
    <TableBlockView block={block} provisionId={PROVISION_ID} citations={[]} />,
  );
}

describe("TableBlockView", () => {
  it("renders a real table with its caption", () => {
    renderTable();
    const table = screen.getByRole("table");
    expect(table).toBeTruthy();
    expect(table.querySelector("caption")?.textContent).toBe("Area Councils");
  });

  it("omits the caption element when the Source has none", () => {
    const { container } = renderTable(tableBlock({ caption: null }));
    expect(container.querySelector("caption")).toBeNull();
  });

  it("maps row group roles to thead, tbody and tfoot", () => {
    const withFooter = tableBlock();
    withFooter.row_groups.push({
      group_id: "g-foot",
      role: "footer",
      rows: [{ row_id: "rf", cells: [cell({ cell_id: "f1" })] }],
    });
    const { container } = renderTable(withFooter);
    expect(container.querySelector("thead")).toBeTruthy();
    expect(container.querySelector("tbody")).toBeTruthy();
    expect(container.querySelector("tfoot")).toBeTruthy();
  });

  it("keeps scope on headers and explicit headers association on data cells", () => {
    const { container } = renderTable();
    const header = container.querySelector("th");
    expect(header?.getAttribute("scope")).toBe("column");
    expect(header?.getAttribute("id")).toBe("h1");
    const data = container.querySelector("td");
    expect(data?.getAttribute("headers")).toBe("h1");
  });

  it("carries row and column spans through, and omits them when they are one", () => {
    const spanned = tableBlock();
    spanned.row_groups[1]!.rows[0]!.cells = [
      cell({ cell_id: "s1", row_span: 2, column_span: 3 }),
    ];
    const { container } = renderTable(spanned);
    const td = container.querySelector("td");
    expect(td?.getAttribute("rowspan")).toBe("2");
    expect(td?.getAttribute("colspan")).toBe("3");

    const plain = renderTable().container.querySelector("td");
    expect(plain?.getAttribute("rowspan")).toBeNull();
    expect(plain?.getAttribute("colspan")).toBeNull();
  });

  it("renders a genuinely blank printed cell as an empty cell", () => {
    const blank = tableBlock();
    blank.row_groups[1]!.rows[0]!.cells = [
      cell({ cell_id: "b1", blank: true, content_blocks: [] }),
    ];
    const { container } = renderTable(blank);
    const td = container.querySelector("td");
    expect(td).toBeTruthy();
    expect(td?.textContent).toBe("");
  });

  it("says so when the layout could not be reconstructed with certainty", () => {
    renderTable(tableBlock({ layout_status: "reconstruction_uncertain" }));
    expect(screen.getByText(/could not be reconstructed with certainty/)).toBeTruthy();
    const clean = render(
      <TableBlockView block={tableBlock()} provisionId={PROVISION_ID} citations={[]} />,
    );
    expect(clean.container.textContent).not.toMatch(/could not be reconstructed/);
  });

  it("renders table notes, which are themselves content blocks", () => {
    renderTable(tableBlock({ notes: [textBlock("Excludes the FCT.", "text", "n1")] }));
    expect(screen.getByText("Excludes the FCT.")).toBeTruthy();
  });

  it("puts a wide table in its own scroll container rather than shrinking it", () => {
    const { container } = renderTable();
    const wrapper = container.querySelector(".overflow-x-auto");
    expect(wrapper?.querySelector("table")).toBeTruthy();
  });

  it("renders nested content inside a cell", () => {
    const nested = tableBlock();
    nested.row_groups[1]!.rows[0]!.cells = [
      cell({
        cell_id: "n1",
        content_blocks: [
          { ...textBlock("nested wording", "text", "nb1"), source_spans: [span] },
        ],
      }),
    ];
    renderTable(nested);
    expect(screen.getByText("nested wording")).toBeTruthy();
  });
});
