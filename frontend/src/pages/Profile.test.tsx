import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  BasicsEdit,
  Bullet,
  BulletEdit,
  CourseworkEdit,
  OpenQuestionEdit,
  RoleRulesEdit,
  SkillsEdit,
  SwapsEdit,
  Profile,
  ProfilePage,
  RoleEdit,
  VersionChange,
  VersionSummary,
} from "../api/profile";
import { createQueryClient } from "../api/queryClient";
import { App } from "../App";
import { routes } from "../routes";

// Tests run with TZ=Asia/Singapore (vite.config.ts), 8 hours ahead of UTC.
const ME = { id: "4c1ff1e0-8d2b-4c55-9c8e-1f0b3a6d2e71", githubLogin: "casey-example" };
const EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes.";
const TRACKER = "Built a tracker for the support team's requests.";
const SEARCH = "Added search across TODO recipes.";

function bullet(id: string, text: string, fields: Partial<Bullet> = {}): Bullet {
  return {
    id,
    text,
    strength: "medium",
    strengthOverrides: [],
    verification: "no",
    verificationNote: null,
    swaps: [],
    needs: [],
    notes: [],
    status: "active",
    statusReason: null,
    tags: [],
    priority: 2,
    visibility: "public",
    sources: [],
    ...fields,
  };
}

function profile(): Profile {
  return {
    basics: {
      name: "Casey Morgan",
      label: "Software Engineer",
      email: "casey@example.com",
      emailVisibility: "public",
      phone: "+1 555 0100",
      phoneVisibility: "resume_only",
      url: "https://example.com",
      location: { city: "Springfield", region: null, countryCode: "US" },
      locationVisibility: "public",
      profiles: [
        { network: "GitHub", username: "casey-example", url: "https://github.com/casey-example" },
      ],
    },
    roleTypes: [
      { id: "backend", name: "Backend / full stack" },
      { id: "data", name: "Data engineering" },
    ],
    work: [
      {
        id: "northwind",
        name: "Northwind Traders",
        position: "Engineer",
        titleVariants: [],
        location: "Springfield",
        url: null,
        startDate: "2025-03",
        endDate: null,
        summary: null,
        highlights: [
          bullet("nw_export", EXPORT, {
            strength: "high",
            verification: "partial",
            verificationNote: "From the job logs",
            tags: ["backend", "data"],
          }),
          bullet("nw_tracker", TRACKER, {
            status: "benched",
            statusReason: "Replaced by the export work",
          }),
        ],
        loadBearing: true,
        duringEducation: false,
        keepFor: [],
        cutFor: [],
        sensitivity: "normal",
        honestyBoundaries: [],
        priority: 1,
        notes: [],
      },
    ],
    education: [
      {
        id: "springfield_state",
        institution: "Springfield State University",
        studyType: "Bachelor of Science",
        area: "Computer Science",
        honours: "Cum laude",
        location: null,
        url: null,
        startDate: "2021-09",
        endDate: "2025-01",
        courses: [],
        courseworkSubsets: [],
        highlights: [],
        rules: [],
      },
    ],
    projects: [
      {
        id: "recipe_box",
        name: "Recipe Box",
        description: "A recipe manager for a family kitchen.",
        keywords: ["TypeScript", "React"],
        url: null,
        startDate: null,
        endDate: null,
        highlights: [bullet("rb_search", SEARCH)],
        status: "active",
        statusReason: null,
        keepFor: [],
        cutFor: [],
        tags: [],
        priority: 2,
        notes: [],
      },
      {
        id: "bird_calls",
        name: "Bird Call Classifier",
        keywords: ["Python"],
        highlights: [
          bullet("bc_model", "Train a classifier on bird calls.", { status: "planned" }),
        ],
        status: "planned",
        statusReason: "After the course",
        keepFor: [],
        cutFor: [],
        tags: ["ml"],
        priority: 3,
        notes: [],
      },
    ],
    skills: [],
    skillGaps: [],
    skillPresets: [],
    summaries: [],
    openQuestions: [],
  };
}

function version(number: number, fields: Partial<VersionSummary> = {}): VersionSummary {
  return {
    number,
    parent: number === 1 ? null : number - 1,
    author: "owner",
    // 1 October 2026, 10:00 in Singapore, a day later for each version.
    createdAt: new Date(Date.UTC(2026, 9, number, 2)).toISOString(),
    restoredFrom: null,
    summary: null,
    ...fields,
  };
}

function page(start: Profile = profile()): ProfilePage {
  return {
    version: version(1),
    profile: start,
    lint: {
      findings: [
        {
          rule: "R2",
          severity: "error",
          location: "projects[recipe_box].highlights[rb_search]",
          message: "has a placeholder: TODO",
          where: "A bullet under Recipe Box",
        },
        {
          rule: "R2",
          severity: "warning",
          location: "projects[recipe_box]",
          message: "has no dates",
          where: "Recipe Box",
        },
      ],
      notes: [],
    },
  };
}

/** A version as the stand-in API keeps it: what the history lists, its profile, and what it
 * changed against its parent. */
interface Stored {
  version: VersionSummary;
  profile: Profile;
  changes: VersionChange[];
}

