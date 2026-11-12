import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FactEdit, Journal, JournalEntry, JournalFact } from "../api/journal";
import { createQueryClient } from "../api/queryClient";
import { App } from "../App";
import { routes } from "../routes";

// Tests run with TZ=Asia/Singapore (vite.config.ts), 8 hours ahead of UTC.
const ME = { id: "4c1ff1e0-8d2b-4c55-9c8e-1f0b3a6d2e71", githubLogin: "casey-example" };
const PLANNING = "0d7d2f8e-3f0a-4a51-9d55-6a1f0f2e9b01";
const EXPORT = "5b0f6c1e-91a4-4c2e-8b7d-2f4e6a8c0d12";
const ON_CALL = "9e2c4a6b-7d8f-4e1a-b3c5-d7e9f1a3b523";
const BULLET = "Cut the nightly export job from 50 to 12 minutes by batching database writes.";

function fact(id: string, statement: string, fields: Partial<JournalFact> = {}): JournalFact {
  return {
    id,
    statement,
    kind: "accomplishment",
    metrics: [],
    tools: [],
    confidentialityFlags: [],
    origin: "journal",
    entryId: EXPORT,
    roleId: "northwind",
    citedBy: [],
    ...fields,
  };
}

function journal(): Journal {
  const entry = (id: string, openedAt: string, fields: Partial<JournalEntry>): JournalEntry => ({
    id,
    openedAt,
    closedAt: openedAt,
    status: "processed",
    triage: "work",
    roleId: null,
    newRole: null,
    question: null,
    messages: [],
    facts: [],
    ...fields,
  });
  const message = (id: string, sentAt: string, text: string) => ({
    id,
    sentAt,
    editedAt: null,
    text,
    edits: [],
  });
  return {
    roles: [
      { id: "northwind", label: "Engineer, Northwind Traders (2025-now)" },
      { id: "contoso", label: "Research Intern, Contoso Research (2024-2024)" },
    ],
    projects: [{ id: "recipe_box", name: "Recipe Box" }],
    entries: [
      entry(PLANNING, "2026-09-28T10:00:00Z", {
        triage: "other",
        messages: [message("m-planning", "2026-09-28T10:00:00Z", "planning week")],
      }),
      // 20:30 UTC on 30 September is already 1 October in Singapore.
      entry(EXPORT, "2026-09-30T20:30:00Z", {
        roleId: "northwind",
        messages: [
          {
            ...message("m-export", "2026-09-30T20:30:00Z", "cut the nightly export to ~12 min"),
            editedAt: "2026-09-30T20:35:00Z",
            edits: [
              { text: "cut the nightly export to 15 min", replacedAt: "2026-09-30T20:35:00Z" },
            ],
          },
          message("m-runbook", "2026-09-30T20:31:00Z", "also wrote the runbook"),
        ],
        facts: [
          fact(`${EXPORT}_1`, "Cut the nightly export from about 50 to 12 minutes.", {
            metrics: [
              {
                subject: "export duration",
                value: { kind: "change", before: 50, after: 12 },
                unit: "minutes",
                qualifier: "approximately",
              },
            ],
            ownership: "owned",
            tools: ["PostgreSQL"],
            date: "2026-10",
            citedBy: [{ bulletId: "nw_export", text: BULLET }],
          }),
          fact(`${EXPORT}_2`, "Wrote the runbook for the nightly export."),
        ],
      }),
      entry(ON_CALL, "2026-10-03T01:00:00Z", {
        status: "waiting_for_role",
        triage: null,
        messages: [message("m-on-call", "2026-10-03T01:00:00Z", "on call this week")],
      }),
    ],
  };
}

