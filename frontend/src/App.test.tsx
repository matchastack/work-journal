import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { createQueryClient } from "./api/queryClient";
import { App } from "./App";
import { PAGES } from "./pages";
import { routes } from "./routes";

const ME = { id: "4c1ff1e0-8d2b-4c55-9c8e-1f0b3a6d2e71", githubLogin: "casey-example" };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Stands in for the API: signed in until someone signs out. */
function serveApi({ signedIn = true } = {}) {
  let session = signedIn;
  const fetch = vi.fn((request: Request) => {
    const path = new URL(request.url).pathname;
    if (path === "/auth/me") {
      return Promise.resolve(session ? json(ME) : json({ detail: "Not signed in" }, 401));
    }
    if (path === "/auth/logout" && request.method === "POST") {
      session = false;
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    return Promise.resolve(json({ detail: "Not Found" }, 404));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function renderAt(path: string) {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<App router={router} queryClient={createQueryClient({ retries: 0 })} />);
  return router;
}

describe("the web app", () => {
  it("sends anyone who isn't signed in to the sign-in page", async () => {
    serveApi({ signedIn: false });
    const router = renderAt("/journal");
    const signIn = await screen.findByRole("link", { name: "Sign in with GitHub" });
    expect(signIn).toHaveAttribute("href", "/auth/login");
    expect(router.state.location.pathname).toBe("/sign-in");
  });

  it("opens the inbox after sign-in, with navigation to every page", async () => {
    serveApi();
    const router = renderAt("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Inbox" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/inbox");
    const nav = screen.getByRole("navigation", { name: "Main" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((link) => link.textContent)).toEqual(PAGES.map((page) => page.title));
    expect(within(nav).getByRole("link", { name: "Inbox" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByText("@casey-example")).toBeInTheDocument();
  });

  it("moves between pages", async () => {
    serveApi();
    renderAt("/inbox");
    await userEvent.click(await screen.findByRole("link", { name: "Settings" }));
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    expect(screen.getByText("This page arrives with T-048.")).toBeInTheDocument();
  });

  it("sends the CSRF token when signing out", async () => {
    document.cookie = "__Host-wj_csrf=csrf-123; Secure; Path=/";
    const fetch = serveApi();
    renderAt("/inbox");
    await userEvent.click(await screen.findByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("link", { name: "Sign in with GitHub" })).toBeInTheDocument();
    const logout = fetch.mock.calls.map(([request]) => request).find((r) => r.method === "POST");
    expect(logout?.headers.get("X-CSRF-Token")).toBe("csrf-123");
  });

  it("shows a page that doesn't exist as not found", async () => {
    serveApi();
    renderAt("/nowhere");
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("writes only ASCII characters", async () => {
    const nonAscii = () => document.documentElement.outerHTML.match(/[^\t\n\r\x20-\x7e]/gu) ?? [];
    vi.stubGlobal("fetch", vi.fn(() => new Promise<Response>(() => undefined)));
    renderAt("/inbox");
    expect(await screen.findByRole("status")).toHaveTextContent("Loading...");
    expect(nonAscii()).toEqual([]);
    cleanup();
    serveApi({ signedIn: false });
    renderAt("/sign-in");
    await screen.findByRole("link", { name: "Sign in with GitHub" });
    expect(nonAscii()).toEqual([]);
    cleanup();
    serveApi();
    renderAt("/inbox");
    await screen.findByRole("heading", { level: 1, name: "Inbox" });
    expect(nonAscii()).toEqual([]);
  });

  it("says so when the server can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))));
    renderAt("/inbox");
    expect(await screen.findByRole("alert")).toHaveTextContent("can't reach its server");
  });
});
