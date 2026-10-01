import { describe, expect, it, vi } from "vitest";

import { api, CSRF_HEADER, csrfToken, readCookie } from "./client";

describe("readCookie", () => {
  it("finds a cookie among others", () => {
    expect(readCookie("b", "a=1; b=two=2; c=3")).toBe("two=2");
    expect(readCookie("__Host-wj_csrf", "__Host-wj_csrf=abc")).toBe("abc");
  });

  it("returns undefined for a missing cookie", () => {
    expect(readCookie("b", "a=1; bb=2")).toBeUndefined();
    expect(readCookie("b", "")).toBeUndefined();
  });
});

describe("csrfToken", () => {
  it("reads the token from the HTTPS cookie, or from the local http:// one", () => {
    expect(csrfToken("__Host-wj_csrf=secure; other=1")).toBe("secure");
    expect(csrfToken("other=1; wj_csrf=local")).toBe("local");
    expect(csrfToken("other=1")).toBeUndefined();
  });
});

describe("the API client", () => {
  it("sends the CSRF token on requests that change something, and only those", async () => {
    document.cookie = "__Host-wj_csrf=token-1; Secure; Path=/";
    const fetch = vi.fn<(request: Request) => Promise<Response>>(() =>
      Promise.resolve(new Response(null, { status: 204 })),
    );
    vi.stubGlobal("fetch", fetch);
    await api.GET("/auth/me");
    await api.POST("/auth/logout");
    const [read, change] = fetch.mock.calls.map(([request]) => request);
    expect(read?.headers.has(CSRF_HEADER)).toBe(false);
    expect(change?.headers.get(CSRF_HEADER)).toBe("token-1");
  });
});