interface Sent {
  method: string;
  path: string;
  query: string;
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
 * Stands in for the API with a journal that each change updates, so the page reads it again
 * as changed. `refuse` answers one request (`"DELETE /api/journal/facts/<id>"`) with an error.
 */
function serveJournal(start: Journal = journal(), refuse: Record<string, Response> = {}) {
  let state = structuredClone(start);
  const sent: Sent[] = [];
  const entryOf = (factId: string) =>
    state.entries.find((entry) => entry.facts.some((fact) => fact.id === factId));
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const path = url.pathname;
      const body: unknown = request.body === null ? undefined : await request.json();
      if (path !== "/auth/me" && path !== "/api/journal") {
        sent.push({
          method: request.method,
          path,
          query: url.search,
          body,
          csrf: request.headers.get("X-CSRF-Token"),
        });
      }
      const refused = refuse[`${request.method} ${path}`];
      if (refused !== undefined) {
        return refused;
      }
      const [, , , kind, id = ""] = path.split("/");
      if (path === "/auth/me") {
        return json(ME);
      }
      if (path === "/api/journal") {
        return json(state);
      }
      if (kind === "messages" && request.method === "PATCH") {
        const text = (body as { text: string }).text;
        for (const entry of state.entries) {
          for (const message of entry.messages) {
            if (message.id === id) {
              message.edits.push({ text: message.text, replacedAt: "2026-10-12T06:00:00Z" });
              message.text = text;
            }
          }
        }
        return json({});
      }
      if (kind === "messages" && request.method === "DELETE") {
        for (const entry of state.entries) {
          entry.messages = entry.messages.filter((message) => message.id !== id);
        }
        return new Response(null, { status: 204 });
      }
      if (kind === "entries" && request.method === "DELETE") {
        state = { ...state, entries: state.entries.filter((entry) => entry.id !== id) };
        return new Response(null, { status: 204 });
      }
      const entry = entryOf(id);
      if (kind === "facts" && entry !== undefined && request.method === "PUT") {
        entry.facts = entry.facts.map((fact) =>
          fact.id === id ? { ...fact, ...(body as FactEdit) } : fact,
        );
        return json({});
      }
      if (kind === "facts" && entry !== undefined && request.method === "DELETE") {
        entry.facts = entry.facts.filter((fact) => fact.id !== id);
        return new Response(null, { status: 204 });
      }
      return json({ detail: "Not Found" }, 404);
    }),
  );
  return sent;
}

function renderAt(path = "/journal") {
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(<App router={router} queryClient={createQueryClient({ retries: 0 })} />);
}

async function entryCard(title: RegExp | string) {
  const heading = await screen.findByRole("heading", { level: 3, name: title });
  const article = heading.closest("article");
  if (article === null) {
    throw new Error(`no entry titled ${String(title)}`);
  }
  return within(article);
}

function nth(elements: HTMLElement[], index: number): HTMLElement {
  const element = elements[index];
  if (element === undefined) {
    throw new Error(`there's no element ${String(index)}`);
  }
  return element;
}

beforeEach(() => {
  document.cookie = "__Host-wj_csrf=csrf-1; Secure; Path=/";
});

