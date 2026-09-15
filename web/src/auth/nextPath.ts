const DEFAULT_PATH = "/dashboard";

/**
 * Where to go after signing in: the page the person originally asked for, or the dashboard.
 *
 * The `next` value comes from the URL, so anyone can craft it. Only a path on this app is accepted
 * - never another site ("//evil.example", "https://evil.example", "/\evil.example"), which would
 * turn the sign-in page into a way to send people somewhere convincing-looking after a real login.
 */
export function safeNextPath(value: string | null | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) {
    return DEFAULT_PATH;
  }

  let url: URL;
  try {
    url = new URL(value, window.location.origin);
  } catch {
    return DEFAULT_PATH;
  }

  if (url.origin !== window.location.origin) {
    return DEFAULT_PATH;
  }

  if (url.pathname === "/login" || url.pathname === "/register") {
    return DEFAULT_PATH;
  }

  return `${url.pathname}${url.search}${url.hash}`;
}
