import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const PASSWORD = "correct horse battery staple";
export const SESSION_COOKIE = "feedbackiq_session";

/** An address no earlier run has registered, so tests never depend on database cleanup. */
export function uniqueEmail(label: string): string {
  const suffix = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return `${label}-${suffix}@e2e.feedbackiq.example`;
}

/** Register through the real sign-up page and wait for the dashboard. */
export async function registerThroughTheApp(page: Page, email: string, organisation: string): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Organisation name").fill(organisation);
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();

  await expect(page).toHaveURL(/\/dashboard$/);
  // exact: Playwright matches names as substrings by default, and the empty state's heading also
  // contains the word "dashboard".
  await expect(page.getByRole("heading", { name: "Dashboard", exact: true })).toBeVisible();
}

/** Register directly against the API, for tests that start from an existing account. */
export async function registerThroughTheApi(request: APIRequestContext, email: string, organisation: string) {
  const response = await request.post("/api/v1/auth/register", {
    data: { email, password: PASSWORD, organisation_name: organisation },
  });
  expect(response.status(), await response.text()).toBe(201);
}

export async function signInThroughTheApp(page: Page, email: string, password = PASSWORD): Promise<void> {
  await page.getByLabel("Work email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}
