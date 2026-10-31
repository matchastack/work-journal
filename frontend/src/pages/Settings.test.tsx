import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createQueryClient } from "../api/queryClient";
import type { ReminderSettings, SettingsView } from "../api/settings";
import { App } from "../App";
import { LINK_CHECK_MS } from "../components/settings/TelegramSection";
import { clockTime } from "../format";
import { routes } from "../routes";

// Tests run with TZ=Asia/Singapore (vite.config.ts), 8 hours ahead of UTC.
const ME = { id: "4c1ff1e0-8d2b-4c55-9c8e-1f0b3a6d2e71", githubLogin: "casey-example" };
const DEEP_LINK = "https://t.me/wj_example_bot?start=one-time-token";
const DEFAULT = { days: ["fri" as const], time: "18:00", timeZone: "Asia/Singapore" };
/** When a link made now expires, as the API says: 15 minutes on. */
const expiresAt = () => new Date(Date.now() + 15 * 60_000).toISOString();

function settings(): SettingsView {
  return {
    telegram: { linked: false, linkedAt: null },
    reminders: {
      schedule: DEFAULT,
      default: DEFAULT,
      chosen: false,
      paused: false,
      summary: "Fridays at 18:00 (Asia/Singapore)",
    },
    portfolio: { handle: null, defaultVisibility: "public", openToWork: false, indexing: true },
    styleNotes: {
      avoid: ["spearheaded"],
      notes: [
        { text: "Say what it fixed", source: "owner", addedAt: "2026-10-01T02:00:00Z" },
        { text: "Keep the team's name out", source: "rejection", addedAt: "2026-10-02T02:00:00Z" },
      ],
    },
    confidentialTerms: { terms: ["Project Falcon"] },
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

/** Lowercase, each once in any case, in order: as the API keeps a list. */
function onceEach(items: readonly string[]): string[] {
  const seen = new Set<string>();
  return items.filter((item) => !seen.has(item.toLowerCase()) && seen.add(item.toLowerCase()));
}

/**
 * Stands in for the API with settings that each save changes. `refuse` answers one request
 * (`"PUT /api/settings/portfolio"`) with an error.
 */
function serveSettings(refuse: Record<string, Response> = {}) {
  const state = { settings: settings(), signedIn: true, expiresAt: expiresAt() };
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const body: unknown = request.body === null ? undefined : await request.json();
      if (request.method !== "GET") {
        sent.push({ method: request.method, path, body, csrf: request.headers.get("X-CSRF-Token") });
      }
      const refused = refuse[`${request.method} ${path}`];
      if (refused !== undefined) {
        return refused;
      }
      const current = state.settings;
      switch (`${request.method} ${path}`) {
        case "GET /auth/me":
          return state.signedIn ? json(ME) : json({ detail: "Not signed in" }, 401);
        case "GET /api/settings":
          return state.signedIn ? json(current) : json({ detail: "Not signed in" }, 401);
        case "PUT /api/settings/reminders": {
          const chosen = body as ReminderSettings;
          const schedule = {
            days: chosen.days ?? DEFAULT.days,
            time: chosen.time ?? DEFAULT.time,
            timeZone: chosen.timeZone ?? DEFAULT.timeZone,
          };
          current.reminders = {
            schedule,
            default: DEFAULT,
            chosen: chosen.days !== null || chosen.time !== null || chosen.timeZone !== null,
            paused: chosen.paused,
            summary: `${schedule.days.join(", ")} at ${schedule.time} (${schedule.timeZone})`,
          };
          return json(current.reminders);
        }
        case "PUT /api/settings/portfolio":
          current.portfolio = body as SettingsView["portfolio"];
          return json(current.portfolio);
        case "PUT /api/settings/style-notes": {
          const change = body as { avoid: string[]; notes: string[] };
          current.styleNotes = {
            avoid: onceEach(change.avoid),
            notes: onceEach(change.notes).map((text) => ({
              text,
              source: "owner",
              addedAt: "2026-10-30T02:00:00Z",
            })),
          };
          return json(current.styleNotes);
        }
        case "PUT /api/settings/confidential-terms":
          current.confidentialTerms = { terms: onceEach((body as { terms: string[] }).terms) };
          return json(current.confidentialTerms);
        case "POST /api/telegram/link":
          return json({ url: DEEP_LINK, expiresAt: state.expiresAt });
        case "DELETE /api/settings/data":
          state.signedIn = false;
          return new Response(null, { status: 204 });
        default:
          return json({ detail: "Not Found" }, 404);
      }
    }),
  );
  return { sent, state };
}

