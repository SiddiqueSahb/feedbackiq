/**
 * The one way the app talks to the FeedbackIQ API.
 *
 *   apiRequest<T>("/api/v1/auth/me")                           GET, JSON back
 *   apiRequest<T>("/api/v1/auth/login", { method: "POST", json: {...} })
 *   apiRequest<T>("/api/v1/imports", { method: "POST", form })  multipart upload
 *
 * Authentication is not handled here, and deliberately so. Requests go to this app's own origin
 * (`/api/...`), and the browser attaches the HttpOnly session cookie itself because the request
 * is same-origin. The app never sees, stores or sends a token, and never sends an organisation
 * id: the server decides both from the cookie.
 *
 * Every failure becomes an `ApiError` with the HTTP status and a message a person can read, so
 * pages handle errors one way whatever went wrong.
 */

export class ApiError extends Error {
  /** HTTP status, or 0 when the request never reached the API. */
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** A JSON body. */
  json?: unknown;
  /** A multipart body, e.g. a file upload. The browser sets the boundary header. */
  form?: FormData;
  signal?: AbortSignal;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  let body: BodyInit | undefined;

  if (options.form) {
    body = options.form;
  } else if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  }

  let response: Response;

  try {
    response = await fetch(path, {
      method: options.method ?? "GET",
      headers,
      body,
      // The default for same-origin requests, stated so nobody "fixes" it to "include": the
      // cookie must only ever go to this origin.
      credentials: "same-origin",
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError(0, "Could not reach FeedbackIQ. Check your connection and try again.");
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const payload = await readJson(response);

  if (!response.ok) {
    throw new ApiError(response.status, messageFrom(payload, response.status));
  }

  return payload as T;
}

async function readJson(response: Response): Promise<unknown> {
  const text = await response.text();

  if (!text) {
    return undefined;
  }

  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

/**
 * A readable message from FastAPI's error body.
 *
 * `detail` is either a string written for people ("Incorrect email or password.") or, for a
 * request that failed validation, a list of `{loc, msg}` entries.
 */
export function messageFrom(payload: unknown, status: number): string {
  const detail = (payload as { detail?: unknown } | undefined)?.detail;

  if (typeof detail === "string" && detail) {
    return detail;
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { loc?: unknown[]; msg?: string };
    const field = first.loc?.filter((part) => part !== "body").join(".");

    if (first.msg) {
      return field ? `${humanise(field)}: ${first.msg}` : first.msg;
    }
  }

  return fallbackMessage(status);
}

function humanise(field: string): string {
  const words = field.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function fallbackMessage(status: number): string {
  if (status === 401) return "Your session has ended. Sign in again.";
  if (status === 403) return "You do not have access to this.";
  if (status === 404) return "Not found.";
  if (status === 413) return "The file is too large.";
  if (status >= 500) return "Something went wrong on our side. Try again in a moment.";
  return "The request could not be completed.";
}