describe("the journal page", () => {
  it("shows the journal as one document by month, with the facts inline", async () => {
    serveJournal();
    renderAt();
    expect(await screen.findByRole("heading", { level: 1, name: "Journal" })).toBeInTheDocument();
    const months = (await screen.findAllByRole("heading", { level: 2 })).map((h) => h.textContent);
    expect(months).toEqual(["September 2026", "October 2026"]);
    const nav = screen.getByRole("navigation", { name: "Months" });
    expect(within(nav).getByRole("link", { name: "October 2026" })).toHaveAttribute(
      "href",
      "#month-2026-10",
    );
    const titles = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(titles).toEqual(["Mon 28 Sep, 18:00", "Thu 1 Oct, 04:30", "Sat 3 Oct, 09:00"]);

    const planning = await entryCard("Mon 28 Sep, 18:00");
    expect(planning.getByText("Not about work")).toBeInTheDocument();
    const onCall = await entryCard("Sat 3 Oct, 09:00");
    expect(onCall.getByText("Choose its role in Telegram")).toBeInTheDocument();

    const exported = await entryCard("Thu 1 Oct, 04:30");
    expect(exported.getByText("Engineer, Northwind Traders (2025-now)")).toBeInTheDocument();
    expect(exported.getByText("cut the nightly export to ~12 min")).toBeInTheDocument();
    const facts = exported.getByRole("heading", { level: 4, name: "Facts" });
    expect(facts).toBeInTheDocument();
    expect(exported.getByText("export duration: about 50 to 12 minutes")).toBeInTheDocument();
    expect(exported.getByText("Tools: PostgreSQL")).toBeInTheDocument();
    expect(exported.getByText("Oct 2026")).toBeInTheDocument();
    expect(exported.getByText("Cited by a bullet in your profile")).toBeInTheDocument();
  });

  it("shows a message's earlier text", async () => {
    serveJournal();
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    await userEvent.click(entry.getByText("Edited once"));
    expect(entry.getByText("Until 1 Oct 2026, 04:35:")).toBeVisible();
    expect(entry.getByText("cut the nightly export to 15 min")).toBeVisible();
  });

  it("edits a message", async () => {
    const sent = serveJournal();
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    await userEvent.click(entry.getByRole("button", { name: "Edit the message sent at 04:30" }));
    const text = entry.getByRole("textbox", { name: "Message sent at 04:30" });
    expect(text).toHaveFocus();
    await userEvent.clear(text);
    await userEvent.type(text, "cut the nightly export to 13 min");
    await userEvent.click(entry.getByRole("button", { name: "Save" }));
    expect(await entry.findByText("cut the nightly export to 13 min")).toBeInTheDocument();
    expect(entry.getByText("Edited 2 times")).toBeInTheDocument();
    expect(sent).toEqual([
      {
        method: "PATCH",
        path: "/api/journal/messages/m-export",
        query: "",
        body: { text: "cut the nightly export to 13 min" },
        csrf: "csrf-1",
      },
    ]);
  });

  it("cancels a message edit with Escape", async () => {
    const sent = serveJournal();
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    await userEvent.click(entry.getByRole("button", { name: "Edit the message sent at 04:31" }));
    await userEvent.type(entry.getByRole("textbox"), " and more{Escape}");
    expect(entry.queryByRole("textbox")).not.toBeInTheDocument();
    expect(entry.getByText("also wrote the runbook")).toBeInTheDocument();
    expect(sent).toEqual([]);
  });

  it("deletes a message, but not an entry's only one", async () => {
    const sent = serveJournal();
    renderAt();
    const onCall = await entryCard("Sat 3 Oct, 09:00");
    expect(onCall.queryByRole("button", { name: /^Delete the message/ })).not.toBeInTheDocument();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    await userEvent.click(entry.getByRole("button", { name: "Delete the message sent at 04:31" }));
    expect(entry.getByRole("button", { name: "Cancel" })).toHaveFocus();
    await userEvent.click(entry.getByRole("button", { name: "Delete this message" }));
    await vi.waitFor(() => {
      expect(entry.queryByText("also wrote the runbook")).not.toBeInTheDocument();
    });
    expect(sent.map(({ method, path }) => `${method} ${path}`)).toEqual([
      "DELETE /api/journal/messages/m-runbook",
    ]);
  });

  it("edits a fact", async () => {
    const sent = serveJournal();
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    const runbook = entry.getByText("Wrote the runbook for the nightly export.").closest("li");
    if (runbook === null) {
      throw new Error("no runbook fact");
    }
    await userEvent.click(within(runbook).getByRole("button", { name: "Edit fact" }));
    const form = within(entry.getByRole("form", { name: "Edit the fact" }));
    const statement = form.getByRole("textbox", { name: "Statement" });
    await userEvent.clear(statement);
    await userEvent.type(statement, "Wrote the export runbook.");
    await userEvent.selectOptions(form.getByRole("combobox", { name: "Your part" }), "Owned");
    await userEvent.selectOptions(
      form.getByRole("combobox", { name: "Role" }),
      "Research Intern, Contoso Research (2024-2024)",
    );
    await userEvent.selectOptions(form.getByRole("combobox", { name: "Project" }), "Recipe Box");
    fireEvent.change(form.getByLabelText("Month"), { target: { value: "2026-09" } });
    await userEvent.type(form.getByRole("textbox", { name: "Tools, separated by commas" }), "Go, ");
    await userEvent.click(form.getByRole("button", { name: "Add a metric" }));
    await userEvent.type(form.getByRole("textbox", { name: "What was measured" }), "pages");
    await userEvent.selectOptions(form.getByRole("combobox", { name: "Value" }), "A range");
    await userEvent.type(form.getByRole("textbox", { name: "Low" }), "3");
    await userEvent.type(form.getByRole("textbox", { name: "High" }), "4");
    await userEvent.selectOptions(form.getByRole("combobox", { name: "Precision" }), "About");
    await userEvent.click(form.getByRole("button", { name: "Save fact" }));
    expect(await entry.findByText("Wrote the export runbook.")).toBeInTheDocument();
    const edit: FactEdit = {
      statement: "Wrote the export runbook.",
      kind: "accomplishment",
      metrics: [
        {
          subject: "pages",
          value: { kind: "range", low: 3, high: 4 },
          unit: null,
          qualifier: "approximately",
        },
      ],
      ownership: "owned",
      tools: ["Go"],
      outcome: null,
      date: "2026-09",
      roleId: "contoso",
      projectId: "recipe_box",
      proposedRole: null,
    };
    expect(sent).toEqual([
      {
        method: "PUT",
        path: `/api/journal/facts/${EXPORT}_2`,
        query: "",
        body: edit,
        csrf: "csrf-1",
      },
    ]);
    expect(entry.getByText("Research Intern, Contoso Research (2024-2024)")).toBeInTheDocument();
  });

  it("says what's wrong with a fact before saving it", async () => {
    const sent = serveJournal();
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    await userEvent.click(nth(entry.getAllByRole("button", { name: "Edit fact" }), 0));
    const form = within(entry.getByRole("form", { name: "Edit the fact" }));
    expect(form.getByText(/A bullet in your profile cites this fact/)).toBeInTheDocument();
    await userEvent.clear(form.getByRole("textbox", { name: "To" }));
    fireEvent.submit(entry.getByRole("form", { name: "Edit the fact" }));
    expect(await form.findByRole("alert")).toHaveTextContent("Metric 1 needs both numbers.");
    await userEvent.click(form.getByRole("button", { name: "Remove metric 1" }));
    await userEvent.click(form.getByRole("button", { name: "Save fact" }));
    await vi.waitFor(() => {
      expect(sent).toHaveLength(1);
    });
    expect((sent[0]?.body as FactEdit).metrics).toEqual([]);
  });

  it("names the bullets before deleting a fact the profile cites", async () => {
    const sent = serveJournal();
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    const buttons = entry.getAllByRole("button", { name: "Delete fact" });
    await userEvent.click(nth(buttons, 0));
    const confirm = within(entry.getByRole("group", { name: /Delete this fact\?/ }));
    expect(confirm.getByText(/A bullet in your profile cites this/)).toBeInTheDocument();
    expect(confirm.getByText(BULLET)).toBeInTheDocument();
    await userEvent.click(confirm.getByRole("button", { name: "Delete this fact" }));
    await vi.waitFor(() => {
      expect(entry.queryByText(/about 50 to 12 minutes/)).not.toBeInTheDocument();
    });
    await userEvent.click(nth(buttons, 1));
    expect(entry.queryByText(BULLET)).not.toBeInTheDocument();
    await userEvent.click(entry.getByRole("button", { name: "Delete this fact" }));
    await vi.waitFor(() => {
      expect(sent).toHaveLength(2);
    });
    expect(sent.map(({ method, path, query }) => `${method} ${path}${query}`)).toEqual([
      `DELETE /api/journal/facts/${EXPORT}_1?confirm=true`,
      `DELETE /api/journal/facts/${EXPORT}_2?confirm=false`,
    ]);
  });

  it("deletes an entry with its messages and facts", async () => {
    const sent = serveJournal();
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    await userEvent.click(
      entry.getByRole("button", { name: "Delete entry from Thu 1 Oct, 04:30" }),
    );
    const confirm = within(
      entry.getByRole("group", {
        name: "Delete this entry, its 2 messages and 2 facts? This can't be undone.",
      }),
    );
    expect(confirm.getByText(BULLET)).toBeInTheDocument();
    await userEvent.click(confirm.getByRole("button", { name: "Delete this entry" }));
    await vi.waitFor(() => {
      expect(screen.queryByRole("heading", { name: "Thu 1 Oct, 04:30" })).not.toBeInTheDocument();
    });
    expect(sent.map(({ method, path, query }) => `${method} ${path}${query}`)).toEqual([
      `DELETE /api/journal/entries/${EXPORT}?confirm=true`,
    ]);
  });

  it("says why a change didn't work", async () => {
    serveJournal(journal(), {
      [`DELETE /api/journal/facts/${EXPORT}_2`]: json({ detail: "there's no fact x" }, 404),
    });
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    await userEvent.click(nth(entry.getAllByRole("button", { name: "Delete fact" }), 1));
    await userEvent.click(entry.getByRole("button", { name: "Delete this fact" }));
    expect(await entry.findByRole("alert")).toHaveTextContent(
      "Couldn't delete it: there's no fact x.",
    );
  });

  it("goes back to the sign-in page when the session ends during a change", async () => {
    serveJournal(journal(), {
      "DELETE /api/journal/messages/m-runbook": json({ detail: "Not signed in" }, 401),
    });
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    await userEvent.click(entry.getByRole("button", { name: "Delete the message sent at 04:31" }));
    await userEvent.click(entry.getByRole("button", { name: "Delete this message" }));
    expect(await screen.findByRole("link", { name: "Sign in with GitHub" })).toBeInTheDocument();
  });

  it("shows the entry the bot's reply links to", async () => {
    serveJournal();
    renderAt(`/journal#${ON_CALL}`);
    const heading = await screen.findByRole("heading", { level: 3, name: "Sat 3 Oct, 09:00" });
    await vi.waitFor(() => {
      expect(heading.closest("article")).toHaveFocus();
    });
  });

  it("shows a catch-up answer after the question it answers", async () => {
    const start = journal();
    const question = "What did you work on this week?";
    const entries = start.entries.map((entry) =>
      entry.id === ON_CALL ? { ...entry, question } : entry,
    );
    serveJournal({ ...start, entries });
    renderAt();
    const heading = await screen.findByRole("heading", { level: 3, name: "Sat 3 Oct, 09:00" });
    const card = heading.closest("article");
    expect(card).toHaveTextContent("Catch-up question: What did you work on this week?");
    expect(card).toHaveTextContent("on call this week");
  });

  it("downloads the journal, with times in the browser's time zone", async () => {
    serveJournal();
    renderAt();
    const markdown = await screen.findByRole("link", { name: "Download Markdown" });
    expect(markdown).toHaveAttribute("href", "/api/journal/export.md?tz=Asia%2FSingapore");
    expect(markdown).toHaveAttribute("download");
    expect(screen.getByRole("link", { name: "Download JSON" })).toHaveAttribute(
      "href",
      "/api/journal/export.json",
    );
  });

  it("says when the journal is empty", async () => {
    serveJournal({ entries: [], roles: [], projects: [] });
    renderAt();
    expect(await screen.findByText("Your journal is empty.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Download JSON" })).not.toBeInTheDocument();
  });

  it("offers to try again when the journal doesn't load", async () => {
    serveJournal(journal(), { "GET /api/journal": json({ detail: "boom" }, 500) });
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your journal didn't load.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("writes only ASCII characters", async () => {
    serveJournal();
    renderAt();
    const entry = await entryCard("Thu 1 Oct, 04:30");
    await userEvent.click(nth(entry.getAllByRole("button", { name: "Edit fact" }), 0));
    await userEvent.click(nth(entry.getAllByRole("button", { name: "Delete fact" }), 0));
    const nonAscii = document.documentElement.outerHTML.match(/[^\t\n\r\x20-\x7e]/gu) ?? [];
    expect(nonAscii).toEqual([]);
  });
});
