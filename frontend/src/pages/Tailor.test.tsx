import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VerifierReport } from "../api/inbox";
import { createQueryClient } from "../api/queryClient";
import type { TailoredResume } from "../api/resumes";
import { localDate, pollingInterval, type TailorDraft, type TailorPage } from "../api/tailor";
import { App } from "../App";
import { routes } from "../routes";

const ME = { id: "4c1ff1e0-8d2b-4c55-9c8e-1f0b3a6d2e71", githubLogin: "casey-example" };
const DRAFT = "7d1c1a9e-2b9c-4c43-9a55-2f0e8c1d3b4a";
const PDF = "e".repeat(64);
const EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes.";
const REPHRASED = "Cut the nightly export job from 50 to 12 minutes by batching PostgreSQL writes.";
const POSTING = {
  title: "Senior Backend Engineer, Payments",
  company: "Fabrikam Logistics",
  seniority: "Senior",
  mustHave: ["Python", "Go", "PostgreSQL"],
  niceToHave: ["payments"],
  responsibilities: ["Design and build APIs for invoicing and carrier payouts"],
  keyTerms: ["APIs", "observability"],
  url: null,
};

function report(text: string): VerifierReport {
  return {
    text,
    passed: true,
    attempts: 1,
    factIds: ["fact_nw_export"],
    findings: [],
    claims: [],
    derivations: [],
    claimPrompt: null,
  };
}

function draft(fields: Partial<TailorDraft> = {}): TailorDraft {
  return {
    id: DRAFT,
    createdAt: "2026-10-31T01:00:00Z",
    status: "ready",
    error: null,
    posting: POSTING,
    droppedTerms: ["Rust"],
    profileVersion: 1,
    roleType: "backend",
    notes: ["nw_tracker has none of its facts here, so it keeps its own words"],
    bullets: [
      {
        id: "nw_export",
        under: "Northwind Traders",
        original: EXPORT,
        use: "own",
        swap: null,
        phrasing: report(REPHRASED),
        approved: false,
      },
    ],
    skills: [{ name: "Kotlin", decision: null, locations: ["skills[Languages]"] }],
    outline: {
      summary: null,
      work: [
        {
          id: "northwind",
          name: "Northwind Traders",
          title: "Software Engineer",
          bullets: [{ id: "nw_export", text: EXPORT }],
        },
      ],
      projects: [],
      skills: [{ label: "Languages", skills: ["Python", "Kotlin"] }],
    },
    rules: {
      findings: [
        {
          rule: "R3",
          severity: "error",
          location: "skills[Languages]",
          message: "Kotlin is marked verify-before-shipping and isn't confirmed for this resume",
        },
      ],
      notes: [],
    },
    coverage: {
      covered: [{ term: "Python", kind: "must_have", locations: ["skills[Languages]"] }],
      missing: [{ term: "Go", kind: "must_have", locations: [] }],
    },
    coverageFitted: false,
    render: { status: "waiting", pdf: null, ats: null, error: null },
    blocked: [
      "Confirm or drop each skill marked verify-before-shipping: Kotlin.",
      "The PDF isn't rendered yet.",
    ],
    application: null,
    ...fields,
  };
}

const RENDERED: Partial<TailorDraft> = {
  skills: [{ name: "Kotlin", decision: "confirmed", locations: ["skills[Languages]"] }],
  rules: { findings: [], notes: [] },
  coverageFitted: true,
  render: {
    status: "rendered",
    pdf: { sha256: PDF, pages: 1, cuts: [], warnings: [] },
    ats: { findings: [], terms: 1, sections: ["Education", "Work Experience"] },
    error: null,
  },
  blocked: [],
};

function application(fields: Partial<TailoredResume> = {}): TailoredResume {
  return {
    id: "20261020-contoso-data-engineer-a1b2c3",
    company: "Contoso",
    position: "Data Engineer",
    appliedOn: "2026-10-20",
    loggedAt: "2026-10-20T02:00:00Z",
    pdf: { sha256: "c".repeat(64), pages: 1, cuts: [], warnings: [] },
    rules: { findings: [], notes: [] },
    sendable: true,
    ...fields,
  };
}

