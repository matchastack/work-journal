import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/queryClient";
import type { MasterResume, Resumes, TailoredResume } from "../api/resumes";
import { App } from "../App";
import { routes } from "../routes";

// Tests run with TZ=Asia/Singapore (vite.config.ts), 8 hours ahead of UTC.
const ME = { id: "4c1ff1e0-8d2b-4c55-9c8e-1f0b3a6d2e71", githubLogin: "casey-example" };
const MASTER_PDF = "a".repeat(64);
const EARLIER_PDF = "b".repeat(64);
const CLEAN_PDF = "c".repeat(64);
const BROKEN_PDF = "d".repeat(64);
const NOTE =
  'education[springfield_state].rules: rule 1 isn\'t checked: "Show the minor after the major."';

function master(fields: Partial<MasterResume> = {}): MasterResume {
  return {
    version: 3,
    status: "updated",
    changedAt: "2026-10-27T01:30:00Z",
    pdf: {
      sha256: MASTER_PDF,
      pages: 3,
      cuts: [],
      warnings: [],
    },
    lint: {
      findings: [
        {
          rule: "R4",
          severity: "warning",
          location: "work[contoso].highlights[ct_sensors]",
          message: "shares the metric 40% with work[northwind].highlights[nw_export]",
        },
      ],
      notes: [NOTE],
    },
    ...fields,
  };
}

function tailored(id: string, fields: Partial<TailoredResume> = {}): TailoredResume {
  return {
    id,
    company: "Fabrikam Logistics",
    position: "Backend Engineer",
    appliedOn: "2026-10-20",
    loggedAt: "2026-10-20T02:00:00Z",
    pdf: { sha256: CLEAN_PDF, pages: 1, cuts: [], warnings: [] },
    rules: { findings: [], notes: [] },
    sendable: true,
    ...fields,
  };
}

