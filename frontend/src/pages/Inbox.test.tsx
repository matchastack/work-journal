import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Inbox, InboxChangeSet, InboxOp, VerifierReport } from "../api/inbox";
import { createQueryClient } from "../api/queryClient";
import { App } from "../App";
import { routes } from "../routes";

// Tests run with TZ=Asia/Singapore (vite.config.ts), 8 hours ahead of UTC.
const ME = { id: "4c1ff1e0-8d2b-4c55-9c8e-1f0b3a6d2e71", githubLogin: "casey-example" };
const CHANGE_SET = "7f3e2a1b-4c5d-4e6f-8a9b-0c1d2e3f4a5b";
const ENTRY = "5b0f6c1e-91a4-4c2e-8b7d-2f4e6a8c0d12";
const EXPORT = "Cut the nightly export job from 50 to 12 minutes by batching database writes.";
const REWORDED = "Cut the nightly export from 50 to 12 minutes by batching database writes.";

function report(text: string, ...errors: string[]): VerifierReport {
  return {
    text,
    passed: errors.length === 0,
    attempts: 1,
    factIds: [],
    findings: errors.map((message) => ({
      check: "claims",
      severity: "error",
      code: "added_tool",
      message,
    })),
    claims: [
      { claim: "Wrote the runbook", verdict: "supported", factIds: [] },
      { claim: "in Go", verdict: "unsupported", issue: "added_tool", term: "Go", factIds: [] },
    ],
    derivations: [],
  };
}

function op(position: number, fields: Partial<InboxOp>): InboxOp {
  return {
    position,
    kind: "edit_bullet",
    status: "proposed",
    reason: null,
    title: "",
    context: null,
    before: null,
    after: null,
    diff: [],
    outputs: [],
    sources: [],
    verifierReport: null,
    editable: false,
    needs: null,
    ...fields,
  };
}

