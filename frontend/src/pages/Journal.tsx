import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useLocation } from "react-router";

import { exportUrls, journalQuery, type JournalEntry } from "../api/journal";
import { EntryCard } from "../components/journal/EntryCard";
import { BUTTON, FOCUS } from "../components/ui";
import { monthKey, monthTitle } from "../format";

interface Month {
  key: string;
  entries: JournalEntry[];
}

/** Entries grouped by the month they were opened in, in the browser's time zone. */
function byMonth(entries: readonly JournalEntry[]): Month[] {
  const months: Month[] = [];
  for (const entry of entries) {
    const key = monthKey(entry.openedAt);
    const last = months.at(-1);
    if (last?.key === key) {
      last.entries.push(entry);
    } else {
      months.push({ key, entries: [entry] });
    }
  }
  return months;
}

/**
 * The journal as one document, oldest first and grouped by month, with the facts saved from
 * each entry inline (FR-JRN-5). Entries, messages and facts can be corrected in place (FR-JRN-6),
 * and the whole journal downloads as Markdown or JSON (FR-JRN-7).
 */
export function Journal() {
  const journal = useQuery(journalQuery);
  const { hash } = useLocation();
  const focused = useRef(false);
  useEffect(() => {
    // The bot links to an entry as /journal#<entry id>: show it once the journal is in.
    if (focused.current || journal.data === undefined || hash === "") {
      return;
    }
    focused.current = true;
    document.getElementById(decodeURIComponent(hash.slice(1)))?.focus();
  }, [journal.data, hash]);
  const urls = exportUrls();
  return (
    <section aria-labelledby="journal-title">
      <title>Journal | Work Journal</title>
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 id="journal-title" className="text-2xl font-semibold">
            Journal
          </h1>
          <p className="mt-2 text-stone-600">
            Everything you&apos;ve sent the bot, oldest first, with the facts saved from it.
          </p>
        </div>
        {journal.data !== undefined && journal.data.entries.length > 0 && (
          <div className="flex gap-2">
            <a href={urls.markdown} download className={BUTTON}>
              Download Markdown
            </a>
            <a href={urls.json} download className={BUTTON}>
              Download JSON
            </a>
          </div>
        )}
      </div>
      {journal.isPending ? (
        <p role="status" className="mt-6 text-stone-600">
          Loading your journal...
        </p>
      ) : journal.isError ? (
        <div role="alert" className="mt-6">
          <p className="text-stone-700">Your journal didn&apos;t load.</p>
          <button type="button" onClick={() => void journal.refetch()} className={`mt-3 ${BUTTON}`}>
            Try again
          </button>
        </div>
      ) : journal.data.entries.length === 0 ? (
        <div className="mt-6 rounded-md border border-dashed border-stone-300 p-6 text-stone-600">
          <p>Your journal is empty.</p>
          <p className="mt-1 text-sm">
            Send the bot a note about your work, and it appears here with the facts saved from it.
          </p>
        </div>
      ) : (
        <div className="mt-6 lg:grid lg:grid-cols-[10rem_minmax(0,1fr)] lg:gap-8">
          <nav aria-label="Months" className="mb-6 lg:sticky lg:top-4 lg:mb-0 lg:self-start">
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm lg:block lg:space-y-1">
              {byMonth(journal.data.entries).map((month) => (
                <li key={month.key}>
                  <a
                    href={`#month-${month.key}`}
                    className={`rounded text-emerald-800 underline-offset-2 hover:underline ${FOCUS}`}
                  >
                    {monthTitle(month.key)}
                  </a>{" "}
                  <span className="text-stone-500">({month.entries.length})</span>
                </li>
              ))}
            </ul>
          </nav>
          <div className="max-w-3xl space-y-10">
            {byMonth(journal.data.entries).map((month) => (
              <section key={month.key} aria-labelledby={`month-${month.key}`}>
                <h2
                  id={`month-${month.key}`}
                  className="scroll-mt-4 border-b border-stone-200 pb-1 text-lg font-semibold"
                >
                  {monthTitle(month.key)}
                </h2>
                <div className="mt-4 space-y-4">
                  {month.entries.map((entry) => (
                    <EntryCard
                      key={entry.id}
                      entry={entry}
                      roles={journal.data.roles}
                      projects={journal.data.projects}
                    />
                  ))}
                </div>
              </section>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}
