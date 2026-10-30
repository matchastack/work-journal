import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { LinkedInPack, LinkedInPage, LinkedInSection } from "../api/linkedin";
import { createQueryClient } from "../api/queryClient";
import { App } from "../App";
import { routes } from "../routes";

// Tests run with TZ=Asia/Singapore (vite.config.ts), 8 hours ahead of UTC.
const ME = { id: "4c1ff1e0-8d2b-4c55-9c8e-1f0b3a6d2e71", githubLogin: "casey-example" };
const HEADLINE = "Software Engineer | Python services and data tooling";
const ABOUT = "I build Python services at Northwind Traders.\n\nBefore, I built research tools.";
const NORTHWIND = "I build Python services with Kafka.";
const NORTHWIND_LABEL = "Position: Software Engineer at Northwind Traders";
const DIGEST = "a".repeat(64);
const NEW_DIGEST = "b".repeat(64);

function section(key: string, label: string, text: string, fields: Partial<LinkedInSection> = {}) {
  const kind = key.startsWith("position:") ? "position" : (key as "headline" | "about");
  const limits = { headline: 220, about: 2600, position: 2000 };
  return {
    key,
    kind,
    label,
    text,
    length: text.length,
    limit: limits[kind],
    flagged: false,
    report: {
      text,
      passed: true,
      attempts: 1,
      factIds: [],
      findings: [],
      claims: [],
      derivations: [],
    },
    notes: [],
    ...fields,
  } satisfies LinkedInSection;
}

const FLAGGED = section("position:northwind", NORTHWIND_LABEL, NORTHWIND, {
  flagged: true,
  report: {
    text: NORTHWIND,
    passed: false,
    attempts: 2,
    factIds: [],
    findings: [
      { check: "claims", severity: "error", code: "added_tool", message: "No fact names Kafka." },
    ],
    claims: [],
    derivations: [],
  },
  notes: ['LinkedIn shows "Software Engineer" for Northwind Traders.'],
});

/** A pack whose headline and About changed, with a flagged position and one to take off. */
function pack(fields: Partial<LinkedInPack> = {}): LinkedInPack {
  return {
    digest: DIGEST,
    profileVersion: 2,
    sections: [
      section("headline", "Headline", HEADLINE),
      section("about", "About", ABOUT),
      FLAGGED,
    ],
    unchanged: 1,
    removed: ["Position: Tutor at A School"],
    ...fields,
  };
}

function page(fields: Partial<LinkedInPage> = {}): LinkedInPage {
  return { profileVersion: 2, markedAt: "2026-10-12T01:00:00Z", pack: pack(), ...fields };
}

/** After marking done: only the flagged section is still listed. */
function afterMarking(): LinkedInPage {
  return page({
    markedAt: "2026-10-30T02:30:00Z",
    pack: pack({ sections: [FLAGGED], unchanged: 3, removed: [] }),
  });
}

interface Sent {
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

/**
 * Stands in for the API: GET /api/linkedin answers `before` until the pack is marked done, then
 * `after`, whether marking worked or not; `done` answers marking it.
 */
function serve(
  before: LinkedInPage,
  after: LinkedInPage = before,
  done: Response = json({ marked: 3, flagged: [] }),
) {
  const sent: Sent[] = [];
  let current = before;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/auth/me") {
        return json(ME);
      }
      if (path === "/api/linkedin") {
        return json(current);
      }
      if (path === "/api/linkedin/done" && request.method === "POST") {
        sent.push({ path, body: await request.json(), csrf: request.headers.get("X-CSRF-Token") });
        current = after;
        return done;
      }
      return json({ detail: "Not Found" }, 404);
    }),
  );
  return sent;
}

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ["/linkedin"] });
  render(<App router={router} queryClient={createQueryClient({ retries: 0 })} />);
}

async function item(label: string) {
  const heading = await screen.findByRole("heading", { level: 2, name: label });
  const article = heading.closest("article");
  if (!article) {
    throw new Error(`no section ${label}`);
  }
  return within(article);
}

beforeEach(() => {
  document.cookie = "__Host-wj_csrf=csrf-1; Secure; Path=/";
});

