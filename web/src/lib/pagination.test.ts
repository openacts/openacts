import { describe, expect, it } from "vitest";

import { nextOffset, parseOffset, previousOffset } from "./pagination";

describe("parseOffset", () => {
  it("reads a valid offset", () => {
    expect(parseOffset("50")).toBe(50);
    expect(parseOffset("0")).toBe(0);
  });

  it("takes the first value when the parameter repeats", () => {
    expect(parseOffset(["50", "100"])).toBe(50);
  });

  it("resets anything the API would reject to the first page", () => {
    expect(parseOffset(undefined)).toBe(0);
    expect(parseOffset("")).toBe(0);
    expect(parseOffset("-1")).toBe(0);
    expect(parseOffset("1.5")).toBe(0);
    expect(parseOffset("banana")).toBe(0);
    expect(parseOffset("9007199254740993")).toBe(0);
  });
});

describe("previousOffset", () => {
  it("is absent on the first page and clamps at zero", () => {
    expect(previousOffset(0, 50)).toBeNull();
    expect(previousOffset(30, 50)).toBe(0);
    expect(previousOffset(100, 50)).toBe(50);
  });
});

describe("nextOffset", () => {
  it("is absent once the page reaches the total", () => {
    expect(nextOffset(0, 50, 2)).toBeNull();
    expect(nextOffset(0, 50, 120)).toBe(50);
    expect(nextOffset(100, 50, 120)).toBeNull();
  });
});
