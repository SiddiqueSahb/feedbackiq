/**
 * What a 401 means - app/queryClient.ts
 *
 * The rule: a 401 from a data request means the server has ended the session, so the app forgets
 * everything it cached. A 401 from signing in is only a wrong password, and must not.
 */
import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import { SESSION_KEY } from "../auth/sessionCache";
import { createQueryClient } from "./queryClient";

const USER = { email: "ana@acme.example", organisation: { id: "o1", name: "Acme Ltd", role: "owner" } };

function reject(status: number) {
  return () => Promise.reject(new ApiError(status, "refused"));
}

describe("a 401 from a data request", () => {
  it("marks the app signed out and forgets every cached response", async () => {
    const client = createQueryClient();
    client.setQueryData(SESSION_KEY, USER);
    client.setQueryData(["feedback"], { items: ["Acme's private feedback"] });

    await client
      .fetchQuery({ queryKey: ["summary"], queryFn: reject(401), retry: false })
      .catch(() => undefined);

    expect(client.getQueryData(SESSION_KEY)).toBeNull();
    expect(client.getQueryData(["feedback"])).toBeUndefined();
  });

  it("does the same for a data-changing request such as an upload", async () => {
    const client = createQueryClient();
    client.setQueryData(SESSION_KEY, USER);

    const upload = client.getMutationCache().build(client, { mutationFn: reject(401) });
    await upload.execute(undefined).catch(() => undefined);

    expect(client.getQueryData(SESSION_KEY)).toBeNull();
  });
});

describe("responses that do not end the session", () => {
  it("leaves the session alone for a wrong password", async () => {
    const client = createQueryClient();
    client.setQueryData(SESSION_KEY, null);
    client.setQueryData(["remembered"], "kept");

    const login = client
      .getMutationCache()
      .build(client, { mutationFn: reject(401), meta: { credentialCheck: true } });
    await login.execute(undefined).catch(() => undefined);

    expect(client.getQueryData(["remembered"])).toBe("kept");
  });

  it("leaves the session alone for other failures", async () => {
    const client = createQueryClient();
    client.setQueryData(SESSION_KEY, USER);

    for (const status of [403, 404, 500]) {
      await client
        .fetchQuery({ queryKey: ["summary", status], queryFn: reject(status), retry: false })
        .catch(() => undefined);
    }

    expect(client.getQueryData(SESSION_KEY)).toEqual(USER);
  });

  it("does not retry a client error, which is an answer rather than a glitch", async () => {
    const client = createQueryClient();
    let attempts = 0;

    await client
      .fetchQuery({
        queryKey: ["missing"],
        queryFn: () => {
          attempts += 1;
          return Promise.reject(new ApiError(404, "Not found."));
        },
      })
      .catch(() => undefined);

    expect(attempts).toBe(1);
  });
});
