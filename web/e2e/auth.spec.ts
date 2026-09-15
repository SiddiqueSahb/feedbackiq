/**
 * Authentication against the real API - Chromium, the built app, FastAPI and PostgreSQL.
 *
 * What these prove that the component tests cannot: the session really is an HttpOnly cookie the
 * page's JavaScript cannot read, the server really ends it, and the app follows the server's word.
 */
import { expect, request as playwrightRequest, test } from "@playwright/test";

import {
  PASSWORD,
  SESSION_COOKIE,
  registerThroughTheApi,
  registerThroughTheApp,
  signInThroughTheApp,
  uniqueEmail,
} from "./support";

test.beforeAll(async ({ request }) => {
  const health = await request.get("/api/health");
  expect(health.ok(), "The FeedbackIQ API must be running behind the app's /api proxy.").toBeTruthy();
});

test("registering creates an organisation and signs in with a cookie the page cannot read", async ({ page, context }) => {
  const email = uniqueEmail("ana");

  await registerThroughTheApp(page, email, "Acme E2E Ltd");

  await expect(page.getByText("Acme E2E Ltd").first()).toBeVisible();
  await expect(page.getByText(email)).toBeVisible();
  await expect(page.getByText("Owner")).toBeVisible();

  const [cookie] = (await context.cookies()).filter((c) => c.name === SESSION_COOKIE);
  expect(cookie, "the API set a session cookie").toBeDefined();
  expect(cookie!.httpOnly).toBe(true);
  expect(cookie!.sameSite).toBe("Lax");

  // The token is invisible to the page: not in document.cookie, storage or the rendered HTML.
  expect(await page.evaluate(() => document.cookie)).not.toContain(SESSION_COOKIE);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  expect(await page.content()).not.toContain(cookie!.value);
});

test("a visitor who is not signed in is sent to sign-in and brought back afterwards", async ({ page, request }) => {
  const email = uniqueEmail("ben");
  await registerThroughTheApi(request, email, "Globex E2E Inc");

  await page.goto("/imports");
  await expect(page).toHaveURL(/\/login\?next=%2Fimports$/);

  await signInThroughTheApp(page, email);

  await expect(page).toHaveURL(/\/imports$/);
  await expect(page.getByRole("heading", { name: "Imports", exact: true })).toBeVisible();
  await expect(page.getByText("Globex E2E Inc").first()).toBeVisible();
});

test("a wrong password shows the API's message and does not sign in", async ({ page, request, context }) => {
  const email = uniqueEmail("cara");
  await registerThroughTheApi(request, email, "Initech E2E");

  await page.goto("/login");
  await signInThroughTheApp(page, email, "definitely not the password");

  await expect(page.getByRole("alert")).toHaveText("Incorrect email or password.");
  await expect(page).toHaveURL(/\/login$/);
  expect((await context.cookies()).some((c) => c.name === SESSION_COOKIE)).toBe(false);
});

test("registering an email that already has an account says so", async ({ page, request }) => {
  const email = uniqueEmail("dup");
  await registerThroughTheApi(request, email, "First Org E2E");

  await page.goto("/register");
  await page.getByLabel("Organisation name").fill("Second Org E2E");
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page.getByRole("alert")).toHaveText("An account with this email already exists.");
});

test("signing out ends the session on the server, not just in the browser", async ({ page, context, baseURL }) => {
  await registerThroughTheApp(page, uniqueEmail("dev"), "Umbrella E2E");
  const [cookie] = (await context.cookies()).filter((c) => c.name === SESSION_COOKIE);

  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.goto("/dashboard");
  await expect(page).toHaveURL(/\/login\?next=%2Fdashboard$/);

  // Replaying the old token directly against the API proves the server deleted the session.
  const replay = await playwrightRequest.newContext({
    baseURL,
    extraHTTPHeaders: { Cookie: `${SESSION_COOKIE}=${cookie!.value}` },
  });
  expect((await replay.get("/api/v1/auth/me")).status()).toBe(401);
  await replay.dispose();
});

test("a session ended elsewhere sends the user back to sign in", async ({ page, context, baseURL }) => {
  await registerThroughTheApp(page, uniqueEmail("eve"), "Hooli E2E");
  const [cookie] = (await context.cookies()).filter((c) => c.name === SESSION_COOKIE);

  // Sign out from "another device": the browser keeps its cookie, but the server forgets it.
  const elsewhere = await playwrightRequest.newContext({
    baseURL,
    extraHTTPHeaders: { Cookie: `${SESSION_COOKIE}=${cookie!.value}` },
  });
  expect((await elsewhere.post("/api/v1/auth/logout")).status()).toBe(204);
  await elsewhere.dispose();

  await page.reload();

  await expect(page).toHaveURL(/\/login\?next=%2Fdashboard$/);
  await expect(page.getByRole("heading", { name: "Sign in to FeedbackIQ" })).toBeVisible();
});

test("two organisations signed in side by side each see only their own", async ({ browser }) => {
  const first = await browser.newContext();
  const second = await browser.newContext();
  const firstPage = await first.newPage();
  const secondPage = await second.newPage();

  await registerThroughTheApp(firstPage, uniqueEmail("frank"), "Stark E2E Industries");
  await registerThroughTheApp(secondPage, uniqueEmail("grace"), "Wayne E2E Enterprises");

  await expect(firstPage.getByText("Stark E2E Industries").first()).toBeVisible();
  await expect(firstPage.getByText("Wayne E2E Enterprises")).toHaveCount(0);
  await expect(secondPage.getByText("Wayne E2E Enterprises").first()).toBeVisible();
  await expect(secondPage.getByText("Stark E2E Industries")).toHaveCount(0);

  await first.close();
  await second.close();
});
