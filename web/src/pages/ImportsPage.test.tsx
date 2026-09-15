/**
 * The imports page: history, uploading, and every answer the API can give to an upload.
 *
 * `fetch` is replaced by canned API answers (test/fakeApi.ts); the hooks, cache and components are
 * real. Uploading against the real API is covered by e2e/imports.spec.ts.
 */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ImportAccepted, ImportSummary } from "../api/types";
import { fakeApi, type RecordedCall } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";

const OWNER = {
  email: "ana@acme.example",
  organisation: { id: "9b1c0000-0000-0000-0000-000000000001", name: "Acme Ltd", role: "owner" },
};

function summary(overrides: Partial<ImportSummary> = {}): ImportSummary {
  return {
    import_id: crypto.randomUUID(),
    organisation_id: OWNER.organisation.id,
    filename: "january.csv",
    status: "completed",
    rows_received: 120,
    rows_imported: 118,
    rows_rejected: 2,
    created_at: "2026-09-01T09:30:00Z",
    job_id: crypto.randomUUID(),
    analysis_status: "succeeded",
    ...overrides,
  };
}

function accepted(overrides: Partial<ImportAccepted> = {}): ImportAccepted {
  return {
    ...summary({ filename: "feedback.csv", rows_received: 4, rows_imported: 3, rows_rejected: 1, analysis_status: "queued" }),
    duplicates_in_file: 0,
    duplicates_in_database: 0,
    duplicate_upload: false,
    row_errors: [{ row: 4, field: "text", message: "Feedback text is empty." }],
    ...overrides,
  };
}

function csvFile(name = "feedback.csv", contents = "text\nWaited forty minutes\n") {
  return new File([contents], name, { type: "text/csv" });
}

function uploads(calls: RecordedCall[]) {
  return calls.filter((call) => call.method === "POST" && call.url === "/api/v1/imports");
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function openImports(routes: Parameters<typeof fakeApi>[0]) {
  const api = fakeApi({ "GET /api/v1/auth/me": { status: 200, body: OWNER }, ...routes });
  renderApp("/imports");
  await screen.findByRole("heading", { name: "Imports" });
  return api;
}

describe("import history", () => {
  it("explains what will appear when there are no imports", async () => {
    await openImports({ "GET /api/v1/imports": { status: 200, body: [] } });

    expect(await screen.findByRole("heading", { name: "No imports yet" })).toBeInTheDocument();
  });

  it("lists imports with their counts and analysis progress in plain words", async () => {
    await openImports({
      "GET /api/v1/imports": {
        status: 200,
        body: [
          summary({ filename: "queued.csv", analysis_status: "queued" }),
          summary({ filename: "running.csv", analysis_status: "running" }),
          summary({ filename: "done.csv", analysis_status: "succeeded", rows_received: 1200, rows_imported: 1180 }),
          summary({ filename: "broken.csv", analysis_status: "failed" }),
          summary({ filename: "empty.csv", rows_imported: 0, job_id: null, analysis_status: null, status: "failed" }),
        ],
      },
    });

    const table = await screen.findByRole("table", { name: "Imports, newest first" });
    const row = (name: string) => within(table).getByRole("row", { name: new RegExp(name) });

    expect(row("queued.csv")).toHaveTextContent("Queued");
    expect(row("running.csv")).toHaveTextContent("Analysing");
    expect(row("done.csv")).toHaveTextContent("Analysed");
    expect(row("done.csv")).toHaveTextContent("1,200");
    expect(row("broken.csv")).toHaveTextContent("Analysis failed");
    expect(row("empty.csv")).toHaveTextContent("Nothing imported");
  });

  it("offers a retry when the list cannot be loaded", async () => {
    let healthy = false;
    await openImports({
      "GET /api/v1/imports": () =>
        healthy ? { status: 200, body: [] } : { status: 503, body: { detail: "Service unavailable." } },
    });

    expect(await screen.findByText("Imports could not be loaded", {}, { timeout: 8000 })).toBeInTheDocument();

    healthy = true;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "No imports yet" })).toBeInTheDocument();
  }, 15000);
});

