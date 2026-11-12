import { Link } from "react-router";

import type { TailoredResume } from "../../api/resumes";
import type { DraftSummary } from "../../api/tailor";
import { calendarDate, dateAndTime } from "../../format";
import { Badge, Command, DownloadLink } from "../resumes/ResumeParts";
import { FOCUS } from "../ui";

const EMPTY = "mt-3 rounded-md border border-dashed border-stone-300 p-4 text-sm text-stone-600";

function draftTitle(draft: DraftSummary): string {
  if (draft.title === null) {
    return "A pasted posting";
  }
  return draft.company ? `${draft.title} at ${draft.company}` : draft.title;
}

function DraftBadge({ draft }: { draft: DraftSummary }) {
  switch (draft.status) {
    case "parsing":
      return <Badge tone="waiting">Reading the posting</Badge>;
    case "tailoring":
      return <Badge tone="waiting">Tailoring</Badge>;
    case "failed":
      return <Badge tone="bad">Failed</Badge>;
    case "ready":
      return draft.renderStatus === "waiting" ? (
        <Badge tone="waiting">Waiting for its PDF</Badge>
      ) : (
        <Badge tone="good">Ready</Badge>
      );
  }
}

/** The drafts not yet downloaded, newest first; each opens on the page. */
export function DraftList({
  drafts,
  selected,
}: {
  drafts: readonly DraftSummary[];
  selected: string | null;
}) {
  return (
    <section aria-labelledby="drafts-title" className="mt-10">
      <h2 id="drafts-title" className="text-lg font-semibold">
        Drafts
      </h2>
      <p className="mt-1 text-sm text-stone-600">
        Resumes tailored to a posting, not downloaded yet, newest first.
      </p>
      {drafts.length === 0 ? (
        <p className={EMPTY}>No drafts. Paste a posting above to start one.</p>
      ) : (
        <ul className="mt-3 divide-y divide-stone-200 rounded-md border border-stone-200 bg-white">
          {drafts.map((draft) => (
            <li key={draft.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <Link
                to={`/tailor?draft=${encodeURIComponent(draft.id)}`}
                aria-current={draft.id === selected ? "true" : undefined}
                className={`rounded font-medium text-emerald-800 underline-offset-2 hover:underline aria-[current]:text-stone-900 ${FOCUS}`}
              >
                {draftTitle(draft)}
              </Link>
              <DraftBadge draft={draft} />
              <span className="ml-auto text-xs text-stone-600">{dateAndTime(draft.createdAt)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/** Where you've applied: each logged application, with the posting, the date and its PDF
 * (FR-TLR-8). */
export function ApplicationHistory({ applications }: { applications: readonly TailoredResume[] }) {
  return (
    <section aria-labelledby="history-title" className="mt-10">
      <h2 id="history-title" className="text-lg font-semibold">
        Applications
      </h2>
      <p className="mt-1 text-sm text-stone-600">
        Each resume you downloaded here or made with <Command>wj tailor --db</Command>, newest
        first.
      </p>
      {applications.length === 0 ? (
        <p className={EMPTY}>No applications yet. Downloading a draft&apos;s PDF logs one.</p>
      ) : (
        <ul className="mt-3 divide-y divide-stone-200 rounded-md border border-stone-200 bg-white">
          {applications.map((application) => {
            const title = `${application.position} at ${application.company}`;
            return (
              <li
                key={application.id}
                className="flex flex-wrap items-center gap-x-3 gap-y-2 px-3 py-2"
              >
                <div>
                  <p className="font-medium">{title}</p>
                  <p className="text-xs text-stone-600">
                    {calendarDate(application.appliedOn)}
                    <span className="text-stone-400"> | </span>
                    <span className="font-mono">{application.id}</span>
                  </p>
                </div>
                {application.rules !== null && !application.sendable && (
                  <Badge tone="bad">Don&apos;t send</Badge>
                )}
                <div className="ml-auto">
                  {application.pdf === null ? (
                    <span className="text-sm text-stone-600">No PDF kept</span>
                  ) : (
                    <DownloadLink pdf={application.pdf} label={`Download the PDF for ${title}`} />
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
