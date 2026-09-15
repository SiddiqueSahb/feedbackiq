import { describe, expect, it } from "vitest";

import type { ImportSummary } from "../api/types";
import { fileProblem, formatBytes, rows } from "./format";
import { isAnalysing } from "./useImports";

function csv(name = "feedback.csv", size = 120, type = "text/csv"): File {
  const file = new File(["text\nhello\n"], name, { type });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function summary(analysis_status: ImportSummary["analysis_status"]): ImportSummary {
  return {
    import_id: "i1",
    organisation_id: "o1",
    status: "completed",
    rows_received: 1,
    rows_imported: 1,
    rows_rejected: 0,
    analysis_status,
  };
}

describe("fileProblem", () => {
  it("accepts a CSV within the limit", () => {
    expect(fileProblem(csv())).toBeNull();
  });

  it("accepts a .csv whatever the case of its extension or a missing type", () => {
    expect(fileProblem(csv("REPORT.CSV", 10, ""))).toBeNull();
  });

  it("refuses a file that is not a CSV", () => {
    expect(fileProblem(csv("feedback.xlsx", 10, "application/vnd.ms-excel"))).toMatch(/csv/i);
  });

  it("refuses an empty file", () => {
    expect(fileProblem(csv("feedback.csv", 0))).toMatch(/empty/i);
  });

  it("refuses a file over the 10 MB limit, saying both sizes", () => {
    expect(fileProblem(csv("feedback.csv", 11 * 1024 * 1024))).toBe(
      "That file is 11.0 MB. Files can be up to 10.0 MB.",
    );
  });
});

describe("isAnalysing", () => {
  it.each([
    [["queued"], true],
    [["running"], true],
    [["succeeded", "queued"], true],
    [["succeeded", "failed", null], false],
    [[], false],
  ] as const)("%j → %s", (statuses, expected) => {
    expect(isAnalysing(statuses.map((status) => summary(status)))).toBe(expected);
  });

  it("is false before the list has loaded", () => {
    expect(isAnalysing(undefined)).toBe(false);
  });
});

describe("formatting", () => {
  it("formats sizes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });

  it("pluralises rows", () => {
    expect(rows(1)).toBe("1 row");
    expect(rows(1200)).toBe("1,200 rows");
  });
});