function page(fields: Partial<TailorPage> = {}): TailorPage {
  return {
    drafts: [
      {
        id: DRAFT,
        createdAt: "2026-10-31T01:00:00Z",
        status: "ready",
        title: POSTING.title,
        company: POSTING.company,
        renderStatus: "waiting",
      },
    ],
    applications: [application()],
    ...fields,
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

/** Stands in for the API: the page, the draft, and an answer for each change, by
 * `"METHOD /path"`. */
function serve(
  shown: TailorDraft,
  answers: Record<string, TailorDraft | Response> = {},
  list: TailorPage = page(),
) {
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const body: unknown = request.body === null ? undefined : await request.json();
      if (path === "/auth/me") {
        return json(ME);
      }
      if (path === "/api/tailor" && request.method === "GET") {
        return json(list);
      }
      if (path === `/api/tailor/${DRAFT}` && request.method === "GET") {
        return json(shown);
      }
      sent.push({
        method: request.method,
        path,
        body,
        csrf: request.headers.get("X-CSRF-Token"),
      });
      const answer = answers[`${request.method} ${path}`];
      if (answer === undefined) {
        return json({ detail: "Not Found" }, 404);
      }
      return answer instanceof Response
        ? answer
        : json(answer, request.method === "POST" && path === "/api/tailor" ? 201 : 200);
    }),
  );
  return sent;
}

function renderPage(entry = `/tailor?draft=${DRAFT}`) {
  const router = createMemoryRouter(routes, { initialEntries: [entry] });
  render(<App router={router} queryClient={createQueryClient({ retries: 0 })} />);
  return router;
}

async function section(name: string) {
  const heading = await screen.findByRole("heading", { level: 3, name });
  const found = heading.closest("section");
  if (found === null) {
    throw new Error(`no section ${name}`);
  }
  return within(found);
}

