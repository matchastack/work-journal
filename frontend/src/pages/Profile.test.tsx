import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type {
  BasicsEdit,
  Bullet,
  BulletEdit,
  Profile,
  ProfilePage,
  RoleEdit,
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

/**
 * Stands in for the API with a profile that each save changes as a new version, so the page
 * reads it again as changed. `elsewhere` changes it as another tab would, without the page
 * knowing; `refuse` answers one request (`"GET /api/profile"`) with an error.
 */
function serveProfile(start: ProfilePage = page(), refuse: Record<string, Response> = {}) {
  let state = structuredClone(start);
  const sent: Sent[] = [];
  const save = (change: (profile: Profile) => void, summary: string) => {
    const current = state.profile;
    const latest = state.version;
    if (current === null || latest === null) {
      throw new Error("no profile to save");
    }
    const profile = structuredClone(current);
    change(profile);
    if (JSON.stringify(profile) === JSON.stringify(current)) {
      return;
    }
    state = { ...state, profile, version: version(latest.number + 1, { summary }) };
  };
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
      const [, , , kind, id = ""] = path.split("/");
      const edit = body as { baseVersion: number };
      if (request.method === "PUT" && edit.baseVersion !== state.version?.number) {
        return json(
          {
            detail: `your profile changed since this page loaded: it's now at version ${String(state.version?.number)}, so check it and try again`,
          },
          409,
        );
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
    expect(sections).toEqual(["Basics", "Work", "Education", "Projects", "Resume rules"]);
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