function renderAt(path = "/settings") {
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

afterEach(() => {
  vi.useRealTimers();
});

describe("the settings page", () => {
  it("shows every group of settings", async () => {
    serveSettings();
    renderAt();
    expect(await screen.findByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
    const headings = (await screen.findAllByRole("heading", { level: 2 })).map(
      (heading) => heading.textContent,
    );
    expect(headings).toEqual([
      "Telegram",
      "Reminders",
      "Portfolio page",
      "Style notes",
      "Confidential terms",
      "Your data",
    ]);
    const telegram = await section("Telegram");
    expect(telegram.getByText("Not linked yet.")).toBeInTheDocument();
    expect(telegram.getByRole("button", { name: "Link Telegram" })).toBeInTheDocument();

    const reminders = await section("Reminders");
    expect(reminders.getByText(/Now: Fridays at 18:00 \(Asia\/Singapore\)\./)).toBeInTheDocument();
    expect(reminders.getByRole("checkbox", { name: "Friday" })).toBeChecked();
    expect(reminders.getByRole("checkbox", { name: "Monday" })).not.toBeChecked();
    expect(reminders.getByLabelText("Time")).toHaveValue("18:00");
    expect(reminders.getByLabelText("Time zone")).toHaveValue("Asia/Singapore");
    expect(reminders.getByRole("checkbox", { name: /Pause reminders/ })).not.toBeChecked();
    expect(reminders.queryByRole("button", { name: /Use the default/ })).not.toBeInTheDocument();

    const portfolio = await section("Portfolio page");
    expect(portfolio.getByLabelText("Handle")).toHaveValue("");
    expect(portfolio.getByLabelText("Default visibility")).toHaveValue("public");
    expect(portfolio.getByRole("checkbox", { name: "Show that I'm open to work" })).not.toBeChecked();
    expect(portfolio.getByRole("checkbox", { name: /search engines/ })).toBeChecked();
    expect(portfolio.getByText(/take effect when the public portfolio page is built/)).toBeVisible();

    const notes = await section("Style notes");
    expect(notes.getByLabelText("Words to avoid")).toHaveValue("spearheaded");
    expect(notes.getByLabelText("Notes")).toHaveValue(
      "Say what it fixed\nKeep the team's name out",
    );
    expect(notes.getByText(/added here \(1 so far\)/)).toBeInTheDocument();
    const terms = await section("Confidential terms");
    expect(terms.getByLabelText("Terms")).toHaveValue("Project Falcon");

    const data = await section("Your data");
    const download = data.getByRole("link", { name: "Download all my data" });
    expect(download).toHaveAttribute("href", "/api/settings/export.json");
    expect(download).toHaveAttribute("download");
  });

  it("links Telegram with a deep link and a QR code, then says when it's linked", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
    const { sent, state } = serveSettings();
    renderAt();
    const telegram = await section("Telegram");
    await user.click(telegram.getByRole("button", { name: "Link Telegram" }));
    const open = await telegram.findByRole("link", { name: "Open in Telegram" });
    expect(open).toHaveAttribute("href", DEEP_LINK);
    expect(sent).toEqual([
      { method: "POST", path: "/api/telegram/link", body: undefined, csrf: "csrf-1" },
    ]);
    const code = telegram.getByRole("img", { name: "QR code of the link to the bot" });
    expect(code.tagName.toLowerCase()).toBe("svg");
    expect(code.querySelectorAll("path").length).toBeGreaterThan(0);
    const until = clockTime(state.expiresAt);
    expect(telegram.getByText(new RegExp(`It works once, until ${until}\\.`))).toBeInTheDocument();

    state.settings.telegram = { linked: true, linkedAt: "2026-10-30T02:05:00Z" };
    await act(() => vi.advanceTimersByTimeAsync(LINK_CHECK_MS));
    expect(
      await telegram.findByText("Your chat is linked. Send the bot a note to try it."),
    ).toBeInTheDocument();
    expect(telegram.queryByRole("link", { name: "Open in Telegram" })).not.toBeInTheDocument();
    expect(telegram.getByText(/since 30 Oct 2026, 10:05/)).toBeInTheDocument();
    expect(telegram.getByRole("button", { name: "Relink" })).toBeInTheDocument();
  });

  it("says why a link couldn't be made", async () => {
    const refuse = { "POST /api/telegram/link": json({ detail: "The bot isn't set up" }, 503) };
    serveSettings(refuse);
    renderAt();
    const telegram = await section("Telegram");
    await userEvent.click(telegram.getByRole("button", { name: "Link Telegram" }));
    expect(await telegram.findByRole("alert")).toHaveTextContent(
      "Couldn't make a link: The bot isn't set up.",
    );
  });

  it("saves the reminder schedule, time zone and pause", async () => {
    const { sent } = serveSettings();
    renderAt();
    const reminders = await section("Reminders");
    await userEvent.click(reminders.getByRole("checkbox", { name: "Thursday" }));
    await userEvent.click(reminders.getByRole("checkbox", { name: "Friday" }));
    await userEvent.click(reminders.getByRole("checkbox", { name: "Monday" }));
    fireEvent.change(reminders.getByLabelText("Time"), { target: { value: "07:30" } });
    const zone = reminders.getByLabelText("Time zone");
    await userEvent.clear(zone);
    await userEvent.type(zone, "Europe/London");
    await userEvent.click(reminders.getByRole("checkbox", { name: /Pause reminders/ }));
    await userEvent.click(reminders.getByRole("button", { name: "Save reminders" }));
    expect(await reminders.findByText("Saved.")).toBeInTheDocument();
    expect(sent).toEqual([
      {
        method: "PUT",
        path: "/api/settings/reminders",
        body: { days: ["mon", "thu"], time: "07:30", timeZone: "Europe/London", paused: true },
        csrf: "csrf-1",
      },
    ]);
    expect(reminders.getByText(/Reminders are paused now\./)).toBeInTheDocument();

    await userEvent.click(reminders.getByRole("checkbox", { name: /Pause reminders/ }));
    expect(reminders.queryByText("Saved.")).not.toBeInTheDocument();
    await userEvent.click(
      reminders.getByRole("button", {
        name: "Use the default schedule (Fri at 18:00, Asia/Singapore)",
      }),
    );
    expect(await reminders.findByText("Saved.")).toBeInTheDocument();
    expect(sent.at(-1)?.body).toEqual({ days: null, time: null, timeZone: null, paused: false });
    expect(reminders.getByRole("checkbox", { name: "Friday" })).toBeChecked();
    expect(reminders.getByRole("checkbox", { name: "Monday" })).not.toBeChecked();
    expect(reminders.getByLabelText("Time zone")).toHaveValue("Asia/Singapore");
  });

  it("asks for a day and a time zone before saving the reminders", async () => {
    const refuse = { "PUT /api/settings/reminders": json({ detail: [{ msg: "zone" }] }, 422) };
    const { sent } = serveSettings(refuse);
    renderAt();
    const reminders = await section("Reminders");
    await userEvent.click(reminders.getByRole("checkbox", { name: "Friday" }));
    await userEvent.click(reminders.getByRole("button", { name: "Save reminders" }));
    expect(reminders.getByRole("alert")).toHaveTextContent("Choose at least one day.");
    await userEvent.click(reminders.getByRole("checkbox", { name: "Sunday" }));
    await userEvent.clear(reminders.getByLabelText("Time zone"));
    await userEvent.click(reminders.getByRole("button", { name: "Save reminders" }));
    expect(reminders.getByRole("alert")).toHaveTextContent("Choose a time zone");
    expect(sent).toEqual([]);

    await userEvent.type(reminders.getByLabelText("Time zone"), "Mars/Olympus_Mons");
    await userEvent.click(reminders.getByRole("button", { name: "Save reminders" }));
    expect(await reminders.findByRole("alert")).toHaveTextContent(
      "Couldn't save the reminders: check the fields.",
    );
  });

  it("saves the portfolio settings, with the handle in lowercase", async () => {
    const { sent } = serveSettings();
    renderAt();
    const portfolio = await section("Portfolio page");
    const handle = portfolio.getByLabelText("Handle");
    await userEvent.type(handle, "casey example");
    await userEvent.click(portfolio.getByRole("button", { name: "Save portfolio settings" }));
    expect(portfolio.getByRole("alert")).toHaveTextContent(
      "The handle needs 2 to 40 letters, digits and single hyphens",
    );
    expect(sent).toEqual([]);

    await userEvent.clear(handle);
    await userEvent.type(handle, " Casey-Example ");
    await userEvent.selectOptions(
      portfolio.getByLabelText("Default visibility"),
      "Resume only: only in resumes you send",
    );
    await userEvent.click(portfolio.getByRole("checkbox", { name: "Show that I'm open to work" }));
    await userEvent.click(portfolio.getByRole("checkbox", { name: /search engines/ }));
    await userEvent.click(portfolio.getByRole("button", { name: "Save portfolio settings" }));
    expect(await portfolio.findByText("Saved.")).toBeInTheDocument();
    expect(sent).toEqual([
      {
        method: "PUT",
        path: "/api/settings/portfolio",
        body: {
          handle: "casey-example",
          defaultVisibility: "resume_only",
          openToWork: true,
          indexing: false,
        },
        csrf: "csrf-1",
      },
    ]);
    expect(handle).toHaveValue("casey-example");
  });

  it("says when the handle is taken", async () => {
    const taken = json({ detail: "That handle is taken; choose another" }, 409);
    serveSettings({ "PUT /api/settings/portfolio": taken });
    renderAt();
    const portfolio = await section("Portfolio page");
    await userEvent.type(portfolio.getByLabelText("Handle"), "casey");
    await userEvent.click(portfolio.getByRole("button", { name: "Save portfolio settings" }));
    expect(await portfolio.findByRole("alert")).toHaveTextContent(
      "Couldn't save the portfolio settings: That handle is taken; choose another.",
    );
  });

  it("saves the style notes and the confidential terms, one per line", async () => {
    const { sent } = serveSettings();
    renderAt();
    const notes = await section("Style notes");
    const avoid = notes.getByLabelText("Words to avoid");
    await userEvent.clear(avoid);
    await userEvent.type(avoid, "leveraged{Enter}{Enter}  synergy {Enter}Leveraged");
    const preferences = notes.getByLabelText("Notes");
    await userEvent.type(preferences, "{Enter}Prefer plain verbs");
    await userEvent.click(notes.getByRole("button", { name: "Save style notes" }));
    expect(await notes.findByText("Saved.")).toBeInTheDocument();
    expect(sent.at(-1)).toEqual({
      method: "PUT",
      path: "/api/settings/style-notes",
      body: {
        avoid: ["leveraged", "synergy", "Leveraged"],
        notes: ["Say what it fixed", "Keep the team's name out", "Prefer plain verbs"],
      },
      csrf: "csrf-1",
    });
    expect(avoid).toHaveValue("leveraged\nsynergy");

    const terms = await section("Confidential terms");
    await userEvent.type(terms.getByLabelText("Terms"), "{Enter}Acme Labs{Enter}");
    await userEvent.click(terms.getByRole("button", { name: "Save confidential terms" }));
    expect(await terms.findByText("Saved.")).toBeInTheDocument();
    expect(sent.at(-1)).toEqual({
      method: "PUT",
      path: "/api/settings/confidential-terms",
      body: { terms: ["Project Falcon", "Acme Labs"] },
      csrf: "csrf-1",
    });
  });

  it("keeps a term the API would refuse from being sent", async () => {
    const { sent } = serveSettings();
    renderAt();
    const terms = await section("Confidential terms");
    fireEvent.change(terms.getByLabelText("Terms"), { target: { value: "x".repeat(101) } });
    await userEvent.click(terms.getByRole("button", { name: "Save confidential terms" }));
    expect(terms.getByRole("alert")).toHaveTextContent("Each term can have up to 100 characters");
    expect(sent).toEqual([]);
  });

  it("deletes all data only once the username is typed, then shows the sign-in page", async () => {
    const { sent } = serveSettings();
    const router = renderAt();
    const data = await section("Your data");
    const opener = data.getByRole("button", { name: "Delete all my data" });
    await userEvent.click(opener);
    const dialog = data.getByRole("dialog", { name: "Delete all your data?" });
    const confirm = within(dialog).getByLabelText(
      "Type your GitHub username, casey-example, to confirm",
    );
    expect(confirm).toHaveFocus();
    const remove = within(dialog).getByRole("button", { name: "Delete everything" });
    expect(remove).toBeDisabled();
    await userEvent.type(confirm, "casey");
    expect(remove).toBeDisabled();
    await userEvent.keyboard("{Escape}");
    expect(data.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
    expect(sent).toEqual([]);

    await userEvent.click(opener);
    await userEvent.type(data.getByRole("textbox"), "CASEY-EXAMPLE ");
    await userEvent.click(data.getByRole("button", { name: "Delete everything" }));
    expect(await screen.findByRole("link", { name: "Sign in with GitHub" })).toBeInTheDocument();
    expect(router.state.location.pathname).toBe("/sign-in");
    expect(sent).toEqual([
      {
        method: "DELETE",
        path: "/api/settings/data",
        body: { confirm: "CASEY-EXAMPLE" },
        csrf: "csrf-1",
      },
    ]);
  });

  it("says when the data couldn't be deleted", async () => {
    const refuse = {
      "DELETE /api/settings/data": json(
        { detail: "Type your GitHub username to confirm; nothing was deleted" },
        422,
      ),
    };
    serveSettings(refuse);
    renderAt();
    const data = await section("Your data");
    await userEvent.click(data.getByRole("button", { name: "Delete all my data" }));
    await userEvent.type(data.getByRole("textbox"), "casey-example");
    await userEvent.click(data.getByRole("button", { name: "Delete everything" }));
    expect(await data.findByRole("alert")).toHaveTextContent(
      "Couldn't delete your data: Type your GitHub username to confirm; nothing was deleted.",
    );
    expect(screen.getByRole("heading", { level: 1, name: "Settings" })).toBeInTheDocument();
  });

  it("offers to try again when the settings don't load", async () => {
    serveSettings({ "GET /api/settings": json({ detail: "boom" }, 500) });
    renderAt();
    expect(await screen.findByRole("alert")).toHaveTextContent("Your settings didn't load.");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("writes only ASCII characters", async () => {
    serveSettings();
    renderAt();
    await screen.findByRole("heading", { level: 2, name: "Your data" });
    await userEvent.click(screen.getByRole("button", { name: "Link Telegram" }));
    await screen.findByRole("link", { name: "Open in Telegram" });
    await userEvent.click(screen.getByRole("button", { name: "Delete all my data" }));
    const html = document.documentElement.outerHTML;
    expect(html.match(/[^\t\n\r\x20-\x7e]/gu) ?? []).toEqual([]);
  });
});

describe("a link to the bot", () => {
  it("goes once it expires", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
    serveSettings();
    renderAt();
    const telegram = await section("Telegram");
    await user.click(telegram.getByRole("button", { name: "Link Telegram" }));
    await telegram.findByRole("link", { name: "Open in Telegram" });
    await act(() => vi.advanceTimersByTimeAsync(15 * 60_000));
    expect(telegram.queryByRole("link", { name: "Open in Telegram" })).not.toBeInTheDocument();
    expect(telegram.getByRole("button", { name: "Link Telegram" })).toBeInTheDocument();
  });
});