beforeEach(() => {
  document.cookie = "__Host-wj_csrf=csrf-1; Secure; Path=/";
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the Tailor page", () => {
  it("lists the drafts and the applications, with each PDF", async () => {
    serve(
      draft(),
      {},
      page({
        applications: [
          application(),
          application({
            id: "20261001-x-y-z1z2z3",
            company: "Adatum",
            sendable: false,
            rules: {
              findings: [{ rule: "R2", severity: "error", location: "x", message: "TODO" }],
              notes: [],
            },
          }),
        ],
      }),
    );
    renderPage("/tailor");
    const drafts = within(await screen.findByRole("region", { name: "Drafts" }));
    expect(
      drafts.getByRole("link", { name: `${POSTING.title} at ${POSTING.company}` }),
    ).toHaveAttribute("href", `/tailor?draft=${DRAFT}`);
    expect(drafts.getByText("Waiting for its PDF")).toBeInTheDocument();
    const history = await screen.findByRole("region", { name: "Applications" });
    const rows = within(history).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Data Engineer at Contoso");
    expect(rows[0]).toHaveTextContent("20 Oct 2026");
    expect(
      within(history).getByRole("link", { name: "Download the PDF for Data Engineer at Contoso" }),
    ).toHaveAttribute("href", `/api/resumes/pdf/${"c".repeat(64)}?download=true`);
    expect(rows[1]).toHaveTextContent("Don't send");
    expect(rows[0]).not.toHaveTextContent("Don't send");
  });

  it("sends a pasted posting and opens its draft while it's read", async () => {
    const parsing = draft({
      status: "parsing",
      posting: null,
      bullets: [],
      skills: [],
      outline: null,
      rules: null,
      coverage: null,
      render: null,
      blocked: ["It isn't tailored yet."],
    });
    const sent = serve(parsing, { "POST /api/tailor": parsing }, page({ drafts: [] }));
    const router = renderPage("/tailor");
    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("Job posting"), "We need a backend engineer.");
    await user.click(screen.getByRole("button", { name: "Tailor a resume" }));
    expect(await screen.findByText("Reading the posting...")).toBeInTheDocument();
    expect(sent[0]).toEqual({
      method: "POST",
      path: "/api/tailor",
      body: { text: "We need a backend engineer." },
      csrf: "csrf-1",
    });
    expect(router.state.location.search).toBe(`?draft=${DRAFT}`);
  });

  it("shows a tailored draft: the requirements, the wording, the skills and the checks", async () => {
    serve(draft());
    renderPage();
    expect(
      await screen.findByRole("heading", {
        level: 2,
        name: `${POSTING.title} at ${POSTING.company}`,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Tailored from version 1 of your profile, as a backend resume."),
    ).toBeInTheDocument();
    const posting = await section("The posting's requirements");
    expect(posting.getByText("Python, Go, PostgreSQL")).toBeInTheDocument();
    expect(
      posting.getByText(/Left out, since the posting doesn't contain them: Rust/),
    ).toBeInTheDocument();
    const wording = await section("Wording");
    const own = wording.getByRole("radio", { name: /Its own words/ });
    expect(own).toBeChecked();
    expect(wording.getByRole("radio", { name: /approve it to use it/ })).toBeDisabled();
    expect(wording.getByText("Verifier: passed")).toBeInTheDocument();
    const skills = await section("Skills to confirm");
    expect(skills.getByText("Used in skills[Languages].")).toBeInTheDocument();
    expect(
      screen.getByText(/Kotlin is marked verify-before-shipping and isn't confirmed/),
    ).toBeInTheDocument();
    const coverage = await section("Keyword coverage");
    expect(
      coverage.getByText(
        /Covers 1 of the posting's 2 skills and key terms, before the resume is fitted/,
      ),
    ).toBeInTheDocument();
    expect(coverage.getByText("Gaps, which are never added: Go")).toBeInTheDocument();
    const pdf = await section("PDF");
    expect(pdf.getByText("Waiting for a computer with TeX Live to render it.")).toBeInTheDocument();
    expect(pdf.getByText("uv run wj worker")).toBeInTheDocument();
    expect(pdf.getByText("uv run wj tick")).toBeInTheDocument();
    const download = await section("Download");
    expect(
      download.getByRole("button", { name: "Download PDF and log the application" }),
    ).toBeDisabled();
    expect(download.getByText("The PDF isn't rendered yet.")).toBeInTheDocument();
    expect(download.queryByText(/don't send it/)).not.toBeInTheDocument();
  });

  it("approves a phrasing, chooses words and decides a skill", async () => {
    const approved = draft({
      bullets: draft().bullets.map((bullet) => ({ ...bullet, use: "phrasing", approved: true })),
    });
    const sent = serve(draft(), {
      [`POST /api/tailor/${DRAFT}/bullets/nw_export/approve`]: approved,
      [`PUT /api/tailor/${DRAFT}/bullets/nw_export`]: draft(),
      [`PUT /api/tailor/${DRAFT}/skills`]: draft(RENDERED),
    });
    renderPage();
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", {
        name: "Approve the new phrasing of nw_export and save it as a swap",
      }),
    );
    const phrasing = await screen.findByRole("radio", { name: /approved and saved as a swap/ });
    expect(phrasing).toBeChecked();
    await user.click(screen.getByRole("radio", { name: /Its own words/ }));
    await user.click(await screen.findByRole("button", { name: /Confirm Kotlin/ }));
    expect(await screen.findByText("Confirmed")).toBeInTheDocument();
    expect(sent.map(({ method, path, body }) => ({ method, path, body }))).toEqual([
      { method: "POST", path: `/api/tailor/${DRAFT}/bullets/nw_export/approve`, body: undefined },
      { method: "PUT", path: `/api/tailor/${DRAFT}/bullets/nw_export`, body: { use: "own" } },
      {
        method: "PUT",
        path: `/api/tailor/${DRAFT}/skills`,
        body: { skill: "Kotlin", decision: "confirmed" },
      },
    ]);
    expect(sent.every((request) => request.csrf === "csrf-1")).toBe(true);
  });

  it("previews the rendered PDF, then downloads it and logs the application", async () => {
    const logged = draft({
      ...RENDERED,
      application: {
        id: "20261031-fabrikam-logistics-a1b2c3",
        appliedOn: "2026-10-31",
        pdfSha256: PDF,
        sendable: true,
      },
    });
    const sent = serve(draft(RENDERED), { [`POST /api/tailor/${DRAFT}/application`]: logged });
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    renderPage();
    const pdf = await section("PDF");
    expect(pdf.getByText("1 page")).toBeInTheDocument();
    expect(pdf.getByTitle(`Preview of the resume for ${POSTING.title}`)).toHaveAttribute(
      "src",
      `/api/tailor/${DRAFT}/pdf?v=${PDF}`,
    );
    expect(pdf.getByText(/Passed: the PDF's text has its 1 covered terms/)).toBeInTheDocument();
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "Download PDF and log the application" }));
    expect(await screen.findByText(/Logged as application/)).toHaveTextContent("on 31 Oct 2026.");
    expect(screen.getByRole("radio", { name: /Its own words/ })).toBeDisabled();
    expect(screen.queryByRole("button", { name: /Undo the decision/ })).not.toBeInTheDocument();
    expect(sent).toEqual([
      {
        method: "POST",
        path: `/api/tailor/${DRAFT}/application`,
        body: { appliedOn: localDate() },
        csrf: "csrf-1",
      },
    ]);
    expect(click).toHaveBeenCalledTimes(1);
    expect(click.mock.contexts[0]).toHaveProperty(
      "href",
      `https://journal.test/api/resumes/pdf/${PDF}?download=true`,
    );
    expect(screen.getByRole("link", { name: "Download PDF" })).toHaveAttribute(
      "href",
      `/api/resumes/pdf/${PDF}?download=true`,
    );
  });

  it("lets a resume that breaks a rule be downloaded, marked not to send", async () => {
    const breaking = draft({
      ...RENDERED,
      rules: {
        findings: [
          {
            rule: "R3",
            severity: "error",
            location: "work[northwind].highlights[nw_export]",
            message: "Kotlin is marked verify-before-shipping and isn't confirmed for this resume",
          },
        ],
        notes: [],
      },
    });
    serve(breaking);
    renderPage();
    const download = await section("Download");
    expect(download.getByText(/you can download it, but don't send it/)).toBeInTheDocument();
    expect(
      download.getByRole("button", { name: "Download PDF and log the application" }),
    ).toBeEnabled();
  });

  it("says why a draft failed and tries it again", async () => {
    const failed = draft({
      status: "failed",
      error: "The resume couldn't be tailored because the model declined it.",
      bullets: [],
      skills: [],
      outline: null,
      rules: null,
      coverage: null,
      render: null,
      blocked: ["Tailoring failed."],
    });
    const sent = serve(failed, {
      [`POST /api/tailor/${DRAFT}/retry`]: draft({
        status: "tailoring",
        rules: null,
        render: null,
      }),
    });
    renderPage();
    expect(
      await screen.findByText("The resume couldn't be tailored because the model declined it."),
    ).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText(/Choosing what your resume shows/)).toBeInTheDocument();
    expect(sent.map(({ method, path }) => `${method} ${path}`)).toEqual([
      `POST /api/tailor/${DRAFT}/retry`,
    ]);
  });

  it("shows the API's reason when a change is refused", async () => {
    serve(draft(), {
      [`PUT /api/tailor/${DRAFT}/skills`]: json({ detail: "It isn't tailored yet" }, 409),
    });
    renderPage();
    await userEvent.setup().click(await screen.findByRole("button", { name: /Drop Kotlin/ }));
    expect(await screen.findByText("Couldn't decide: It isn't tailored yet.")).toBeInTheDocument();
  });
});

describe("reading a draft again", () => {
  it("happens often while a job works on it, and stops when nothing does", () => {
    expect(pollingInterval(draft({ status: "parsing" }))).toBe(2000);
    expect(pollingInterval(draft({ status: "tailoring" }))).toBe(2000);
    expect(pollingInterval(draft())).toBe(5000);
    expect(pollingInterval(draft(RENDERED))).toBe(false);
    expect(pollingInterval(draft({ status: "failed" }))).toBe(false);
    expect(pollingInterval(undefined)).toBe(false);
  });

  it("uses the browser's own date for the application", () => {
    expect(localDate(new Date(2026, 9, 31, 23, 30))).toBe("2026-10-31");
  });
});
