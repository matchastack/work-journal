import { useState } from "react";

import {
  useDeleteEntry,
  type Citation,
  type JournalEntry,
  type ProjectOption,
  type RoleOption,
} from "../../api/journal";
import { dayAndTime } from "../../format";
import { TEXT_BUTTON } from "../ui";
import { ConfirmDelete } from "./ConfirmDelete";
import { FactItem } from "./FactItem";
import { roleText } from "./labels";
import { MessageItem } from "./MessageItem";

/** Where the entry is in capture, when that's worth saying. */
function statusText(entry: JournalEntry): string | null {
  switch (entry.status) {
    case "open":
      return "Open: your next message joins it";
    case "waiting_for_role":
      return "Choose its role in Telegram";
    case "extracting":
      return "Finding its facts";
    case "failed":
      return "The bot couldn't read it";
    case "processed":
      return entry.triage === "other" ? "Not about work" : null;
  }
}

function plural(count: number, one: string, many: string): string {
  return `${String(count)} ${count === 1 ? one : many}`;
}

/** One journal entry: its messages, then the facts saved from it. The bot's reply links here
 * (`/journal#<entry id>`), so it can take focus. */
export function EntryCard({
  entry,
  roles,
  projects,
}: {
  entry: JournalEntry;
  roles: readonly RoleOption[];
  projects: readonly ProjectOption[];
}) {
  const [deleting, setDeleting] = useState(false);
  const remove = useDeleteEntry();
  const titleId = `${entry.id}-title`;
  const status = statusText(entry);
  const role = roleText(entry, roles);
  const opened = dayAndTime(entry.openedAt);
  const cited = new Map<string, Citation>();
  for (const fact of entry.facts) {
    for (const citation of fact.citedBy) {
      cited.set(citation.bulletId, citation);
    }
  }
  return (
    <article
      id={entry.id}
      tabIndex={-1}
      aria-labelledby={titleId}
      className="scroll-mt-4 rounded-lg border border-stone-200 bg-white p-4 shadow-sm focus:outline-2 focus:outline-offset-2 focus:outline-emerald-700"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 id={titleId} className="font-medium">
          {opened}
        </h3>
        {status !== null && (
          <span className="rounded-full bg-amber-50 px-2 py-0.5 text-xs text-amber-900 ring-1 ring-amber-200">
            {status}
          </span>
        )}
        {role !== null && <p className="text-sm text-stone-600">{role}</p>}
      </header>
      <div className="mt-3 space-y-3">
        {entry.messages.map((message) => (
          <MessageItem key={message.id} message={message} deletable={entry.messages.length > 1} />
        ))}
      </div>
      {entry.facts.length > 0 ? (
        <section aria-labelledby={`${entry.id}-facts`} className="mt-4">
          <h4 id={`${entry.id}-facts`} className="text-sm font-medium text-stone-700">
            Facts
          </h4>
          <ul className="mt-2 space-y-2">
            {entry.facts.map((fact) => (
              <FactItem key={fact.id} fact={fact} entry={entry} roles={roles} projects={projects} />
            ))}
          </ul>
        </section>
      ) : (
        entry.status === "processed" &&
        entry.triage === "work" && (
          <p className="mt-4 text-sm text-stone-600">The bot found no facts in this entry.</p>
        )
      )}
      <div className="mt-4 border-t border-stone-100 pt-3">
        {deleting ? (
          <ConfirmDelete
            question={`Delete this entry, its ${plural(entry.messages.length, "message", "messages")} and ${plural(entry.facts.length, "fact", "facts")}? This can't be undone.`}
            citations={[...cited.values()]}
            confirmLabel="Delete this entry"
            pending={remove.isPending}
            error={remove.error}
            onConfirm={() => {
              remove.mutate({ id: entry.id, confirm: cited.size > 0 });
            }}
            onCancel={() => {
              remove.reset();
              setDeleting(false);
            }}
          />
        ) : (
          <button
            type="button"
            aria-label={`Delete entry from ${opened}`}
            onClick={() => {
              setDeleting(true);
            }}
            className={TEXT_BUTTON}
          >
            Delete entry
          </button>
        )}
      </div>
    </article>
  );
}
