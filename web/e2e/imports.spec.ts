/**
 * Uploading feedback against the real API - Chromium, the built app, FastAPI and PostgreSQL.
 *
 * No worker runs in these tests (it needs the ML models, which CI does not have), so an upload's
 * analysis stays "Queued". Analysis completing is verified in Docker with the real worker.
 */
import { expect, request as playwrightRequest, test } from "@playwright/test";

import {
  SESSION_COOKIE,
  failOnContentSecurityPolicyViolations,
  registerThroughTheApp,
  uniqueEmail,
} from "./support";

failOnContentSecurityPolicyViolations();

// Four data rows; line 4 has no text, so it is skipped with its line number.
const FEEDBACK_CSV = [
  "text,rating",
  "Waited forty minutes for a table,1",
  "Charged twice for one order and still no refund,1",
  ",3",
  "The app crashes every time I log in,2",
].join("\n");

async function openImports(page: import("@playwright/test").Page) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Imports" }).click();
  await expect(page.getByRole("heading", { name: "Imports", exact: true })).toBeVisible();
}

async function chooseAndUpload(page: import("@playwright/test").Page, name: string, contents: string) {
  await page.getByLabel("Choose file").setInputFiles({ name, mimeType: "text/csv", buffer: Buffer.from(contents) });
  await expect(page.getByText(name).first()).toBeVisible();
  await page.getByRole("button", { name: "Upload", exact: true }).click();
}

test("uploading a CSV imports its rows, explains skipped ones and queues analysis", async ({ page }) => {
  await registerThroughTheApp(page, uniqueEmail("upload"), "Upload E2E Ltd");
  await openImports(page);

  await expect(page.getByRole("heading", { name: "No imports yet" })).toBeVisible();

  await chooseAndUpload(page, "january-feedback.csv", FEEDBACK_CSV);

  await expect(page.getByRole("status").filter({ hasText: "Imported 3 of 4 rows" })).toBeVisible();
  await expect(page.getByText("Feedback text is empty.")).toBeVisible();

  const history = page.getByRole("table", { name: "Imports, newest first" });
  const row = history.getByRole("row", { name: /january-feedback\.csv/ });
  await expect(row).toContainText("Queued");

  // After a reload the list still knows the analysis was queued - the status comes from the API.
  await page.reload();
  await expect(page.getByRole("table", { name: "Imports, newest first" }).getByRole("row", { name: /january-feedback\.csv/ })).toContainText("Queued");
});

test("a file without a feedback text column shows the API's reason", async ({ page }) => {
  await registerThroughTheApp(page, uniqueEmail("badfile"), "Bad File E2E Ltd");
  await openImports(page);

  await chooseAndUpload(page, "customers.csv", "name,rating\nAcme,5\n");

  const alert = page.getByRole("alert").filter({ hasText: "The file was not imported" });
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("No feedback text column found");
});

test("uploading the same file again says it was already imported", async ({ page }) => {
  await registerThroughTheApp(page, uniqueEmail("again"), "Again E2E Ltd");
  await openImports(page);

  await chooseAndUpload(page, "repeat.csv", FEEDBACK_CSV);
  await expect(page.getByRole("status").filter({ hasText: "Imported 3 of 4 rows" })).toBeVisible();

  await chooseAndUpload(page, "repeat.csv", FEEDBACK_CSV);
  await expect(page.getByRole("status").filter({ hasText: "This file was already imported" })).toBeVisible();

  await expect(page.getByRole("table", { name: "Imports, newest first" }).getByRole("row")).toHaveCount(2); // header + one import
});

test("a session ended elsewhere takes effect on the next action, without a reload", async ({ page, context, baseURL }) => {
  await registerThroughTheApp(page, uniqueEmail("revoked"), "Revoked E2E Ltd");
  await openImports(page);

  // Sign out from "another device": the server forgets the session; this tab still has its cookie
  // and is still showing the imports page.
  const [cookie] = (await context.cookies()).filter((c) => c.name === SESSION_COOKIE);
  const elsewhere = await playwrightRequest.newContext({
    baseURL,
    extraHTTPHeaders: { Cookie: `${SESSION_COOKIE}=${cookie!.value}` },
  });
  expect((await elsewhere.post("/api/v1/auth/logout")).status()).toBe(204);
  await elsewhere.dispose();

  // The next thing the person does is refused by the API, and the app follows at once.
  await chooseAndUpload(page, "after-sign-out.csv", FEEDBACK_CSV);

  await expect(page).toHaveURL(/\/login\?next=%2Fimports$/);
  await expect(page.getByRole("heading", { name: "Sign in to FeedbackIQ" })).toBeVisible();
});

test("another organisation sees none of the imports", async ({ browser }) => {
  const acme = await browser.newContext();
  const globex = await browser.newContext();
  const acmePage = await acme.newPage();
  const globexPage = await globex.newPage();

  await registerThroughTheApp(acmePage, uniqueEmail("acme-imports"), "Acme Imports E2E");
  await openImports(acmePage);
  await chooseAndUpload(acmePage, "acme-private.csv", FEEDBACK_CSV);
  await expect(acmePage.getByRole("status").filter({ hasText: "Imported 3 of 4 rows" })).toBeVisible();

  await registerThroughTheApp(globexPage, uniqueEmail("globex-imports"), "Globex Imports E2E");
  await openImports(globexPage);

  await expect(globexPage.getByRole("heading", { name: "No imports yet" })).toBeVisible();
  await expect(globexPage.getByText("acme-private.csv")).toHaveCount(0);
  expect(await (await globexPage.request.get("/api/v1/imports")).json()).toEqual([]);

  await acme.close();
  await globex.close();
});
