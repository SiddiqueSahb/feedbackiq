import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiRequest, messageFrom } from "./client";

function respondWith(status: number, body?: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(body === undefined ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The ApiError a request rejects with. Fails the test if it resolves or rejects with anything else. */
async function rejectionOf(request: Promise<unknown>): Promise<ApiError> {
  const reason = await request.then(
    () => {
      throw new Error("expected the request to fail, but it succeeded");
    },
    (error: unknown) => error,
  );

  expect(reason).toBeInstanceOf(ApiError);
  return reason as ApiError;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("apiRequest", () => {
  it("returns the parsed JSON body", async () => {
    respondWith(200, { email: "ana@acme.example", organisation: null });

    await expect(apiRequest("/api/v1/auth/me")).resolves.toEqual({
      email: "ana@acme.example",
      organisation: null,
    });
  });

  it("calls this app's own origin and never asks the browser to send credentials elsewhere", async () => {
    const fetchMock = respondWith(200, {});

    await apiRequest("/api/v1/auth/me");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/auth/me");
    expect(init.credentials).toBe("same-origin");
  });

  it("never sends an Authorization header or a token of its own", async () => {
    const fetchMock = respondWith(200, {});

    await apiRequest("/api/v1/feedback");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(Object.keys(init.headers as Record<string, string>)).toEqual(["Accept"]);
  });

  it("sends JSON bodies as JSON", async () => {
    const fetchMock = respondWith(200, {});

    await apiRequest("/api/v1/auth/login", {
      method: "POST",
      json: { email: "ana@acme.example", password: "correct horse battery staple" },
    });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(init.body as string)).toEqual({
      email: "ana@acme.example",
      password: "correct horse battery staple",
    });
  });

  it("leaves the content type of a multipart upload to the browser", async () => {
    const fetchMock = respondWith(201, {});
    const form = new FormData();
    form.append("file", new Blob(["text\nhello\n"], { type: "text/csv" }), "feedback.csv");

    await apiRequest("/api/v1/imports", { method: "POST", form });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.body).toBe(form);
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("returns nothing for 204 No Content", async () => {
    respondWith(204);

    await expect(apiRequest("/api/v1/auth/logout", { method: "POST" })).resolves.toBeUndefined();
  });

  it("turns an error response into an ApiError with the server's message", async () => {
    respondWith(401, { detail: "Incorrect email or password." });

    const error = await rejectionOf(apiRequest("/api/v1/auth/login", { method: "POST", json: {} }));

    expect(error.status).toBe(401);
    expect(error.message).toBe("Incorrect email or password.");
  });

  it("reports an unreachable API as status 0 with a readable message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await rejectionOf(apiRequest("/api/v1/auth/me"));

    expect(error.status).toBe(0);
    expect(error.message).toMatch(/could not reach/i);
  });

  it("passes an aborted request through untouched", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")));

    await expect(apiRequest("/api/v1/auth/me")).rejects.toMatchObject({ name: "AbortError" });
  });

  it("copes with an error body that is not JSON", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>Bad gateway</html>", { status: 502 })));

    const error = await rejectionOf(apiRequest("/api/v1/feedback"));

    expect(error.status).toBe(502);
    expect(error.message).toMatch(/something went wrong/i);
  });
});

describe("messageFrom", () => {
  it("uses a string detail as it is", () => {
    expect(messageFrom({ detail: "An account with this email already exists." }, 409)).toBe(
      "An account with this email already exists.",
    );
  });

  it("names the field of a validation error", () => {
    const payload = { detail: [{ loc: ["body", "organisation_name"], msg: "Field required", type: "missing" }] };

    expect(messageFrom(payload, 422)).toBe("Organisation name: Field required");
  });

  it("falls back to a message for the status when there is no detail", () => {
    expect(messageFrom(undefined, 401)).toMatch(/sign in again/i);
    expect(messageFrom({}, 413)).toMatch(/too large/i);
    expect(messageFrom(null, 500)).toMatch(/something went wrong/i);
  });
});