function inbox(): Inbox {
  return {
    profileVersion: 2,
    changeSets: [
      {
        id: CHANGE_SET,
        author: "ai",
        summary: "From your journal",
        baseVersion: 1,
        createdAt: "2026-10-12T01:00:00Z",
        ops: [
          op(0, {
            title: "Reword a bullet under Engineer, Northwind Traders",
            before: EXPORT,
            after: REWORDED,
            diff: [
              { kind: "same", text: "Cut the nightly export " },
              { kind: "removed", text: "job " },
              { kind: "same", text: "from 50 to 12 minutes by batching database writes." },
            ],
            outputs: ["master_resume", "tailored_resumes", "portfolio", "linkedin"],
            sources: [{ id: "fact_nw_export", statement: EXPORT, entryId: null }],
            verifierReport: { ...report(REWORDED), claims: [] },
            editable: true,
          }),
          op(1, {
            kind: "add_bullet",
            title: "Add a bullet under Engineer, Northwind Traders",
            after: "Wrote the runbook in Go.",
            outputs: ["master_resume", "tailored_resumes", "portfolio", "linkedin"],
            sources: [
              { id: "f_runbook", statement: "Wrote the runbook.", entryId: ENTRY },
              { id: "gone", statement: null, entryId: null },
            ],
            verifierReport: report("Wrote the runbook in Go.", "No fact names Go."),
            editable: true,
          }),
          op(2, {
            kind: "set_bullet_status",
            title: "Bench a bullet under Engineer, Northwind Traders",
            context: "Built order services.",
            before: "active",
            after: "benched: Older",
            outputs: ["master_resume"],
          }),
        ],
      },
    ],
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
 * Stands in for the API with an inbox that each decision updates. `refuse` answers one request
 * (`"POST /api/inbox/<id>/apply"`) with an error.
 */
function serveInbox(start: Inbox = inbox(), refuse: Record<string, Response> = {}) {
  let state = structuredClone(start);
  const sent: Sent[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const body: unknown = request.body === null ? undefined : await request.json();
      if (path !== "/auth/me" && path !== "/api/inbox") {
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
      if (path === "/api/inbox") {
        return json(state);
      }
      const [, , , id, action = "", position = "", what = ""] = path.split("/");
      const changeSet = state.changeSets.find((item) => item.id === id);
      if (changeSet === undefined) {
        return json({ detail: "Not Found" }, 404);
      }
      const answer = (changed: InboxChangeSet) => json(changed);
      const target = changeSet.ops[Number(position)];
      if (action === "ops" && what === "decision" && target !== undefined) {
        const { status, reason } = body as { status: InboxOp["status"]; reason: string | null };
        target.status = status;
        target.reason = status === "rejected" ? reason : null;
        // A rejection takes the changes that need it along.
        for (const item of changeSet.ops) {
          if (status === "rejected" && item.needs === target.position) {
            item.status = "rejected";
            item.reason = null;
          }
        }
        return answer(changeSet);
      }
      if (action === "ops" && what === "text" && target !== undefined) {
        const { text } = body as { text: string };
        Object.assign(target, {
          status: "proposed",
          after: text,
          diff: [],
          verifierReport: { ...report(text), claims: [] },
        });
        return answer(changeSet);
      }
      if (action === "accept-passed") {
        for (const item of changeSet.ops) {
          if (item.status === "proposed" && item.verifierReport?.passed !== false) {
            item.status = "accepted";
          }
        }
        return answer(changeSet);
      }
      if (action === "apply") {
        state = { ...state, changeSets: [], profileVersion: 3 };
        return json({ version: 3 });
      }
      return json({ detail: "Not Found" }, 404);
    }),
  );
  return sent;
}

function renderInbox() {
  const router = createMemoryRouter(routes, { initialEntries: ["/inbox"] });
  render(<App router={router} queryClient={createQueryClient({ retries: 0 })} />);
  return router;
}

async function change(title: string, index = 0) {
  const headings = await screen.findAllByRole("heading", { level: 3, name: title });
  const article = headings[index]?.closest("article");
  if (!article) {
    throw new Error(`no change titled ${title}`);
  }
  return within(article);
}

beforeEach(() => {
  document.cookie = "__Host-wj_csrf=csrf-1; Secure; Path=/";
});

describe("the inbox", () => {
  it("shows each change with its diff, outputs, sources and verifier report", async () => {
    serveInbox();
    const router = renderInbox();
    expect(
      await screen.findByRole("heading", { level: 2, name: "From your journal" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Proposed 12 Oct 2026, 09:00 from your journal: 3 changes/),
    ).toHaveTextContent("0 accepted, 0 rejected, 3 waiting.");
    expect(screen.getByText(/your profile is now at version 2/)).toBeInTheDocument();

    const reworded = await change("Reword a bullet under Engineer, Northwind Traders");
    expect(reworded.getByText("job").tagName).toBe("DEL");
    expect(reworded.getByText("job").parentElement).toHaveTextContent(EXPORT);
    expect(
      reworded.getByText("Changes: Master resume, Tailored resumes, Portfolio page, LinkedIn"),
    ).toBeInTheDocument();
    expect(reworded.getByText("(from your imported resume)")).toBeInTheDocument();
    expect(reworded.getByText("Verifier: passed")).toBeInTheDocument();

    const added = await change("Add a bullet under Engineer, Northwind Traders");
    expect(added.getByText("Wrote the runbook in Go.")).toBeInTheDocument();
    expect(added.getByText(/Verifier: flagged/)).toBeInTheDocument();
    expect(added.getByText(/No fact names Go\./)).toBeInTheDocument();
    await userEvent.click(added.getByText("2 claims checked against the facts"));
    expect(added.getByText('Not supported: "in Go" (Go)')).toBeVisible();
    expect(added.getByText("A fact that has been deleted.")).toBeInTheDocument();
    await userEvent.click(added.getByRole("link", { name: "In your journal" }));
    expect(router.state.location.pathname).toBe("/journal");
    expect(router.state.location.hash).toBe(`#${ENTRY}`);
  });

  it("keeps a removed last word apart from the word that replaces it", async () => {
    const changed = inbox();
    const first = changed.changeSets[0]?.ops[0];
    if (first === undefined) {
      throw new Error("no change");
    }
    first.diff = [
      { kind: "same", text: "Led the " },
      { kind: "removed", text: "team." },
      { kind: "added", text: "teams." },
    ];
    serveInbox(changed);
    renderInbox();
    const reworded = await change("Reword a bullet under Engineer, Northwind Traders");
    expect(reworded.getByText("team.").parentElement).toHaveTextContent("Led the team. teams.");
  });

  it("clears an edit's error when the edit is cancelled", async () => {
    serveInbox(inbox(), {
      [`PUT /api/inbox/${CHANGE_SET}/ops/1/text`]: json(
        { detail: "Claude isn't set up to check the text" },
        503,
      ),
    });
    renderInbox();
    const added = await change("Add a bullet under Engineer, Northwind Traders");
    await userEvent.click(added.getByRole("button", { name: "Edit" }));
    await userEvent.click(added.getByRole("button", { name: "Save and check" }));
    expect(await added.findByRole("alert")).toHaveTextContent(
      "Couldn't check your text: Claude isn't set up to check the text.",
    );
    await userEvent.click(added.getByRole("button", { name: "Cancel" }));
    expect(added.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("rejects the changes that need a rejected one with it", async () => {
    const withRole = inbox();
    withRole.changeSets[0]?.ops.push(
      op(3, {
        kind: "add_role",
        title: "Add a role",
        after: "Engineer, Fabrikam (2026-09 - now)",
        outputs: ["master_resume"],
      }),
      op(4, {
        kind: "add_bullet",
        title: "Add a bullet under Engineer, Fabrikam",
        after: "Built a queue.",
        outputs: ["master_resume"],
        editable: true,
        needs: 3,
      }),
    );
    serveInbox(withRole);
    renderInbox();
    const bullet = await change("Add a bullet under Engineer, Fabrikam");
    expect(
      bullet.getByText('Goes with "Add a role": Engineer, Fabrikam (2026-09 - now).'),
    ).toBeInTheDocument();
    const role = await change("Add a role");
    await userEvent.click(role.getByRole("button", { name: "Reject" }));
    await userEvent.click(role.getByRole("button", { name: "Reject" }));
    expect(
      await bullet.findByText(/Rejected with "Add a role", which it needs\./),
    ).toBeInTheDocument();
    expect(bullet.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
    expect(bullet.queryByRole("button", { name: "Undo" })).not.toBeInTheDocument();
    await userEvent.click(role.getByRole("button", { name: "Undo" }));
    expect(await bullet.findByRole("button", { name: "Undo" })).toBeInTheDocument();
    expect(bullet.getByText("Rejected")).toBeInTheDocument();
  });

  it("says what a change without text is about", async () => {
    serveInbox();
    renderInbox();
    const benched = await change("Bench a bullet under Engineer, Northwind Traders");
    expect(benched.getByText('About: "Built order services."')).toBeInTheDocument();
    expect(benched.getByText("Before").nextSibling).toHaveTextContent("active");
    expect(benched.getByText("After").nextSibling).toHaveTextContent("benched: Older");
    expect(benched.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
  });

  it("accepts a change and moves on to the next one waiting", async () => {
    const sent = serveInbox();
    renderInbox();
    const reworded = await change("Reword a bullet under Engineer, Northwind Traders");
    await userEvent.click(reworded.getByRole("button", { name: "Accept" }));
    expect(await reworded.findByText("Accepted")).toBeInTheDocument();
    const added = await change("Add a bullet under Engineer, Northwind Traders");
    await vi.waitFor(() => {
      expect(added.getByRole("button", { name: "Accept" })).toHaveFocus();
    });
    expect(sent).toEqual([
      {
        method: "POST",
        path: `/api/inbox/${CHANGE_SET}/ops/0/decision`,
        body: { status: "accepted", reason: null },
        csrf: "csrf-1",
      },
    ]);
  });

  it("rejects a change with a reason, and undoes it", async () => {
    const sent = serveInbox();
    renderInbox();
    const added = await change("Add a bullet under Engineer, Northwind Traders");
    await userEvent.click(added.getByRole("button", { name: "Reject" }));
    const reason = added.getByRole("textbox", { name: /Why\?/ });
    expect(reason).toHaveFocus();
    await userEvent.type(reason, "Never used Go there");
    await userEvent.click(added.getByRole("button", { name: "Reject" }));
    expect(await added.findByText("Your reason: Never used Go there")).toBeInTheDocument();
    expect(added.getByText("Rejected")).toBeInTheDocument();
    await userEvent.click(added.getByRole("button", { name: "Undo" }));
    expect(await added.findByRole("button", { name: "Accept" })).toBeInTheDocument();
    expect(sent.map((request) => request.body)).toEqual([
      { status: "rejected", reason: "Never used Go there" },
      { status: "proposed", reason: null },
    ]);
  });

  it("edits a bullet's text, which the verifier checks again", async () => {
    const sent = serveInbox();
    renderInbox();
    const added = await change("Add a bullet under Engineer, Northwind Traders");
    await userEvent.click(added.getByRole("button", { name: "Edit" }));
    const text = added.getByRole("textbox", { name: "Bullet text" });
    expect(text).toHaveValue("Wrote the runbook in Go.");
    await userEvent.clear(text);
    await userEvent.type(text, "Wrote the runbook for the nightly export.");
    await userEvent.click(added.getByRole("button", { name: "Save and check" }));
    expect(await added.findByText("Verifier: passed")).toBeInTheDocument();
    expect(added.getByText("Wrote the runbook for the nightly export.")).toBeInTheDocument();
    expect(added.getByText("Waiting")).toBeInTheDocument();
    expect(sent).toEqual([
      {
        method: "PUT",
        path: `/api/inbox/${CHANGE_SET}/ops/1/text`,
        body: { text: "Wrote the runbook for the nightly export." },
        csrf: "csrf-1",
      },
    ]);
  });

  it("accepts every change that passed at once, and leaves the flagged one", async () => {
    const sent = serveInbox();
    renderInbox();
    expect(
      await screen.findByText(/The verifier flagged 1 change: decide on it yourself\./),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Accept all that passed (2)" }));
    expect(await screen.findByText(/2 accepted, 0 rejected, 1 waiting\./)).toBeInTheDocument();
    const added = await change("Add a bullet under Engineer, Northwind Traders");
    expect(added.getByText("Waiting")).toBeInTheDocument();
    expect(sent.map((request) => request.path)).toEqual([`/api/inbox/${CHANGE_SET}/accept-passed`]);
  });

  it("applies the accepted changes once every change is decided", async () => {
    const sent = serveInbox();
    renderInbox();
    const apply = await screen.findByRole("button", { name: "Close without changes" });
    expect(apply).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Accept all that passed (2)" }));
    const added = await change("Add a bullet under Engineer, Northwind Traders");
    await userEvent.click(added.getByRole("button", { name: "Reject" }));
    await userEvent.click(added.getByRole("button", { name: "Reject" }));
    const ready = await screen.findByRole("button", { name: "Apply 2 accepted changes" });
    await vi.waitFor(() => {
      expect(ready).toHaveFocus();
    });
    await userEvent.click(ready);
    expect(
      await screen.findByText("Applied: your profile is now at version 3."),
    ).toBeInTheDocument();
    expect(await screen.findByText("Nothing to review.")).toBeInTheDocument();
    expect(sent.at(-1)?.path).toBe(`/api/inbox/${CHANGE_SET}/apply`);
  });

  it("says why applying didn't work", async () => {
    const decided = inbox();
    for (const item of decided.changeSets[0]?.ops ?? []) {
      item.status = "accepted";
    }
    serveInbox(decided, {
      [`POST /api/inbox/${CHANGE_SET}/apply`]: json(
        { detail: "the changes don't fit your profile any more" },
        409,
      ),
    });
    renderInbox();
    await userEvent.click(await screen.findByRole("button", { name: "Apply 3 accepted changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't apply the changes: the changes don't fit your profile any more.",
    );
  });

  it("says when there's nothing to review", async () => {
    serveInbox({ changeSets: [], profileVersion: 1 });
    renderInbox();
    expect(await screen.findByText("Nothing to review.")).toBeInTheDocument();
  });

  it("writes only ASCII characters", async () => {
    serveInbox();
    renderInbox();
    const added = await change("Add a bullet under Engineer, Northwind Traders");
    await userEvent.click(added.getByRole("button", { name: "Reject" }));
    const nonAscii = document.documentElement.outerHTML.match(/[^\t\n\r\x20-\x7e]/gu) ?? [];
    expect(nonAscii).toEqual([]);
  });
});