/** Version 3 of a profile: the profile as first loaded, a refresh's changes, and an edit. */
function history(): { start: ProfilePage; versions: Stored[] } {
  const latest = profile();
  const first = structuredClone(latest);
  first.projects = first.projects.filter((project) => project.id !== "bird_calls");
  const exported = first.work[0]?.highlights[0];
  if (exported !== undefined) {
    exported.text = "Cut the export job from 50 to 12 minutes overall.";
    exported.tags = ["backend"];
  }
  const second = structuredClone(first);
  second.projects = latest.projects;
  const versions: Stored[] = [
    { version: version(1), profile: first, changes: [] },
    {
      version: version(2, { author: "ai", summary: "From the week's journal" }),
      profile: second,
      changes: [
        {
          kind: "added",
          path: "projects[bird_calls]",
          title: "Project added: Bird Call Classifier",
          context: null,
          before: null,
          after: "Bird Call Classifier",
          diff: [],
        },
      ],
    },
    {
      version: version(3, { summary: "Edited a bullet under Engineer, Northwind Traders" }),
      profile: latest,
      changes: [
        {
          kind: "changed",
          path: "work[northwind].highlights[nw_export].text",
          title: "Text of a bullet under Engineer, Northwind Traders",
          context: null,
          before: "Cut the export job from 50 to 12 minutes overall.",
          after: EXPORT,
          diff: [
            { kind: "same", text: "Cut the " },
            { kind: "added", text: "nightly " },
            { kind: "same", text: "export job from 50 to 12 minutes " },
            { kind: "removed", text: "overall." },
            { kind: "added", text: "by batching database writes." },
          ],
        },
        {
          kind: "changed",
          path: "work[northwind].highlights[nw_export].tags",
          title: "Tags of a bullet under Engineer, Northwind Traders",
          context: EXPORT,
          before: "backend",
          after: "backend, data",
          diff: [],
        },
      ],
    },
  ];
  return { start: { ...page(latest), version: versions[2]?.version ?? null }, versions };
}

interface Sent {
  method: string;
  path: string;
  body: unknown;
  csrf: string | null;
}

interface Edit {
  baseVersion: number;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Stands in for the API with a profile that each save changes as a new version, so the page
 * reads it again as changed, and restores make a copy of an earlier one. `elsewhere` changes it
 * as another tab would, without the page knowing; `refuse` answers one request
 * (`"GET /api/profile"`) with an error. `versions` are the versions up to `start`'s.
 */
function serveProfile(
  start: ProfilePage = page(),
  refuse: Record<string, Response> = {},
  versions: Stored[] = start.version && start.profile
    ? [{ version: start.version, profile: start.profile, changes: [] }]
    : [],
) {
  let state = structuredClone(start);
  const stored = structuredClone(versions);
  const sent: Sent[] = [];
  const next = (profile: Profile, fields: Partial<VersionSummary>) => {
    const number = stored.length + 1;
    const saved = version(number, fields);
    stored.push({ version: saved, profile, changes: [] });
    state = { ...state, profile, version: saved };
  };
  const save = (change: (profile: Profile) => void, summary: string) => {
    const current = state.profile;
    if (current === null) {
      throw new Error("no profile to save");
    }
    const profile = structuredClone(current);
    change(profile);
    if (JSON.stringify(profile) !== JSON.stringify(current)) {
      next(profile, { summary });
    }
  };
  const stale = (baseVersion: number) =>
    baseVersion === state.version?.number
      ? null
      : json(
          {
            detail: `your profile changed since this page loaded: it's now at version ${String(state.version?.number)}, so check it and try again`,
          },
          409,
        );
  const bulletOf = (profile: Profile, id: string) => {
    const items = [...profile.work, ...profile.education, ...profile.projects];
    const found = items.flatMap((item) => item.highlights).find((each) => each.id === id);
    if (found === undefined) {
      throw new Error(`no bullet ${id}`);
    }
    return found;
  };
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
      if (path === "/auth/me") {
        return json(ME);
      }
      if (path === "/api/profile") {
        return json(state);
      }
      if (path === "/api/profile/versions") {
        return json({ versions: stored.map((each) => each.version).reverse() });
      }
      const [, , , kind, id = "", action] = path.split("/");
      const number = Number(id);
      const found = stored.find((each) => each.version.number === number);
      if (kind === "versions" && found !== undefined && request.method === "GET") {
        return json({ version: found.version, changes: found.changes });
      }
      const refusal = request.method === "GET" ? null : stale((body as Edit).baseVersion);
      if (refusal !== null) {
        return refusal;
      }
      if (kind === "versions" && found !== undefined && action === "restore") {
        if (number === state.version?.number) {
          return json({ detail: `version ${id} is already your current profile` }, 409);
        }
        next(structuredClone(found.profile), { restoredFrom: number });
        return json(state);
      }
      // An edit's fields, which the stand-in API sets as they are.
      const fields = Object.fromEntries(
        Object.entries(body ?? {}).filter(([key]) => key !== "baseVersion"),
      );
      if (request.method === "PUT" && kind === "bullets") {
        save((profile) => {
          Object.assign(bulletOf(profile, id), fields);
        }, "Edited a bullet");
        return json(state);
      }
      if (request.method === "PUT" && kind === "basics") {
        save((profile) => {
          Object.assign(profile.basics, fields);
        }, "Edited your basics");
        return json(state);
      }
      if (request.method === "PUT" && kind === "work") {
        save((profile) => {
          Object.assign(profile.work.find((role) => role.id === id) ?? {}, fields);
        }, "Edited a role");
        return json(state);
      }
      if (request.method === "PUT" && kind === "education" && action === "coursework") {
        save((profile) => {
          Object.assign(profile.education.find((entry) => entry.id === id) ?? {}, fields);
        }, "Edited the coursework");
        return json(state);
      }
      const lists: Record<string, string> = {
        "role-types": "Edited your role types",
        skills: "Edited your skills",
        "skill-presets": "Edited your skills presets",
        summaries: "Edited your summaries",
      };
      if (request.method === "PUT" && kind !== undefined && kind in lists) {
        save((profile) => {
          Object.assign(profile, fields);
          // A skill keeps the fields a profile has, without the name it had before.
          profile.skills = profile.skills.map((skill) => ({
            name: skill.name,
            category: skill.category,
            tier: skill.tier ?? null,
            verifyBeforeShipping: skill.verifyBeforeShipping,
            aliases: skill.aliases,
            notes: [],
          }));
        }, lists[kind] ?? "");
        return json(state);
      }
      if (kind === "open-questions") {
        const edit = body as OpenQuestionEdit;
        save((profile) => {
          const questionId = request.method === "POST" ? "q_1" : id;
          const others = profile.openQuestions.filter((question) => question.id !== questionId);
          profile.openQuestions = [
            ...others,
            { id: questionId, question: edit.question, status: edit.status, answerFactIds: [] },
          ];
          const items = [...profile.work, ...profile.education, ...profile.projects];
          for (const each of items.flatMap((item) => item.highlights)) {
            const needs = each.needs.filter((need) => need !== questionId);
            each.needs = edit.bulletIds.includes(each.id) ? [...needs, questionId] : needs;
          }
        }, "Edited an open question");
        return json(state);
      }
      return json({ detail: "Not Found" }, 404);
    }),
  );
  return {
    sent,
    elsewhere: (change: (profile: Profile) => void) => {
      save(change, "Edited elsewhere");
    },
  };
}