describe("the LinkedIn page", () => {
  it("says how to get a pack before the first one", async () => {
    serve(page({ pack: null, markedAt: null }));
    renderPage();
    expect(await screen.findByText("No LinkedIn text yet.")).toBeInTheDocument();
    expect(screen.getByText("wj linkedin --db")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark done" })).not.toBeInTheDocument();
  });

  it("lists only the changed sections, each with its length against LinkedIn's limit", async () => {
    serve(page());
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "LinkedIn" })).toBeInTheDocument();
    const sections = await screen.findByRole("list", { name: "Sections to paste" });
    expect(
      within(sections)
        .getAllByRole("heading", { level: 2 })
        .map((heading) => heading.textContent),
    ).toEqual(["Headline", "About", NORTHWIND_LABEL]);
    expect(
      screen.getByText(
        "3 sections changed since you marked the pack done on 12 Oct 2026, 09:00. 1 section unchanged, not shown.",
      ),
    ).toBeInTheDocument();

    const headline = await item("Headline");
    expect(headline.getByText(HEADLINE)).toBeInTheDocument();
    expect(headline.getByText(`${String(HEADLINE.length)} of 220 characters`)).toBeInTheDocument();
    expect(headline.getByText("Verifier: passed")).toBeInTheDocument();
    expect((await item("About")).getByText(/of 2,600 characters/)).toBeInTheDocument();

    const flagged = await item(NORTHWIND_LABEL);
    expect(
      flagged.getByText(/Verifier: flagged\. Paste it only if you're sure/),
    ).toBeInTheDocument();
    expect(flagged.getByText(/No fact names Kafka\./)).toBeInTheDocument();
    expect(flagged.getByText(/^Check: LinkedIn shows "Software Engineer"/)).toBeInTheDocument();
    expect(flagged.getByText(/Not marked done with the others/)).toBeInTheDocument();
    expect(flagged.queryByRole("checkbox")).not.toBeInTheDocument();

    expect(
      screen.getByRole("heading", { level: 2, name: "Take off LinkedIn" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: "Position: Tutor at A School" })).not.toBeChecked();
  });

  it("marks a section over LinkedIn's limit", async () => {
    const long = "x".repeat(221);
    serve(page({ pack: pack({ sections: [section("headline", "Headline", long)] }) }));
    renderPage();
    const headline = await item("Headline");
    expect(headline.getByText("221 of 220 characters: over LinkedIn's limit")).toBeInTheDocument();
  });

  it("copies a section and says so, or says how to copy it by hand", async () => {
    serve(page());
    const user = userEvent.setup();
    renderPage();
    const headline = await item("Headline");
    await user.click(headline.getByRole("button", { name: "Copy" }));
    expect(await navigator.clipboard.readText()).toBe(HEADLINE);
    expect(headline.getByRole("status")).toHaveTextContent("Copied");

    const about = await item("About");
    vi.spyOn(navigator.clipboard, "writeText").mockRejectedValueOnce(new Error("denied"));
    await user.click(about.getByRole("button", { name: "Copy" }));
    expect(await about.findByRole("alert")).toHaveTextContent(
      "Couldn't copy it. Select the text above and copy it yourself.",
    );
    expect(about.getByRole("status")).toBeEmptyDOMElement();
  });

  it("marks the pack done once every item is ticked, sending the pack it showed", async () => {
    const sent = serve(page(), afterMarking(), json({ marked: 3, flagged: [NORTHWIND_LABEL] }));
    const user = userEvent.setup();
    renderPage();
    const markDone = await screen.findByRole("button", { name: "Mark done" });
    expect(markDone).toBeDisabled();
    expect(screen.getByText(/3 items left/)).toBeInTheDocument();

    await user.click(
      (await item("Headline")).getByRole("checkbox", { name: "Pasted into LinkedIn" }),
    );
    await user.click((await item("About")).getByRole("checkbox", { name: "Pasted into LinkedIn" }));
    expect(markDone).toBeDisabled();
    expect(screen.getByText(/1 item left/)).toBeInTheDocument();
    await user.click(screen.getByRole("checkbox", { name: "Position: Tutor at A School" }));
    expect(markDone).toBeEnabled();

    await user.click(markDone);
    const status = await screen.findByText(/^Marked done\./);
    expect(status).toHaveTextContent(
      `Marked done. This page now lists only what changes after this. Not marked, as the verifier flagged it: ${NORTHWIND_LABEL}. Written again next time.`,
    );
    expect(status).toHaveFocus();
    expect(sent).toEqual([
      { path: "/api/linkedin/done", body: { digest: DIGEST }, csrf: "csrf-1" },
    ]);
    expect(
      await screen.findByText(/The verifier flagged every section listed/),
    ).toBeInTheDocument();
    expect(screen.queryByRole("heading", { level: 2, name: "Headline" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark done" })).not.toBeInTheDocument();
    expect(
      screen.getByText(
        "1 section changed since you marked the pack done on 30 Oct 2026, 10:30. 3 sections unchanged, not shown.",
      ),
    ).toBeInTheDocument();
  });

  it("says it's marked done when nothing is left to paste", async () => {
    const everything = pack({ sections: [section("headline", "Headline", HEADLINE)], removed: [] });
    const marked = page({
      markedAt: "2026-10-30T02:30:00Z",
      pack: pack({ sections: [], unchanged: 4, removed: [] }),
    });
    serve(page({ pack: everything }), marked, json({ marked: 4, flagged: [] }));
    const user = userEvent.setup();
    renderPage();
    await user.click(
      (await item("Headline")).getByRole("checkbox", { name: "Pasted into LinkedIn" }),
    );
    await user.click(screen.getByRole("button", { name: "Mark done" }));
    expect(
      await screen.findByText("Nothing to update: LinkedIn has everything you last marked done."),
    ).toBeInTheDocument();
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(
      "Marked done. This page now lists only what changes after this.",
    );
    expect(status).toHaveFocus();
    expect(screen.getByText("You last marked the pack done on 30 Oct 2026, 10:30.")).toBeVisible();
  });

  it("says why a pack wasn't marked, and shows the pack written since unticked", async () => {
    const rewritten = page({ pack: pack({ digest: NEW_DIGEST, removed: [] }) });
    const detail = "the pack was written again since the page loaded; check its sections first";
    serve(page(), rewritten, json({ detail }, 409));
    const user = userEvent.setup();
    renderPage();
    await user.click(
      (await item("Headline")).getByRole("checkbox", { name: "Pasted into LinkedIn" }),
    );
    await user.click((await item("About")).getByRole("checkbox", { name: "Pasted into LinkedIn" }));
    await user.click(screen.getByRole("checkbox", { name: "Position: Tutor at A School" }));
    await user.click(screen.getByRole("button", { name: "Mark done" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't mark the pack done: the pack was written again since the page loaded; check its sections first.",
    );
    expect(await screen.findByText(/2 items left/)).toBeInTheDocument();
    expect((await item("Headline")).getByRole("checkbox")).not.toBeChecked();
    expect(screen.getByRole("button", { name: "Mark done" })).toBeDisabled();
  });

  it("says when LinkedIn has everything last marked done", async () => {
    serve(page({ pack: pack({ sections: [], unchanged: 4, removed: [] }) }));
    renderPage();
    expect(
      await screen.findByText("Nothing to update: LinkedIn has everything you last marked done."),
    ).toBeInTheDocument();
    expect(
      screen.getByText("You last marked the pack done on 12 Oct 2026, 09:00."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Mark done" })).not.toBeInTheDocument();
  });

  it("lists every section of the first pack, and says when the profile has moved on", async () => {
    serve(page({ markedAt: null, profileVersion: 3 }));
    renderPage();
    expect(
      await screen.findByText("You haven't marked a pack done yet, so every section is listed."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "Written from version 2 of your profile, which is now at version 3: it may miss your latest changes.",
      ),
    ).toBeInTheDocument();
  });

  it("offers to try again when the page doesn't load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((request: Request) =>
        Promise.resolve(
          new URL(request.url).pathname === "/auth/me" ? json(ME) : json({ detail: "Down" }, 503),
        ),
      ),
    );
    renderPage();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your LinkedIn text didn't load.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
