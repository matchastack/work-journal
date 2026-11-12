import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { PortfolioView, PublishedResume } from "../api/portfolio";
import { createQueryClient } from "../api/queryClient";
import { App } from "../App";
import { routes } from "../routes";

const ME = { id: "4c1ff1e0-8d2b-4c55-9c8e-1f0b3a6d2e71", githubLogin: "casey-example" };
const MASTER = {
  source: "master" as const,
  applicationId: null,
  name: "Master resume (version 3)",
  label: "Resume",
  confidentialTerms: [],
};
const TAILORED = {
  source: "application" as const,
  applicationId: "app_backend",
  name: "Tailored: Backend Engineer at Acme Example (2026-10-20)",
  label: "Backend Engineer resume",
  confidentialTerms: [],
};

function view(changes: Partial<PortfolioView> = {}): PortfolioView {
  return {
    handle: "casey-example",
    path: "/p/casey-example",
    published: false,
    publishedAt: null,
    version: 3,
    newestVersion: 3,
    settings: {
      handle: "casey-example",
      defaultVisibility: "public",
      openToWork: true,
      indexing: false,
    },
    resumes: [],
    options: [MASTER, TAILORED],
    confidential: [],
    ...changes,
  };
}

interface Sent {
  method: string;
  path: string;
  body: unknown;
  csrf: string | null;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Stands in for the API. `refuse` answers one request (`"POST /api/portfolio/publish"`) with an
 * error. */
function servePortfolio(start: PortfolioView, refuse: Record<string, Response> = {}) {
  const state = { view: start };
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const body: unknown = request.body === null ? undefined : await request.json();
      if (request.method !== "GET") {
        sent.push({
          method: request.method,
          path,
          body,
          csrf: request.headers.get("X-CSRF-Token"),
        });
      }
      const refused = refuse[`${request.method} ${path}`];
      if (refused !== undefined) {
        return refused;
      }
      switch (`${request.method} ${path}`) {
        case "GET /auth/me":
          return json(ME);
        case "GET /api/portfolio":
          return json(state.view);
        case "POST /api/portfolio/publish":
          state.view = { ...state.view, published: true, publishedAt: "2026-10-31T02:00:00Z" };
          return json(state.view);
        case "POST /api/portfolio/unpublish":
          state.view = { ...state.view, published: false, publishedAt: null };
          return json(state.view);
        case "PUT /api/portfolio/resumes": {
          const { resumes } = body as { resumes: PublishedResume[] };
          state.view = {
            ...state.view,
            resumes: resumes.map((resume) => ({
              ...resume,
              available: true,
              confidentialTerms: [],
            })),
          };
          return json(state.view);
        }
        default:
          return json({ detail: "Not Found" }, 404);
      }
    }),
  );
  return { sent, state };
}

function renderAt(path = "/portfolio") {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<App router={router} queryClient={createQueryClient({ retries: 0 })} />);
  return router;
}

async function section(name: string) {
  return within(await screen.findByRole("region", { name }));
}

beforeEach(() => {
  document.cookie = "__Host-wj_csrf=csrf-1; Secure; Path=/";
});