function renderAt(path = "/profile") {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<App router={router} queryClient={createQueryClient({ retries: 0 })} />);
}

/** The card of a role, education entry or project, by its heading. */
async function card(title: string) {
  const heading = await screen.findByRole("heading", { level: 3, name: title });
  const article = heading.closest("article");
  if (article === null) {
    throw new Error(`no card titled ${title}`);
  }
  return within(article);
}

/** A version in the history, by its number. */
async function versionRow(number: number) {
  const name = new RegExp(`^Version ${String(number)}( \\(current\\))?$`);
  const heading = await screen.findByRole("heading", { level: 3, name });
  const item = heading.closest("li");
  if (item === null) {
    throw new Error(`no version ${String(number)}`);
  }
  return within(item);
}

/** A part of the profile with its own Edit, such as Skills and gaps, by its heading. */
async function block(title: string, level: 3 | 4 = 3) {
  const heading = await screen.findByRole("heading", { level, name: title });
  const section = heading.closest("section");
  if (section === null) {
    throw new Error(`no part titled ${title}`);
  }
  return within(section);
}

async function bulletItem(text: string) {
  const item = (await screen.findByText(text)).closest("li");
  if (item === null) {
    throw new Error(`no bullet ${text}`);
  }
  return within(item);
}

beforeEach(() => {
  document.cookie = "__Host-wj_csrf=csrf-1; Secure; Path=/";
});

