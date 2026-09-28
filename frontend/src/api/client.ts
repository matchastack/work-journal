import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";

/** The cookie and header of the CSRF check in `backend/app/auth/sessions.py`. */
export const CSRF_COOKIE = "__Host-wj_csrf";
export const CSRF_HEADER = "X-CSRF-Token";
const SAFE_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

/** A cookie's value, from the cookies that page scripts can read. */
export function readCookie(name: string, cookies: string = document.cookie): string | undefined {
  for (const pair of cookies.split(";")) {
    const separator = pair.indexOf("=");
    if (separator !== -1 && pair.slice(0, separator).trim() === name) {
      return pair.slice(separator + 1).trim();
    }
  }
  return undefined;
}

/** Sends the session's CSRF token with every request that changes something. */
export const sendCsrfToken: Middleware = {
  onRequest({ request }) {
    const token = readCookie(CSRF_COOKIE);
    if (token !== undefined && !SAFE_METHODS.has(request.method)) {
      request.headers.set(CSRF_HEADER, token);
    }
    return request;
  },
};

/** The API, typed from its OpenAPI schema. `npm run api` regenerates `schema.d.ts`. */
export const api = createClient<paths>({
  baseUrl: globalThis.location.origin,
  // Look `fetch` up on each call, so tests can stand in for the API.
  fetch: (request) => globalThis.fetch(request),
});
api.use(sendCsrfToken);
