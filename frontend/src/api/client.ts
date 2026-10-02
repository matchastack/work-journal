import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";

/**
 * The cookies and header of the CSRF check in `backend/app/auth/sessions.py`. The token is in
 * `__Host-wj_csrf` over HTTPS, and in `wj_csrf` on a local http:// address, where the cookie
 * can't be Secure.
 */
export const CSRF_COOKIES = ["__Host-wj_csrf", "wj_csrf"] as const;
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

/** The session's CSRF token, from whichever CSRF cookie the API set. */
export function csrfToken(cookies: string = document.cookie): string | undefined {
  for (const name of CSRF_COOKIES) {
    const token = readCookie(name, cookies);
    if (token !== undefined) {
      return token;
    }
  }
  return undefined;
}

/** Sends the session's CSRF token with every request that changes something. */
export const sendCsrfToken: Middleware = {
  onRequest({ request }) {
    const token = csrfToken();
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
