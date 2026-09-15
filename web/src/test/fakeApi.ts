/**
 * A stand-in for the FeedbackIQ API in component tests: canned responses by "METHOD /path".
 *
 * It replaces `fetch` - the boundary between the app and the network - and nothing inside the app.
 * The session hooks, guards and query cache under test are the real ones. The real API, with real
 * cookies, is exercised by the Playwright tests in e2e/.
 */
import { vi } from "vitest";

export interface RecordedCall {
  method: string;
  url: string;
  body: unknown;
}

type Reply = { status: number; body?: unknown };
type Route = Reply | ((call: RecordedCall) => Reply);

export function fakeApi(routes: Record<string, Route>): { calls: RecordedCall[] } {
  const calls: RecordedCall[] = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const url = String(input);
      const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body;
      const call = { method, url, body };
      calls.push(call);

      const route = routes[`${method} ${url}`];
      const reply = !route
        ? { status: 404, body: { detail: `No fake for ${method} ${url}` } }
        : typeof route === "function"
          ? route(call)
          : route;

      return new Response(reply.body === undefined ? null : JSON.stringify(reply.body), {
        status: reply.status,
        headers: { "Content-Type": "application/json" },
      });
    }),
  );

  return { calls };
}