describe("uploading", () => {
  it("uploads the chosen file, reports what happened and refreshes the history", async () => {
    let uploaded = false;
    const api = await openImports({
      "GET /api/v1/imports": () => ({ status: 200, body: uploaded ? [summary({ filename: "feedback.csv", analysis_status: "queued" })] : [] }),
      "POST /api/v1/imports": () => {
        uploaded = true;
        return { status: 201, body: accepted() };
      },
    });

    await userEvent.upload(screen.getByLabelText("Choose file"), csvFile());
    expect(screen.getByText("feedback.csv")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByText("Imported 3 of 4 rows")).toBeInTheDocument();
    expect(screen.getByText("Feedback text is empty.")).toBeInTheDocument();

    const [call] = uploads(api.calls);
    const sent = (call!.body as FormData).get("file") as File;
    expect(sent.name).toBe("feedback.csv");

    const table = await screen.findByRole("table", { name: "Imports, newest first" });
    expect(within(table).getByRole("row", { name: /feedback\.csv/ })).toHaveTextContent("Queued");
  });

  it("lets a file be dropped onto the upload area", async () => {
    await openImports({ "GET /api/v1/imports": { status: 200, body: [] } });

    fireEvent.drop(screen.getByText("Drop a CSV file here").closest("div")!, {
      dataTransfer: { files: [csvFile("dropped.csv")] },
    });

    expect(await screen.findByText("dropped.csv")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload" })).toBeEnabled();
  });

  it("keeps the upload button disabled until a file is chosen", async () => {
    await openImports({ "GET /api/v1/imports": { status: 200, body: [] } });

    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
  });

  it("refuses a file that is not a CSV without uploading it", async () => {
    const api = await openImports({ "GET /api/v1/imports": { status: 200, body: [] } });
    const user = userEvent.setup({ applyAccept: false });

    await user.upload(screen.getByLabelText("Choose file"), new File(["x"], "report.xlsx", { type: "application/vnd.ms-excel" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a CSV file (.csv).");
    expect(screen.getByRole("button", { name: "Upload" })).toBeDisabled();
    expect(uploads(api.calls)).toHaveLength(0);
  });

  it("refuses a file over the size limit without uploading it", async () => {
    const api = await openImports({ "GET /api/v1/imports": { status: 200, body: [] } });
    const big = csvFile("huge.csv");
    Object.defineProperty(big, "size", { value: 11 * 1024 * 1024 });

    await userEvent.upload(screen.getByLabelText("Choose file"), big);

    expect(await screen.findByRole("alert")).toHaveTextContent("That file is 11.0 MB. Files can be up to 10.0 MB.");
    expect(uploads(api.calls)).toHaveLength(0);
  });

  it("shows the API's reason when it refuses the file", async () => {
    await openImports({
      "GET /api/v1/imports": { status: 200, body: [] },
      "POST /api/v1/imports": {
        status: 422,
        body: { detail: "No feedback text column found. Add a column named 'text'." },
      },
    });

    await userEvent.upload(screen.getByLabelText("Choose file"), csvFile("customers.csv", "name\nAcme\n"));
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The file was not imported");
    expect(alert).toHaveTextContent("No feedback text column found. Add a column named 'text'.");
  });

  it("says when the same file was already imported", async () => {
    await openImports({
      "GET /api/v1/imports": { status: 200, body: [] },
      "POST /api/v1/imports": {
        status: 201,
        body: accepted({ duplicate_upload: true, job_id: null, analysis_status: null, row_errors: [] }),
      },
    });

    await userEvent.upload(screen.getByLabelText("Choose file"), csvFile());
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByText("This file was already imported")).toBeInTheDocument();
    expect(screen.getByText(/Nothing new was added/)).toBeInTheDocument();
  });

  it("explains when no row could be imported", async () => {
    await openImports({
      "GET /api/v1/imports": { status: 200, body: [] },
      "POST /api/v1/imports": {
        status: 201,
        body: accepted({
          rows_received: 2, rows_imported: 0, rows_rejected: 2, job_id: null, analysis_status: null,
          row_errors: [
            { row: 2, field: "text", message: "Feedback text is empty." },
            { row: 3, field: "rating", message: "Rating must be between 1 and 5." },
          ],
        }),
      },
    });

    await userEvent.upload(screen.getByLabelText("Choose file"), csvFile());
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByText("No rows could be imported")).toBeInTheDocument();
    expect(screen.getByText("Rating must be between 1 and 5.")).toBeInTheDocument();
  });

  it("sends the person to sign in if the import list is refused because the session ended", async () => {
    fakeApi({
      "GET /api/v1/auth/me": { status: 200, body: OWNER },
      "GET /api/v1/imports": { status: 401, body: { detail: "Not signed in." } },
    });

    renderApp("/imports");

    expect(await screen.findByRole("heading", { name: "Sign in to FeedbackIQ" })).toBeInTheDocument();
    expect(new URLSearchParams(window.location.search).get("next")).toBe("/imports");
    expect(screen.queryByText("Recent imports")).not.toBeInTheDocument();
  });

  it("sends the person to sign in if their session ended before the upload", async () => {
    await openImports({
      "GET /api/v1/imports": { status: 200, body: [] },
      "POST /api/v1/imports": { status: 401, body: { detail: "Not signed in." } },
    });

    await userEvent.upload(screen.getByLabelText("Choose file"), csvFile());
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("heading", { name: "Sign in to FeedbackIQ" })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/login"));
    expect(new URLSearchParams(window.location.search).get("next")).toBe("/imports");
  });
});