describe("the portfolio page", () => {
  it("previews the page and publishes it", async () => {
    const { sent } = servePortfolio(view());
    renderAt();
    expect(await screen.findByRole("heading", { level: 1, name: "Portfolio" })).toBeInTheDocument();
    const publishing = await section("Publishing");
    expect(publishing.getByRole("status")).toHaveTextContent("Not published.");
    expect(
      publishing.getByRole("link", { name: "https://journal.test/p/casey-example" }),
    ).toHaveAttribute("href", "https://journal.test/p/casey-example");
    expect(publishing.getByText("It will show version 3 of your profile.")).toBeInTheDocument();
    expect(publishing.getByText("Shown on the page")).toBeInTheDocument();
    expect(publishing.getByText("Asked not to index it")).toBeInTheDocument();
    expect(publishing.getByRole("link", { name: "Change these in Settings" })).toHaveAttribute(
      "href",
      "/settings",
    );

    const preview = await section("Preview");
    expect(preview.getByTitle("Preview of your portfolio page")).toHaveAttribute(
      "src",
      "/api/portfolio/preview",
    );
    expect(preview.getByTitle("Preview of your portfolio page")).toHaveAttribute(
      "sandbox",
      "allow-same-origin allow-downloads",
    );

    await userEvent.click(publishing.getByRole("button", { name: "Publish" }));
    expect(
      await publishing.findByText(/^Published since 31 Oct 2026, 10:00\.$/),
    ).toBeInTheDocument();
    expect(publishing.getByText("It shows version 3 of your profile.")).toBeInTheDocument();
    expect(sent).toEqual([
      { method: "POST", path: "/api/portfolio/publish", body: undefined, csrf: "csrf-1" },
    ]);

    await userEvent.click(publishing.getByRole("button", { name: "Unpublish" }));
    expect(await publishing.findByText("Not published.")).toBeInTheDocument();
    expect(sent.at(-1)).toEqual({
      method: "POST",
      path: "/api/portfolio/unpublish",
      body: undefined,
      csrf: "csrf-1",
    });
  });

  it("says to choose a handle in Settings before publishing", async () => {
    servePortfolio(
      view({ handle: null, path: null, settings: { ...view().settings, handle: null } }),
    );
    renderAt();
    const publishing = await section("Publishing");
    expect(publishing.getByRole("link", { name: "Choose one in Settings" })).toHaveAttribute(
      "href",
      "/settings",
    );
    expect(publishing.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("says a newer version reaches the page at the next refresh", async () => {
    servePortfolio(
      view({ published: true, publishedAt: "2026-10-30T02:00:00Z", newestVersion: 4 }),
    );
    renderAt();
    const publishing = await section("Publishing");
    expect(
      publishing.getByText(
        "It shows version 3 of your profile. Version 4 reaches it at your next refresh.",
      ),
    ).toBeInTheDocument();
  });

  it("has nothing to publish or preview without a profile", async () => {
    servePortfolio(view({ version: null, newestVersion: null, options: [] }));
    renderAt();
    const publishing = await section("Publishing");
    expect(publishing.getByText(/There's no profile to show yet/)).toBeInTheDocument();
    expect(publishing.getByRole("button", { name: "Publish" })).toBeDisabled();
    const preview = await section("Preview");
    expect(preview.queryByTitle("Preview of your portfolio page")).not.toBeInTheDocument();
    const resumes = await section("Resume downloads");
    expect(resumes.getByText(/No resumes to offer yet/)).toBeInTheDocument();
  });

  it("says why the page couldn't be published", async () => {
    const refused = json({ detail: "Choose the page's handle in Settings first" }, 409);
    servePortfolio(view(), { "POST /api/portfolio/publish": refused });
    renderAt();
    const publishing = await section("Publishing");
    await userEvent.click(publishing.getByRole("button", { name: "Publish" }));
    expect(await publishing.findByRole("alert")).toHaveTextContent(
      "Couldn't publish the page: Choose the page's handle in Settings first.",
    );
  });

  it("saves the resumes visitors can download, with their button text", async () => {
    const { sent } = servePortfolio(view());
    renderAt();
    const resumes = await section("Resume downloads");
    expect(resumes.getByText(/phone number/)).toBeInTheDocument();
    expect(resumes.queryByLabelText("Button text")).not.toBeInTheDocument();

    await userEvent.click(resumes.getByRole("checkbox", { name: /Offer Tailored: Backend/ }));
    const label = resumes.getByLabelText("Button text");
    expect(label).toHaveValue("Backend Engineer resume");
    await userEvent.clear(label);
    await userEvent.click(resumes.getByRole("button", { name: "Save resume downloads" }));
    expect(resumes.getByRole("alert")).toHaveTextContent("Give each resume a button text");
    expect(sent).toEqual([]);

    await userEvent.type(label, " One-page resume ");
    await userEvent.click(resumes.getByRole("checkbox", { name: /Offer Master resume/ }));
    await userEvent.click(resumes.getByRole("button", { name: "Save resume downloads" }));
    expect(await resumes.findByText("Saved.")).toBeInTheDocument();
    expect(sent).toEqual([
      {
        method: "PUT",
        path: "/api/portfolio/resumes",
        body: {
          resumes: [
            { source: "master", applicationId: null, label: "Resume" },
            { source: "application", applicationId: "app_backend", label: "One-page resume" },
          ],
        },
        csrf: "csrf-1",
      },
    ]);
  });

  it("keeps a chosen resume that isn't offered now, and says so", async () => {
    servePortfolio(
      view({
        resumes: [
          {
            source: "application",
            applicationId: "app_gone",
            label: "Old resume",
            available: false,
            confidentialTerms: [],
          },
        ],
      }),
    );
    renderAt();
    const resumes = await section("Resume downloads");
    expect(resumes.getByRole("checkbox", { name: /Offer A tailored resume/ })).toBeChecked();
    expect(resumes.getByText(/Not offered now/)).toBeInTheDocument();
    expect(resumes.getByLabelText("Button text")).toHaveValue("Old resume");
  });

  it("names each confidential term the page names, and where, and won't publish it", async () => {
    servePortfolio(
      view({
        confidential: [
          { term: "Falcon", where: "Experience: Engineer" },
          { term: "Falcon", where: "About: Profile" },
          { term: "Orion", where: "Page address" },
        ],
      }),
    );
    renderAt();
    const publishing = await section("Publishing");
    const alert = publishing.getByRole("alert");
    expect(alert).toHaveTextContent(
      "Your page names a confidential term, so it can't be published.",
    );
    expect(within(alert).getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      'Error: "Falcon" in Experience: Engineer; About: Profile',
      'Error: "Orion" in Page address',
    ]);
    expect(publishing.getByRole("button", { name: "Publish" })).toBeDisabled();
  });

  it("says visitors get page not found while a published page names a term", async () => {
    servePortfolio(
      view({
        published: true,
        publishedAt: "2026-10-30T02:00:00Z",
        confidential: [{ term: "Falcon", where: "Contact" }],
      }),
    );
    renderAt();
    const publishing = await section("Publishing");
    expect(publishing.getByRole("alert")).toHaveTextContent(
      'Visitors get "Page not found" while your page names a confidential term.',
    );
    expect(publishing.getByRole("button", { name: "Unpublish" })).toBeEnabled();
  });

  it("says which resumes name a confidential term", async () => {
    servePortfolio(
      view({
        resumes: [
          {
            source: "master",
            applicationId: null,
            label: "Resume",
            available: false,
            confidentialTerms: ["Falcon"],
          },
        ],
        options: [
          { ...MASTER, confidentialTerms: ["Falcon"] },
          { ...TAILORED, confidentialTerms: ["Falcon", "Orion"] },
        ],
      }),
    );
    renderAt();
    const resumes = await section("Resume downloads");
    expect(
      resumes.getByText(/not offered now, as it names your confidential term "Falcon"\./),
    ).toBeInTheDocument();
    const both = /can't be offered, as it names your confidential terms "Falcon" and "Orion"/;
    expect(resumes.getByText(both)).toBeInTheDocument();
    expect(resumes.getByRole("checkbox", { name: /Offer Master resume/ })).toBeEnabled();
    expect(resumes.getByRole("checkbox", { name: /Offer Tailored: Backend/ })).toBeDisabled();
  });
});
