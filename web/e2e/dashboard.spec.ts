/**
 * The dashboard against the real API - Chromium, the built app, FastAPI and PostgreSQL.
 *
 * No worker runs here (it needs the ML models), so uploaded feedback stays unanalysed. What these
 * tests prove is that the figures are the organisation's own, straight from the API, in each state.
 * Analysed figures are verified in Docker with the real worker.
 */
import { expect, test, type Page } from "@playwright/test";

import { failOnContentSecurityPolicyViolations, registerThroughTheApp, uniqueEmail } from "./support";

failOnContentSecurityPolicyViolations();

const FEEDBACK_CSV = [
  "text,rating",
  "Waited forty minutes for a table,1",
  "Charged twice for one order,2",
  "The app crashes every time I log in,1",
].join("\n");

function tile(page: Page, label: string) {
  return page.locator("dt", { hasText: new RegExp(`^${label}$`) }).locator("xpath=..");
}

async function upload(page: Page, name: string) {
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Imports" }).click();
  await page.getByLabel("Choose file").setInputFiles({ name, mimeType: "text/csv", buffer: Buffer.from(FEEDBACK_CSV) });
  await page.getByRole("button", { name: "Upload", exact: true }).click();
  await expect(page.getByText("Imported 3 of 3 rows")).toBeVisible();
}

test("a new organisation's dashboard invites the first upload", async ({ page }) => {
  await registerThroughTheApp(page, uniqueEmail("dash-empty"), "Dashboard Empty E2E");

  await expect(page.getByRole("heading", { name: /fills in as feedback is analysed/ })).toBeVisible();
  await page.getByRole("link", { name: "Upload feedback" }).click();
  await expect(page).toHaveURL(/\/imports$/);
});

test("after an upload the dashboard counts it and says analysis is in progress", async ({ page }) => {
  await registerThroughTheApp(page, uniqueEmail("dash-upload"), "Dashboard Upload E2E");
  await upload(page, "dashboard.csv");

  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: "Dashboard" }).click();

  await expect(page.getByText("Analysis in progress")).toBeVisible();
  await expect(tile(page, "Feedback")).toContainText("3");
  await expect(tile(page, "Feedback")).toContainText("0 analysed · 3 waiting");
  await expect(tile(page, "Negative")).toContainText("Waiting for analysis");
  await expect(tile(page, "Average rating")).toContainText("1.3");

  // Nothing is analysed without the worker, so the charts say so rather than drawing empty axes.
  await expect(page.getByText("No analysed feedback in this period yet.")).toBeVisible();
  await expect(page.getByText("No analysed feedback to break down yet.")).toBeVisible();
});

test("each organisation's dashboard counts only its own feedback", async ({ browser }) => {
  const acme = await browser.newContext();
  const globex = await browser.newContext();
  const acmePage = await acme.newPage();
  const globexPage = await globex.newPage();

  await registerThroughTheApp(acmePage, uniqueEmail("dash-acme"), "Dashboard Acme E2E");
  await upload(acmePage, "acme.csv");

  await registerThroughTheApp(globexPage, uniqueEmail("dash-globex"), "Dashboard Globex E2E");

  await expect(globexPage.getByRole("heading", { name: /fills in as feedback is analysed/ })).toBeVisible();
  expect((await (await globexPage.request.get("/api/v1/analytics/summary")).json()).total_feedback).toBe(0);
  expect((await (await acmePage.request.get("/api/v1/analytics/summary")).json()).total_feedback).toBe(3);

  await acme.close();
  await globex.close();
});
