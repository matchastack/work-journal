import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { linkedinQuery, useMarkDone, type LinkedInPage, type Marked } from "../api/linkedin";
import { Checklist } from "../components/linkedin/Checklist";
import { BUTTON } from "../components/ui";
import { dateAndTime } from "../format";

function plural(count: number, one: string): string {
  return `${String(count)} ${one}${count === 1 ? "" : "s"}`;
}

function markedMessage({ flagged }: Marked): string {
  const done = "Marked done. This page now lists only what changes after this.";
  if (flagged.length === 0) {
    return done;
  }
  const them = flagged.length === 1 ? "it" : "them";
  return `${done} Not marked, as the verifier flagged ${them}: ${flagged.join(", ")}. Written again next time.`;
}

/** Where the listed sections stand: how many changed since the pack was last marked done, and
 * whether the profile has moved on since the pack was written. */
function Summary({ page }: { page: LinkedInPage }) {
  const pack = page.pack;
  if (pack === null) {
    return null;
  }
  let since = "You haven't marked a pack done yet, so every section is listed.";
  if (page.markedAt !== null && pack.sections.length === 0) {
    since = `You last marked the pack done on ${dateAndTime(page.markedAt)}.`;
  } else if (page.markedAt !== null) {
    since = `${plural(pack.sections.length, "section")} changed since you marked the pack done on ${dateAndTime(page.markedAt)}.`;
    if (pack.unchanged > 0) {
      since += ` ${plural(pack.unchanged, "section")} unchanged, not shown.`;
    }
  }
  const stale =
    pack.profileVersion !== null &&
    page.profileVersion !== null &&
    pack.profileVersion !== page.profileVersion;
  return (
    <div className="mt-4 space-y-1 text-sm text-stone-600">
      <p>{since}</p>
      {stale && (
        <p>
          Written from version {pack.profileVersion} of your profile, which is now at version{" "}
          {page.profileVersion}: it may miss your latest changes.
        </p>
      )}
    </div>
  );
}

/**
 * The LinkedIn pack as a checklist (FR-LIN-2, FR-LIN-3): the sections that changed since the
 * pack was last marked done, each to copy into LinkedIn, then "Mark done" to store the snapshot.
 * Work Journal never posts to LinkedIn: its API is closed to apps like this one (C1).
 */
export function LinkedIn() {
  const page = useQuery(linkedinQuery);
  const markDone = useMarkDone();
  const marked = markDone.data;
  const status = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    // Marking may leave nothing to list, taking the focused button with it.
    if (marked !== undefined) {
      status.current?.focus();
    }
  }, [marked]);
  const pack = page.data?.pack;
  return (
    <section aria-labelledby="linkedin-title">
      <title>LinkedIn | Work Journal</title>
      <h1 id="linkedin-title" className="text-2xl font-semibold">
        LinkedIn
      </h1>
      <p className="mt-2 text-stone-600">
        Copy the sections that changed into your LinkedIn profile, then mark them done.
      </p>
      <p
        ref={status}
        role="status"
        tabIndex={-1}
        className="mt-4 text-sm text-emerald-800 empty:hidden focus:outline-none"
      >
        {marked === undefined ? null : markedMessage(marked)}
      </p>
      {page.isPending ? (
        <p role="status" className="mt-6 text-stone-600">
          Loading your LinkedIn text...
        </p>
      ) : page.isError ? (
        <div role="alert" className="mt-6">
          <p className="text-stone-700">Your LinkedIn text didn&apos;t load.</p>
          <button type="button" onClick={() => void page.refetch()} className={`mt-3 ${BUTTON}`}>
            Try again
          </button>
        </div>
      ) : pack === null || pack === undefined ? (
        <div className="mt-6 rounded-md border border-dashed border-stone-300 p-6 text-stone-600">
          <p>No LinkedIn text yet.</p>
          <p className="mt-1 text-sm">
            It&apos;s written from your profile by <code>wj linkedin --db</code> on your computer.
            Its sections then appear here, ready to paste.
          </p>
        </div>
      ) : pack.sections.length === 0 && pack.removed.length === 0 ? (
        <div className="mt-6 rounded-md border border-dashed border-stone-300 p-6 text-stone-600">
          <p>Nothing to update: LinkedIn has everything you last marked done.</p>
          <Summary page={page.data} />
        </div>
      ) : (
        <>
          <Summary page={page.data} />
          <Checklist pack={pack} markDone={markDone} />
        </>
      )}
    </section>
  );
}