function resumes(fields: Partial<Resumes> = {}): Resumes {
  return {
    profileVersion: 3,
    master: master(),
    lastRendered: null,
    tailored: [
      tailored("20261027-contoso-data-engineer-d4e5f6", {
        company: "Contoso",
        position: "Data Engineer",
        appliedOn: "2026-10-27",
        pdf: {
          sha256: BROKEN_PDF,
          pages: 2,
          cuts: [
            { kind: "bullet", id: "ct_datasets", text: "Labelled the sensor datasets." },
            { kind: "project", id: "recipe_box", text: "Recipe Box" },
          ],
          warnings: ["Some text uses a bitmap font, which looks rough and may not extract."],
        },
        rules: {
          findings: [
            {
              rule: "R2",
              severity: "error",
              location: "work[northwind].highlights[nw_export]",
              message: 'has placeholder text: "TODO"',
            },
          ],
          notes: [],
        },
        sendable: false,
      }),
      tailored("20261020-fabrikam-backend-engineer-a1b2c3"),
    ],
    ...fields,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/** Stands in for the API with `answer` for the resumes, or an error status. */
function serveResumes(answer: Resumes | number) {
  const fetch = vi.fn((request: Request) => {
    const path = new URL(request.url).pathname;
    if (path === "/auth/me") {
      return Promise.resolve(json(ME));
    }
    if (path === "/api/resumes") {
      return Promise.resolve(
        typeof answer === "number" ? json({ detail: "Broken" }, answer) : json(answer),
      );
    }
    return Promise.resolve(json({ detail: "Not Found" }, 404));
  });
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

function renderPage() {
  const router = createMemoryRouter(routes, { initialEntries: ["/resumes"] });
  render(<App router={router} queryClient={createQueryClient({ retries: 0 })} />);
}

async function card(name: string | RegExp) {
  const heading = await screen.findByRole("heading", { level: 3, name });
  const article = heading.closest("article");
  if (article === null) {
    throw new Error(`no card for ${String(name)}`);
  }
  return within(article);
}

describe("the Resumes page", () => {
  it("shows the master resume from the latest refresh, with its PDF, pages and checks", async () => {
    serveResumes(resumes());
    renderPage();
    expect(await screen.findByRole("heading", { level: 1, name: "Resumes" })).toBeInTheDocument();
    const masterCard = await card("The master resume");
    expect(masterCard.getByText("Rendered")).toBeInTheDocument();
    expect(masterCard.getByText("3 pages")).toBeInTheDocument();
    expect(masterCard.getByText("Profile version 3, rendered 27 Oct 2026, 09:30.")).toBeVisible();
    expect(masterCard.getByTitle("Preview of the master resume")).toHaveAttribute(
      "src",
      `/api/resumes/pdf/${MASTER_PDF}`,
    );
    expect(masterCard.getByRole("link", { name: "Open the PDF in a new tab" })).toHaveAttribute(
      "href",
      `/api/resumes/pdf/${MASTER_PDF}`,
    );
    const download = masterCard.getByRole("link", {
      name: "Download the PDF of the master resume",
    });
    expect(download).toHaveAttribute("href", `/api/resumes/pdf/${MASTER_PDF}?download=true`);
    expect(download).toHaveAttribute("download");
    expect(
      masterCard.getByText("Nothing was cut: the master document shows everything."),
    ).toBeVisible();
    expect(masterCard.getByRole("heading", { name: "Profile check of version 3" })).toBeVisible();
    expect(
      masterCard.getByText(
        "(R4) work[contoso].highlights[ct_sensors]: shares the metric 40% with work[northwind].highlights[nw_export]",
        { exact: false },
      ),
    ).toBeVisible();
    expect(masterCard.getByText(`Not checked: ${NOTE}`)).toBeVisible();
  });

  it("lists the tailored resumes newest first", async () => {
    serveResumes(resumes());
    renderPage();
    const section = within(await screen.findByRole("region", { name: "Tailored resumes" }));
    expect(section.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Data Engineer at Contoso",
      "Backend Engineer at Fabrikam Logistics",
    ]);
    const clean = await card("Backend Engineer at Fabrikam Logistics");
    expect(clean.getByText("Passes the resume rules")).toBeInTheDocument();
    expect(clean.getByText("1 page")).toBeInTheDocument();
    expect(clean.getByText(/Tailored on 20 Oct 2026/)).toBeVisible();
    expect(clean.getByText("20261020-fabrikam-backend-engineer-a1b2c3")).toBeVisible();
    expect(clean.getByText("Nothing was cut: it fit one page as chosen.")).toBeVisible();
    expect(clean.getByText("No problems found.")).toBeVisible();
    expect(
      clean.getByRole("link", {
        name: "Download the PDF for Backend Engineer at Fabrikam Logistics",
      }),
    ).toHaveAttribute("href", `/api/resumes/pdf/${CLEAN_PDF}?download=true`);
  });

  it("marks a tailored resume that breaks a rule not to send, with its errors and cuts", async () => {
    serveResumes(resumes());
    renderPage();
    const broken = await card("Data Engineer at Contoso");
    expect(broken.getByText("Don't send")).toBeInTheDocument();
    expect(broken.getByText(/It breaks a resume rule/)).toHaveTextContent(
      "It breaks a resume rule: don't send it until the errors under Resume rules are fixed.",
    );
    expect(broken.getByText("2 pages: over the one-page limit")).toHaveClass("text-red-900");
    expect(broken.getByText("Error")).toHaveClass("text-red-800");
    expect(
      broken.getByText('(R2) work[northwind].highlights[nw_export]: has placeholder text: "TODO"', {
        exact: false,
      }),
    ).toBeVisible();
    expect(broken.getByText("2 items cut to fit, lowest value first:")).toBeVisible();
    expect(broken.getAllByRole("listitem").map((item) => item.textContent)).toEqual(
      expect.arrayContaining(["Bullet: Labelled the sensor datasets.", "Project: Recipe Box"]),
    );
    expect(
      broken.getByText(
        "Warning: Some text uses a bitmap font, which looks rough and may not extract.",
      ),
    ).toBeVisible();
  });

  it("loads a tailored resume's preview only when asked", async () => {
    serveResumes(resumes());
    renderPage();
    const clean = await card("Backend Engineer at Fabrikam Logistics");
    const toggle = clean.getByRole("button", { name: "Show preview" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(clean.queryByTitle(/Preview/)).not.toBeInTheDocument();
    await userEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle).toHaveTextContent("Hide preview");
    expect(
      clean.getByTitle("Preview of the resume for Backend Engineer at Fabrikam Logistics"),
    ).toHaveAttribute("src", `/api/resumes/pdf/${CLEAN_PDF}`);
    await userEvent.click(toggle);
    expect(clean.queryByTitle(/Preview/)).not.toBeInTheDocument();
  });

  it("says which command renders a master resume that waits, and keeps the last one to hand", async () => {
    serveResumes(
      resumes({
        profileVersion: 4,
        master: master({ version: 4, status: "pending", pdf: null }),
        lastRendered: master({ pdf: { sha256: EARLIER_PDF, pages: 2, cuts: null, warnings: [] } }),
      }),
    );
    renderPage();
    const waiting = await card("The master resume");
    expect(waiting.getByText("Waiting to be rendered")).toBeInTheDocument();
    expect(waiting.getByText(/Version 4 waits to be rendered/)).toHaveTextContent(
      "run uv run wj tick on your computer, where TeX Live is installed",
    );
    expect(waiting.queryByRole("link", { name: /Download/ })).not.toBeInTheDocument();
    expect(waiting.queryByTitle(/Preview/)).not.toBeInTheDocument();
    const last = await card("The last one rendered: version 3");
    expect(last.getByTitle("Preview of the last one rendered: version 3")).toHaveAttribute(
      "src",
      `/api/resumes/pdf/${EARLIER_PDF}`,
    );
    expect(
      last.getByText("Not recorded: this PDF was made before cut reports were kept."),
    ).toBeVisible();
  });

  it("says when the master resume couldn't be rendered, or is behind the profile", async () => {
    serveResumes(
      resumes({ profileVersion: 5, master: master({ status: "failed", pdf: null, lint: null }) }),
    );
    renderPage();
    const failed = await card("The master resume");
    expect(failed.getByText("Couldn't be rendered")).toBeInTheDocument();
    expect(failed.getByText(/Version 3 couldn't be rendered/)).toHaveTextContent(
      "Run uv run wj render --db on your computer to see why",
    );
    expect(failed.getByText("Not checked: this profile version is gone.")).toBeVisible();
    expect(screen.getByText(/Your profile is at version 5, newer than this resume/)).toBeVisible();
  });

  it("says what wasn't recorded for an older tailored resume", async () => {
    serveResumes(
      resumes({
        tailored: [
          tailored("20261001-old-aaaaaa", {
            pdf: { sha256: CLEAN_PDF, pages: null, cuts: null, warnings: [] },
            rules: null,
            sendable: false,
          }),
          tailored("20261002-no-pdf-bbbbbb", { position: "Platform Engineer", pdf: null }),
        ],
      }),
    );
    renderPage();
    const old = await card("Backend Engineer at Fabrikam Logistics");
    expect(old.getByText("Rules not checked")).toBeInTheDocument();
    expect(old.queryByText("Don't send")).not.toBeInTheDocument();
    expect(old.getByText("Page count not recorded")).toBeInTheDocument();
    expect(
      old.getByText("Not checked: the rule findings weren't kept with this resume."),
    ).toBeVisible();
    const noPdf = await card("Platform Engineer at Fabrikam Logistics");
    expect(noPdf.getByText("No PDF was kept for this resume.")).toBeVisible();
    expect(noPdf.queryByRole("button", { name: "Show preview" })).not.toBeInTheDocument();
  });

  it("explains how to make resumes when there are none", async () => {
    serveResumes(resumes({ profileVersion: 1, master: null, tailored: [] }));
    renderPage();
    expect(await screen.findByText("No master resume yet.")).toBeVisible();
    expect(screen.getByText(/Send \/refresh to the bot and apply its changes/)).toHaveTextContent(
      "Then run uv run wj tick on your computer",
    );
    expect(screen.getByText("No tailored resumes yet.")).toBeVisible();
    expect(screen.getByText(/each resume it makes appears here/)).toHaveTextContent(
      "Run uv run wj tailor <posting-file> --db on your computer",
    );
  });

  it("offers to try again when the resumes don't load", async () => {
    const fetch = serveResumes(500);
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Your resumes didn't load.");
    fetch.mockClear();
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(fetch.mock.calls.map(([request]) => new URL(request.url).pathname)).toContain(
      "/api/resumes",
    );
  });

  it("writes only ASCII characters", async () => {
    serveResumes(resumes({ lastRendered: master({ version: 2 }) }));
    renderPage();
    await card("The master resume");
    const nonAscii = document.documentElement.outerHTML.match(/[^\t\n\r\x20-\x7e]/gu) ?? [];
    expect(nonAscii).toEqual([]);
  });
});
