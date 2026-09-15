/**
 * Authentication in the app, end to end inside the browser code: routing by session, signing in,
 * registering and signing out.
 *
 * `fetch` is replaced by canned API answers (test/fakeApi.ts); the guards, session hooks and query
 * cache are the real ones. What the real API does with real cookies is tested in e2e/ with
 * Playwright.
 */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { fakeApi } from "../test/fakeApi";
import { renderApp } from "../test/renderApp";

const OWNER = {
  email: "ana@acme.example",
  organisation: { id: "9b1c0000-0000-0000-0000-000000000001", name: "Acme Ltd", role: "owner" },
};
const SIGNED_OUT = { status: 401, body: { detail: "Not signed in." } };
const PASSWORD = "correct horse battery staple";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function currentSearch(name: string) {
  return new URLSearchParams(window.location.search).get(name);
}

describe("routing by session", () => {
  it("sends a visitor who is not signed in to sign-in, remembering where they were going", async () => {
    fakeApi({ "GET /api/v1/auth/me": SIGNED_OUT });

    renderApp("/imports?view=recent");

    expect(await screen.findByRole("heading", { name: "Sign in to FeedbackIQ" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
    expect(currentSearch("next")).toBe("/imports?view=recent");
  });

  it("shows the app, and the organisation the API reported, to a signed-in user", async () => {
    fakeApi({ "GET /api/v1/auth/me": { status: 200, body: OWNER } });

    renderApp("/dashboard");

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Main" })).toBeInTheDocument();
    expect(screen.getAllByText("Acme Ltd").length).toBeGreaterThan(0);
    expect(screen.getByText("ana@acme.example")).toBeInTheDocument();
    expect(screen.getByText("Owner")).toBeInTheDocument();
  });

  it("opens the dashboard from the root path", async () => {
    fakeApi({ "GET /api/v1/auth/me": { status: 200, body: OWNER } });

    renderApp("/");

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/dashboard");
  });

  it("explains, rather than showing refused pages, when the user belongs to no organisation", async () => {
    fakeApi({ "GET /api/v1/auth/me": { status: 200, body: { email: "loner@example.com", organisation: null } } });

    renderApp("/dashboard");

    expect(await screen.findByRole("heading", { name: /not part of an organisation/i })).toBeInTheDocument();
    expect(screen.queryByRole("navigation", { name: "Main" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("moves a signed-in user on from the sign-in page", async () => {
    fakeApi({ "GET /api/v1/auth/me": { status: 200, body: OWNER } });

    renderApp("/login?next=%2Fimports");

    expect(await screen.findByRole("heading", { name: "Imports" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/imports");
  });

  it("offers a retry, not a sign-in page, when the API cannot answer", async () => {
    let healthy = false;
    fakeApi({
      "GET /api/v1/auth/me": () =>
        healthy ? { status: 200, body: OWNER } : { status: 503, body: { detail: "Service unavailable." } },
    });

    renderApp("/dashboard");

    expect(await screen.findByRole("alert", {}, { timeout: 5000 })).toHaveTextContent("Service unavailable.");
    expect(window.location.pathname).toBe("/dashboard");

    healthy = true;
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });
});

describe("signing in", () => {
  it("signs in and continues to the page that was asked for", async () => {
    let signedIn = false;
    const api = fakeApi({
      "GET /api/v1/auth/me": () => (signedIn ? { status: 200, body: OWNER } : SIGNED_OUT),
      "POST /api/v1/auth/login": () => {
        signedIn = true;
        return { status: 200, body: OWNER };
      },
    });

    renderApp("/login?next=%2Fimports");

    await userEvent.type(await screen.findByLabelText("Work email"), "ana@acme.example");
    await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Imports" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/imports");
    expect(api.calls).toContainEqual({
      method: "POST",
      url: "/api/v1/auth/login",
      body: { email: "ana@acme.example", password: PASSWORD },
    });
  });

  it("shows the API's message when sign-in is refused, and stays signed out", async () => {
    fakeApi({
      "GET /api/v1/auth/me": SIGNED_OUT,
      "POST /api/v1/auth/login": { status: 401, body: { detail: "Incorrect email or password." } },
    });

    renderApp("/login");

    await userEvent.type(await screen.findByLabelText("Work email"), "ana@acme.example");
    await userEvent.type(screen.getByLabelText("Password"), "not the password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect email or password.");
    expect(window.location.pathname).toBe("/login");
  });

  it("shows a disabled account's message", async () => {
    fakeApi({
      "GET /api/v1/auth/me": SIGNED_OUT,
      "POST /api/v1/auth/login": { status: 403, body: { detail: "This account has been disabled." } },
    });

    renderApp("/login");

    await userEvent.type(await screen.findByLabelText("Work email"), "ana@acme.example");
    await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("This account has been disabled.");
  });

  it("never follows a next parameter to another site", async () => {
    const appOrigin = window.location.origin;
    let signedIn = false;
    fakeApi({
      "GET /api/v1/auth/me": () => (signedIn ? { status: 200, body: OWNER } : SIGNED_OUT),
      "POST /api/v1/auth/login": () => {
        signedIn = true;
        return { status: 200, body: OWNER };
      },
    });

    renderApp("/login?next=%2F%2Fevil.example%2Fphish");

    await userEvent.type(await screen.findByLabelText("Work email"), "ana@acme.example");
    await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(window.location.origin).toBe(appOrigin);
    expect(window.location.pathname).toBe("/dashboard");
  });

  it("writes nothing to browser storage while signing in and using the app", async () => {
    const localWrite = vi.spyOn(Storage.prototype, "setItem");
    let signedIn = false;
    fakeApi({
      "GET /api/v1/auth/me": () => (signedIn ? { status: 200, body: OWNER } : SIGNED_OUT),
      "POST /api/v1/auth/login": () => {
        signedIn = true;
        return { status: 200, body: OWNER };
      },
    });

    renderApp("/login");

    await userEvent.type(await screen.findByLabelText("Work email"), "ana@acme.example");
    await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await screen.findByRole("heading", { name: "Dashboard" });

    expect(localWrite).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
  });
});

describe("registering", () => {
  it("creates the account and its organisation, then opens the dashboard", async () => {
    let signedIn = false;
    const api = fakeApi({
      "GET /api/v1/auth/me": () => (signedIn ? { status: 200, body: OWNER } : SIGNED_OUT),
      "POST /api/v1/auth/register": () => {
        signedIn = true;
        return { status: 201, body: OWNER };
      },
    });

    renderApp("/register");

    await userEvent.type(await screen.findByLabelText("Organisation name"), "Acme Ltd");
    await userEvent.type(screen.getByLabelText("Work email"), "ana@acme.example");
    await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    expect(api.calls).toContainEqual({
      method: "POST",
      url: "/api/v1/auth/register",
      body: { email: "ana@acme.example", password: PASSWORD, organisation_name: "Acme Ltd" },
    });
  });

  it("shows the password rule before anything is submitted", async () => {
    fakeApi({ "GET /api/v1/auth/me": SIGNED_OUT });

    renderApp("/register");

    expect(await screen.findByLabelText("Password")).toHaveAccessibleDescription(/at least 12 characters/i);
  });

  it("shows why registration was refused", async () => {
    fakeApi({
      "GET /api/v1/auth/me": SIGNED_OUT,
      "POST /api/v1/auth/register": { status: 409, body: { detail: "An account with this email already exists." } },
    });

    renderApp("/register");

    await userEvent.type(await screen.findByLabelText("Organisation name"), "Acme Ltd");
    await userEvent.type(screen.getByLabelText("Work email"), "ana@acme.example");
    await userEvent.type(screen.getByLabelText("Password"), PASSWORD);
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("An account with this email already exists.");
    expect(window.location.pathname).toBe("/register");
  });
});

describe("signing out", () => {
  it("ends the session on the server, forgets the user and returns to sign-in", async () => {
    let signedIn = true;
    const api = fakeApi({
      "GET /api/v1/auth/me": () => (signedIn ? { status: 200, body: OWNER } : SIGNED_OUT),
      "POST /api/v1/auth/logout": () => {
        signedIn = false;
        return { status: 204 };
      },
    });

    renderApp("/dashboard");

    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "Sign in to FeedbackIQ" })).toBeInTheDocument();
    await waitFor(() => expect(window.location.pathname).toBe("/login"));
    expect(api.calls.filter((call) => call.method === "POST" && call.url === "/api/v1/auth/logout")).toHaveLength(1);
    expect(screen.queryByText("Acme Ltd")).not.toBeInTheDocument();
    expect(screen.queryByText("ana@acme.example")).not.toBeInTheDocument();
  });

  it("stays signed in, and says so, if the server could not sign out", async () => {
    fakeApi({
      "GET /api/v1/auth/me": { status: 200, body: OWNER },
      "POST /api/v1/auth/logout": { status: 500, body: { detail: "Could not sign out." } },
    });

    renderApp("/dashboard");

    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Could not sign out.");
    expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  });
});