describe("the profile page", () => {
  it("shows the latest version by section, with the bullets and the resume rules", async () => {
    serveProfile();
    renderAt();
    expect(await screen.findByRole("heading", { level: 1, name: "Profile" })).toBeInTheDocument();
    const sections = (await screen.findAllByRole("heading", { level: 2 })).map(
      (heading) => heading.textContent,
    );
    expect(sections).toEqual([
      "Basics",
      "Work",
      "Education",
      "Projects",
      "Role types",
      "Skills",
      "Skills presets",
      "Summaries",
      "Open questions",
      "Resume rules",
      "History",
    ]);
    const nav = screen.getByRole("navigation", { name: "Sections" });
    expect(within(nav).getByRole("link", { name: "Resume rules" })).toHaveAttribute(
      "href",
      "#profile-rules",
    );
    expect(
      screen.getByText("Showing version 1, saved by you on 1 Oct 2026, 10:00."),
    ).toBeInTheDocument();

    const basics = await card("Casey Morgan");
    expect(basics.getByText("Software Engineer")).toBeInTheDocument();
    expect(basics.getByText("Springfield, US")).toBeInTheDocument();
    expect(basics.getByText("GitHub: https://github.com/casey-example")).toBeInTheDocument();

    const northwind = await card("Engineer, Northwind Traders");
    expect(northwind.getByText("Mar 2025 - now")).toBeInTheDocument();
    const exported = await bulletItem(EXPORT);
    expect(exported.getByText("Strength: High")).toBeInTheDocument();
    expect(exported.getByText("Partly verified: From the job logs")).toBeInTheDocument();
    expect(exported.getByText("Tags: backend, data")).toBeInTheDocument();
    const tracker = await bulletItem(TRACKER);
    expect(tracker.getByText("Benched: Replaced by the export work")).toBeInTheDocument();

    const education = await card(
      "Bachelor of Science in Computer Science, Springfield State University",
    );
    expect(education.getByText("Sep 2021 - Jan 2025")).toBeInTheDocument();
    expect(education.getByText("Cum laude")).toBeInTheDocument();
    expect(education.getByText(/No bullets yet/)).toBeInTheDocument();

    const birds = await card("Bird Call Classifier");
    expect(birds.getByText("Planned: After the course")).toBeInTheDocument();
    expect(
      (await bulletItem("Train a classifier on bird calls.")).getByText("Planned"),
    ).toBeInTheDocument();

    const recipes = await card("Recipe Box");
    expect(recipes.getByText("Tech stack: TypeScript, React")).toBeInTheDocument();
    expect(recipes.getByText("has no dates", { exact: false })).toBeInTheDocument();
    const search = await bulletItem(SEARCH);
    expect(search.getByText(/has a placeholder: TODO/)).toHaveTextContent(
      "Error: has a placeholder: TODO",
    );

    const rules = within(screen.getByRole("region", { name: "Resume rules" }));
    expect(rules.getByText(/1 error and 1 warning/)).toBeInTheDocument();
    expect(
      rules.getByText(/A bullet under Recipe Box: has a placeholder: TODO/),
    ).toBeInTheDocument();
    expect(rules.getByText(/Recipe Box: has no dates/)).toBeInTheDocument();
  });

  it("edits a bullet as a new version", async () => {
    const server = serveProfile();
    renderAt();
    const exported = await bulletItem(EXPORT);
    await userEvent.click(exported.getByRole("button", { name: "Edit bullet" }));
    const form = within(screen.getByRole("form", { name: "Edit the bullet" }));
    const text = form.getByRole("textbox", { name: "Bullet text" });
    expect(text).toHaveFocus();
    await userEvent.clear(text);
    await userEvent.type(text, "Cut the nightly export from 50 to 12 minutes.");
    await userEvent.selectOptions(form.getByRole("combobox", { name: "Status" }), "Benched");
    await userEvent.type(
      form.getByRole("textbox", { name: "Why it's benched" }),
      "Covered by the runbook bullet",
    );
    await userEvent.selectOptions(form.getByRole("combobox", { name: "Strength" }), "Medium");
    await userEvent.selectOptions(form.getByRole("combobox", { name: "Verification" }), "Verified");
    expect(form.getByText(/Your role types: backend, data/)).toBeInTheDocument();
    const tags = form.getByRole("textbox", { name: "Tags" });
    await userEvent.clear(tags);
    await userEvent.type(tags, "backend");
    await userEvent.click(form.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();
    const edit: BulletEdit = {
      baseVersion: 1,
      text: "Cut the nightly export from 50 to 12 minutes.",
      status: "benched",
      statusReason: "Covered by the runbook bullet",
      strength: "medium",
      verification: "yes",
      verificationNote: "From the job logs",
      tags: ["backend"],
    };
    expect(server.sent).toEqual([
      { method: "PUT", path: "/api/profile/bullets/nw_export", body: edit, csrf: "csrf-1" },
    ]);
    const saved = await bulletItem("Cut the nightly export from 50 to 12 minutes.");
    expect(saved.getByText("Benched: Covered by the runbook bullet")).toBeInTheDocument();
    expect(saved.getByText("Verified: From the job logs")).toBeInTheDocument();
    await vi.waitFor(() => {
      expect(saved.getByRole("button", { name: "Edit bullet" })).toHaveFocus();
    });
    expect(screen.getByText(/Showing version 2/)).toBeInTheDocument();
  });

  it("says when a save changed nothing", async () => {
    const server = serveProfile();
    renderAt();
    await userEvent.click((await bulletItem(TRACKER)).getByRole("button", { name: "Edit bullet" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(
      await screen.findByText("Nothing changed, so no new version was saved."),
    ).toBeInTheDocument();
    expect(server.sent).toHaveLength(1);
    expect(screen.getByText(/Showing version 1/)).toBeInTheDocument();
  });

  it("needs a reason to bench a bullet", async () => {
    const server = serveProfile();
    renderAt();
    await userEvent.click((await bulletItem(EXPORT)).getByRole("button", { name: "Edit bullet" }));
    const element = screen.getByRole("form", { name: "Edit the bullet" });
    const form = within(element);
    await userEvent.selectOptions(form.getByRole("combobox", { name: "Status" }), "Benched");
    fireEvent.submit(element);
    expect(await form.findByRole("alert")).toHaveTextContent("Say why the bullet is benched.");
    expect(server.sent).toEqual([]);
  });

  it("cancels an edit with Escape", async () => {
    const server = serveProfile();
    renderAt();
    const exported = await bulletItem(EXPORT);
    await userEvent.click(exported.getByRole("button", { name: "Edit bullet" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Bullet text" }), " More.{Escape}");
    expect(screen.queryByRole("form", { name: "Edit the bullet" })).not.toBeInTheDocument();
    expect(screen.getByText(EXPORT)).toBeInTheDocument();
    await vi.waitFor(() => {
      expect(exported.getByRole("button", { name: "Edit bullet" })).toHaveFocus();
    });
    expect(server.sent).toEqual([]);
  });

  it("checks a role's dates before saving it", async () => {
    const server = serveProfile();
    renderAt();
    const northwind = await card("Engineer, Northwind Traders");
    await userEvent.click(northwind.getByRole("button", { name: "Edit role" }));
    const element = northwind.getByRole("form", { name: "Edit the role" });
    const form = within(element);
    expect(form.getByRole("textbox", { name: "Title" })).toHaveFocus();
    fireEvent.change(form.getByLabelText("End month"), { target: { value: "2025-01" } });
    fireEvent.submit(element);
    expect(await form.findByRole("alert")).toHaveTextContent(
      "The end date is before the start date.",
    );
    expect(server.sent).toEqual([]);

    fireEvent.change(form.getByLabelText("End month"), { target: { value: "2026-09" } });
    await userEvent.clear(form.getByRole("textbox", { name: "Location" }));
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();
    const edit: RoleEdit = {
      baseVersion: 1,
      name: "Northwind Traders",
      position: "Engineer",
      location: null,
      url: null,
      startDate: "2025-03",
      endDate: "2026-09",
      summary: null,
    };
    expect(server.sent).toEqual([
      { method: "PUT", path: "/api/profile/work/northwind", body: edit, csrf: "csrf-1" },
    ]);
    expect(northwind.getByText("Mar 2025 - Sep 2026")).toBeInTheDocument();
  });

  it("edits the basics, adding and removing profiles", async () => {
    const server = serveProfile();
    renderAt();
    const basics = await card("Casey Morgan");
    await userEvent.click(basics.getByRole("button", { name: "Edit basics" }));
    const form = within(basics.getByRole("form", { name: "Edit your basics" }));
    const label = form.getByRole("textbox", { name: "Label" });
    await userEvent.clear(label);
    await userEvent.type(label, "Backend Engineer");
    await userEvent.click(form.getByRole("button", { name: "Add a profile" }));
    const networks = form.getAllByRole("textbox", { name: "Network" });
    expect(networks).toHaveLength(2);
    await userEvent.type(networks[1] ?? document.body, "LinkedIn");
    await userEvent.type(
      form.getAllByRole("textbox", { name: "Username" })[1] ?? document.body,
      "casey-morgan-example",
    );
    await userEvent.click(form.getByRole("button", { name: "Remove profile 1" }));
    expect(form.getAllByRole("textbox", { name: "Network" })).toHaveLength(1);
    await userEvent.click(form.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();
    const edit: BasicsEdit = {
      baseVersion: 1,
      name: "Casey Morgan",
      label: "Backend Engineer",
      email: "casey@example.com",
      phone: "+1 555 0100",
      url: "https://example.com",
      summary: null,
      location: { city: "Springfield", region: null, countryCode: "US" },
      profiles: [{ network: "LinkedIn", username: "casey-morgan-example", url: null }],
    };
    expect(server.sent).toEqual([
      { method: "PUT", path: "/api/profile/basics", body: edit, csrf: "csrf-1" },
    ]);
    expect(basics.getByText("LinkedIn: casey-morgan-example")).toBeInTheDocument();
    expect(basics.queryByText(/GitHub/)).not.toBeInTheDocument();
  });

  it("reads the profile again when it changed since the page loaded", async () => {
    const server = serveProfile();
    renderAt();
    await userEvent.click((await bulletItem(EXPORT)).getByRole("button", { name: "Edit bullet" }));
    const form = within(screen.getByRole("form", { name: "Edit the bullet" }));
    await userEvent.type(form.getByRole("textbox", { name: "Bullet text" }), " Nightly.");
    server.elsewhere((profile) => {
      const role = profile.work[0];
      if (role !== undefined) {
        role.location = "Remote";
      }
    });
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await form.findByRole("alert")).toHaveTextContent(
      "Couldn't save the bullet: your profile changed since this page loaded: it's now at version 2, so check it and try again.",
    );
    expect(await screen.findByText(/Showing version 2/)).toBeInTheDocument();
    expect((await card("Engineer, Northwind Traders")).getByText("Remote")).toBeInTheDocument();

    // Only the bullet's own fields are saved, so the change made elsewhere stays.
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved as version 3.")).toBeInTheDocument();
    expect(server.sent.map((each) => (each.body as BulletEdit).baseVersion)).toEqual([1, 2]);
  });

  it("won't save a card that changed elsewhere while it was edited", async () => {
    const server = serveProfile();
    renderAt();
    await userEvent.click((await bulletItem(EXPORT)).getByRole("button", { name: "Edit bullet" }));
    const form = within(screen.getByRole("form", { name: "Edit the bullet" }));
    await userEvent.type(form.getByRole("textbox", { name: "Tags" }), ", ops");
    server.elsewhere((profile) => {
      const exported = profile.work[0]?.highlights[0];
      if (exported !== undefined) {
        exported.text = "Cut the nightly export from 50 to 12 minutes.";
      }
    });
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await form.findByText(/This bullet changed while you were editing/)).toBeInTheDocument();
    expect(form.getByRole("button", { name: "Save" })).toBeDisabled();
    await userEvent.click(form.getByRole("button", { name: "Cancel" }));
    expect(
      await screen.findByText("Cut the nightly export from 50 to 12 minutes."),
    ).toBeInTheDocument();
    expect(server.sent).toHaveLength(1);
  });

  it("explains how to load a profile when there's none", async () => {
    serveProfile({ version: null, profile: null, lint: null });
    renderAt();
    expect(await screen.findByText("No profile yet.")).toBeInTheDocument();
    expect(
      await screen.findByText("uv run wj db load-profile --user casey-example"),
    ).toBeInTheDocument();
  });

  it("offers to try again when the profile doesn't load", async () => {
    serveProfile(page(), { "GET /api/profile": json({ detail: "boom" }, 500) });
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your profile didn't load.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("writes only ASCII characters", async () => {
    serveProfile();
    renderAt();
    await userEvent.click((await bulletItem(EXPORT)).getByRole("button", { name: "Edit bullet" }));
    await userEvent.click(
      (await card("Engineer, Northwind Traders")).getByRole("button", { name: "Edit role" }),
    );
    const nonAscii = document.documentElement.outerHTML.match(/[^\t\n\r\x20-\x7e]/gu) ?? [];
    expect(nonAscii).toEqual([]);
  });
});

describe("the profile's history", () => {
  it("lists every version, newest first", async () => {
    const { start, versions } = history();
    serveProfile(start, {}, versions);
    renderAt();
    const list = within(await screen.findByRole("list", { name: "Versions" }));
    expect(list.getAllByRole("heading", { level: 3 }).map((h) => h.textContent)).toEqual([
      "Version 3 (current)",
      "Version 2",
      "Version 1",
    ]);
    const current = await versionRow(3);
    expect(current.getByText("Edited a bullet under Engineer, Northwind Traders")).toBeVisible();
    expect(current.getByText("By you")).toBeInTheDocument();
    expect(current.getByText("3 Oct 2026, 10:00")).toBeInTheDocument();
    expect(current.queryByRole("button", { name: /^Restore/ })).not.toBeInTheDocument();
    const refresh = await versionRow(2);
    expect(refresh.getByText("From the week's journal")).toBeInTheDocument();
    expect(refresh.getByText("From your journal, accepted by you")).toBeInTheDocument();
    expect(refresh.getByRole("button", { name: "Restore version 2" })).toBeInTheDocument();
    const first = await versionRow(1);
    expect(first.getByText("The profile as first loaded")).toBeInTheDocument();
    expect(first.queryByRole("button", { name: "Show changes" })).not.toBeInTheDocument();
  });

  it("shows what a version changed against its parent", async () => {
    const { start, versions } = history();
    serveProfile(start, {}, versions);
    renderAt();
    const row = await versionRow(3);
    const show = row.getByRole("button", { name: "Show changes" });
    expect(show).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(show);
    expect(show).toHaveAttribute("aria-expanded", "true");
    expect(show).toHaveTextContent("Hide changes");
    const changes = within(await row.findByRole("list", { name: "Changes in version 3" }));
    expect(row.getByText("Against version 2:")).toBeInTheDocument();
    expect(changes.getByText("Text of a bullet under Engineer, Northwind Traders")).toBeVisible();
    expect(changes.getByText("nightly").tagName).toBe("INS");
    expect(changes.getByText("overall.").tagName).toBe("DEL");
    expect(changes.getByText("Tags of a bullet under Engineer, Northwind Traders")).toBeVisible();
    expect(changes.getByText(`In the bullet: ${EXPORT}`)).toBeInTheDocument();
    expect(changes.getByText("backend, data")).toBeInTheDocument();

    await userEvent.click(show);
    expect(show).toHaveAttribute("aria-expanded", "false");
    expect(row.queryByRole("list", { name: "Changes in version 3" })).not.toBeInTheDocument();
    await userEvent.click((await versionRow(2)).getByRole("button", { name: "Show changes" }));
    expect(await screen.findByText("Project added: Bird Call Classifier")).toBeInTheDocument();
  });

  it("asks before restoring a version, then saves a copy of it as a new one", async () => {
    const { start, versions } = history();
    const server = serveProfile(start, {}, versions);
    renderAt();
    const row = await versionRow(1);
    const restore = row.getByRole("button", { name: "Restore version 1" });
    await userEvent.click(restore);
    const confirm = row.getByRole("group", { name: /^Restore version 1\?/ });
    expect(confirm).toHaveTextContent("saved as version 4. The versions in between stay");
    expect(within(confirm).getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(row.queryByRole("group")).not.toBeInTheDocument();
    await vi.waitFor(() => {
      expect(restore).toHaveFocus();
    });
    expect(server.sent).toEqual([]);

    await userEvent.click(restore);
    await userEvent.click(within(row.getByRole("group")).getByRole("button", { name: "Restore" }));
    expect(await screen.findByText("Restored version 1 as version 4.")).toBeInTheDocument();
    expect(server.sent).toEqual([
      {
        method: "POST",
        path: "/api/profile/versions/1/restore",
        body: { baseVersion: 3 },
        csrf: "csrf-1",
      },
    ]);
    expect(await screen.findByText(/Showing version 4/)).toBeInTheDocument();
    expect(
      await screen.findByText("Cut the export job from 50 to 12 minutes overall."),
    ).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Bird Call Classifier" })).not.toBeInTheDocument();
    expect((await versionRow(4)).getByText("Restored version 1")).toBeInTheDocument();
    expect((await versionRow(3)).getByRole("button", { name: "Restore version 3" })).toBeVisible();
  });

  it("refuses a restore when the profile changed since the page loaded", async () => {
    const { start, versions } = history();
    const server = serveProfile(start, {}, versions);
    renderAt();
    const row = await versionRow(2);
    await userEvent.click(row.getByRole("button", { name: "Restore version 2" }));
    server.elsewhere((profile) => {
      profile.basics.label = "Backend Engineer";
    });
    await userEvent.click(within(row.getByRole("group")).getByRole("button", { name: "Restore" }));
    expect(await row.findByRole("alert")).toHaveTextContent(
      "Couldn't restore it: your profile changed since this page loaded: it's now at version 4, so check it and try again.",
    );
    expect(await screen.findByText(/Showing version 4/)).toBeInTheDocument();
    expect(row.getByRole("group")).toHaveTextContent("saved as version 5.");
  });

  it("writes only ASCII characters", async () => {
    const { start, versions } = history();
    serveProfile(start, {}, versions);
    renderAt();
    const row = await versionRow(3);
    await userEvent.click(row.getByRole("button", { name: "Show changes" }));
    await row.findByRole("list", { name: "Changes in version 3" });
    await userEvent.click((await versionRow(1)).getByRole("button", { name: "Restore version 1" }));
    const nonAscii = document.documentElement.outerHTML.match(/[^\t\n\r\x20-\x7e]/gu) ?? [];
    expect(nonAscii).toEqual([]);
  });
});

describe("the profile's tailoring metadata", () => {
  it("edits a role's tailoring rules as a new version", async () => {
    const server = serveProfile();
    renderAt();
    const rules = await block("Tailoring rules", 4);
    expect(rules.getByText("Always shown")).toBeInTheDocument();
    await userEvent.click(rules.getByRole("button", { name: "Edit tailoring rules" }));
    const form = within(rules.getByRole("form", { name: "Edit the tailoring rules" }));
    await userEvent.type(
      form.getByRole("textbox", { name: "Approved title variants" }),
      "Software Engineer{Enter}Backend Engineer",
    );
    await userEvent.click(form.getByRole("checkbox", { name: /Load-bearing/ }));
    await userEvent.type(form.getByRole("textbox", { name: "Keep for" }), "backend, data");
    await userEvent.type(form.getByRole("textbox", { name: "Cut for" }), "data");
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await form.findByRole("alert")).toHaveTextContent(
      "The role type data can't be both kept and cut.",
    );
    await userEvent.clear(form.getByRole("textbox", { name: "Cut for" }));
    await userEvent.selectOptions(
      form.getByRole("combobox", { name: "Sensitivity" }),
      "Sensitive: architecture and outcomes only",
    );
    await userEvent.type(
      form.getByRole("textbox", { name: "Honesty boundaries" }),
      "Did not own the deployment pipeline.",
    );
    await userEvent.click(form.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();
    const edit: RoleRulesEdit = {
      baseVersion: 1,
      titleVariants: ["Software Engineer", "Backend Engineer"],
      loadBearing: false,
      duringEducation: false,
      keepFor: ["backend", "data"],
      cutFor: [],
      sensitivity: "sensitive",
      honestyBoundaries: ["Did not own the deployment pipeline."],
    };
    expect(server.sent).toEqual([
      { method: "PUT", path: "/api/profile/work/northwind/rules", body: edit, csrf: "csrf-1" },
    ]);
    const saved = await block("Tailoring rules", 4);
    expect(saved.getByText("Software Engineer, Backend Engineer")).toBeInTheDocument();
    expect(saved.getByText("Did not own the deployment pipeline.")).toBeInTheDocument();
    expect(saved.queryByText("Always shown")).not.toBeInTheDocument();
  });

  it("adds a swap to a bullet", async () => {
    const server = serveProfile();
    renderAt();
    const exported = await bulletItem(EXPORT);
    await userEvent.click(exported.getByRole("button", { name: "Edit swaps" }));
    const form = within(screen.getByRole("form", { name: "Edit the swaps" }));
    expect(form.getByText("No swaps yet.")).toBeInTheDocument();
    await userEvent.click(form.getByRole("button", { name: "Add a swap" }));
    await userEvent.type(form.getByRole("textbox", { name: "Phrasing" }), "Shortened the export");
    await userEvent.type(
      form.getByRole("textbox", { name: "Replaces (optional)" }),
      "Cut the nightly export job",
    );
    await userEvent.type(form.getByRole("textbox", { name: "Fits (optional)" }), "data");
    await userEvent.click(form.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();
    const edit: SwapsEdit = {
      baseVersion: 1,
      swaps: [
        {
          text: "Shortened the export",
          replaces: "Cut the nightly export job",
          contexts: ["data"],
          origin: "approved",
        },
      ],
    };
    expect(server.sent).toEqual([
      { method: "PUT", path: "/api/profile/bullets/nw_export/swaps", body: edit, csrf: "csrf-1" },
    ]);
    const saved = within(await screen.findByRole("list", { name: "Swaps" }));
    expect(saved.getByText("Shortened the export")).toBeInTheDocument();
  });

  it("chooses an education entry's courses for a role type", async () => {
    const server = serveProfile();
    renderAt();
    const coursework = await block("Coursework and rules", 4);
    await userEvent.click(coursework.getByRole("button", { name: "Edit coursework and rules" }));
    const form = within(coursework.getByRole("form", { name: "Edit the coursework and rules" }));
    await userEvent.type(
      form.getByRole("textbox", { name: "Coursework" }),
      "Algorithms{Enter}Databases{Enter}Statistics",
    );
    await userEvent.click(form.getByRole("button", { name: "Add a role type's coursework" }));
    await userEvent.selectOptions(
      form.getByRole("combobox", { name: "Role type" }),
      "Data engineering (data)",
    );
    await userEvent.click(form.getByRole("checkbox", { name: "Statistics" }));
    await userEvent.click(form.getByRole("checkbox", { name: "Databases" }));
    await userEvent.type(form.getByRole("textbox", { name: "Rules" }), "Never print the GPA.");
    await userEvent.click(form.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();
    const edit: CourseworkEdit = {
      baseVersion: 1,
      courses: ["Algorithms", "Databases", "Statistics"],
      courseworkSubsets: [{ roleType: "data", courses: ["Databases", "Statistics"] }],
      rules: ["Never print the GPA."],
    };
    expect(server.sent).toEqual([
      {
        method: "PUT",
        path: "/api/profile/education/springfield_state/coursework",
        body: edit,
        csrf: "csrf-1",
      },
    ]);
    const saved = await block("Coursework and rules", 4);
    expect(saved.getByText("Databases, Statistics")).toBeInTheDocument();
  });

  it("edits skills and gaps, refusing a gap that's also a skill", async () => {
    const server = serveProfile();
    renderAt();
    const skills = await block("Skills and gaps");
    expect(skills.getByText("No skills yet.")).toBeInTheDocument();
    await userEvent.click(skills.getByRole("button", { name: "Edit skills and gaps" }));
    const form = within(skills.getByRole("form", { name: "Edit your skills" }));
    await userEvent.click(form.getByRole("button", { name: "Add a skill" }));
    await userEvent.type(form.getByRole("textbox", { name: "Name" }), "Node.js");
    await userEvent.type(form.getByRole("textbox", { name: "Category" }), "Backend");
    await userEvent.selectOptions(form.getByRole("combobox", { name: "Tier" }), "Working");
    await userEvent.type(form.getByRole("textbox", { name: "Other spellings (optional)" }), "NodeJS");
    await userEvent.click(form.getByRole("checkbox", { name: /Verify before shipping/ }));
    await userEvent.type(form.getByRole("textbox", { name: "Gaps" }), "node.js");
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await form.findByRole("alert")).toHaveTextContent(
      "node.js can't be both a skill and a gap.",
    );
    const gaps = form.getByRole("textbox", { name: "Gaps" });
    await userEvent.clear(gaps);
    await userEvent.type(gaps, "Rust");
    await userEvent.click(form.getByRole("button", { name: "Save" }));

    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();
    const edit: SkillsEdit = {
      baseVersion: 1,
      skills: [
        {
          name: "Node.js",
          previousName: null,
          category: "Backend",
          tier: "working",
          verifyBeforeShipping: true,
          aliases: ["NodeJS"],
        },
      ],
      skillGaps: ["Rust"],
    };
    expect(server.sent).toEqual([
      { method: "PUT", path: "/api/profile/skills", body: edit, csrf: "csrf-1" },
    ]);
    const saved = await block("Skills and gaps");
    expect(saved.getByText("Node.js")).toBeInTheDocument();
    expect(saved.getByText("Backend - Working - verify before shipping - also NodeJS")).toBeInTheDocument();
    expect(saved.getByText("Rust")).toBeInTheDocument();
  });

  it("adds a role type, then a summary and a skills preset for it", async () => {
    const server = serveProfile();
    renderAt();
    const types = await block("Role types");
    await userEvent.click(types.getByRole("button", { name: "Edit role types" }));
    let form = within(types.getByRole("form", { name: "Edit your role types" }));
    expect(form.queryAllByRole("textbox", { name: "ID" })).toHaveLength(0);
    await userEvent.click(form.getByRole("button", { name: "Add a role type" }));
    await userEvent.type(form.getByRole("textbox", { name: "ID" }), "ml");
    await userEvent.type(form.getAllByRole("textbox", { name: "Name" })[2] ?? document.body, "Machine learning");
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();

    const summaries = await block("Summaries");
    await userEvent.click(summaries.getByRole("button", { name: "Edit summaries" }));
    form = within(summaries.getByRole("form", { name: "Edit your summaries" }));
    await userEvent.click(form.getByRole("button", { name: "Add a summary" }));
    await userEvent.selectOptions(
      form.getByRole("combobox", { name: "Role type" }),
      "Machine learning (ml)",
    );
    await userEvent.type(form.getByRole("textbox", { name: "Text" }), "Trains small models.");
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved as version 3.")).toBeInTheDocument();

    const presets = await block("Skills presets");
    await userEvent.click(presets.getByRole("button", { name: "Edit skills presets" }));
    form = within(presets.getByRole("form", { name: "Edit your skills presets" }));
    await userEvent.click(form.getByRole("button", { name: "Add a preset" }));
    await userEvent.selectOptions(
      form.getByRole("combobox", { name: "Role type" }),
      "Machine learning (ml)",
    );
    await userEvent.type(form.getByRole("textbox", { name: "Lines" }), "Python, PyTorch");
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await form.findByRole("alert")).toHaveTextContent("needs a label and skills");
    const lines = form.getByRole("textbox", { name: "Lines" });
    await userEvent.clear(lines);
    await userEvent.type(lines, "Machine learning: Python, PyTorch");
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved as version 4.")).toBeInTheDocument();

    expect(server.sent.map((each) => [each.path, each.body])).toEqual([
      [
        "/api/profile/role-types",
        {
          baseVersion: 1,
          roleTypes: [
            { id: "backend", name: "Backend / full stack" },
            { id: "data", name: "Data engineering" },
            { id: "ml", name: "Machine learning" },
          ],
        },
      ],
      [
        "/api/profile/summaries",
        { baseVersion: 2, summaries: [{ roleType: "ml", text: "Trains small models." }] },
      ],
      [
        "/api/profile/skill-presets",
        {
          baseVersion: 3,
          skillPresets: [
            { roleType: "ml", lines: [{ label: "Machine learning", skills: ["Python", "PyTorch"] }] },
          ],
        },
      ],
    ]);
    expect((await block("Summaries")).getByText("Trains small models.")).toBeInTheDocument();
    expect((await block("Skills presets")).getByText("Python, PyTorch")).toBeInTheDocument();
  });

  it("adds an open question that blocks a bullet, then answers it", async () => {
    const server = serveProfile();
    renderAt();
    expect(await screen.findByText("No open questions yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Add an open question" }));
    const form = within(screen.getByRole("form", { name: "Add an open question" }));
    await userEvent.type(
      form.getByRole("textbox", { name: "Question" }),
      "How long did the export take before?",
    );
    await userEvent.click(form.getByRole("checkbox", { name: EXPORT }));
    await userEvent.click(form.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved as version 2.")).toBeInTheDocument();
    const list = within(await screen.findByRole("list", { name: "Open questions" }));
    expect(list.getByText("How long did the export take before?")).toBeInTheDocument();
    expect(within(list.getByRole("list", { name: "Bullets it blocks" })).getByText(EXPORT)).toBeInTheDocument();

    await userEvent.click(list.getByRole("button", { name: "Edit question" }));
    const edit = within(screen.getByRole("form", { name: "Edit the open question" }));
    expect(edit.getByRole("checkbox", { name: EXPORT })).toBeChecked();
    await userEvent.selectOptions(edit.getByRole("combobox", { name: "Status" }), "Answered");
    await userEvent.click(edit.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Saved as version 3.")).toBeInTheDocument();

    const added: OpenQuestionEdit = {
      baseVersion: 1,
      question: "How long did the export take before?",
      status: "open",
      bulletIds: ["nw_export"],
    };
    expect(server.sent).toEqual([
      { method: "POST", path: "/api/profile/open-questions", body: added, csrf: "csrf-1" },
      {
        method: "PUT",
        path: "/api/profile/open-questions/q_1",
        body: { ...added, baseVersion: 2, status: "answered" },
        csrf: "csrf-1",
      },
    ]);
    expect(await screen.findByText("Answered")).toBeInTheDocument();
  });
});
