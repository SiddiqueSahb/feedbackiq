import { describe, expect, it } from "vitest";

import { safeNextPath } from "./nextPath";

describe("safeNextPath", () => {
  it.each([
    ["/imports", "/imports"],
    ["/imports?view=recent", "/imports?view=recent"],
    ["/dashboard#trend", "/dashboard#trend"],
  ])("keeps a path on this app: %s", (given, expected) => {
    expect(safeNextPath(given)).toBe(expected);
  });

  it.each([
    [null],
    [undefined],
    [""],
    ["//evil.example"],
    ["//evil.example/login"],
    ["/\\evil.example"],
    ["https://evil.example/dashboard"],
    ["javascript:alert(1)"],
    ["dashboard"],
  ])("sends anything else to the dashboard: %s", (given) => {
    expect(safeNextPath(given)).toBe("/dashboard");
  });

  it("does not loop back to the sign-in or registration pages", () => {
    expect(safeNextPath("/login")).toBe("/dashboard");
    expect(safeNextPath("/register?next=/imports")).toBe("/dashboard");
  });
});
