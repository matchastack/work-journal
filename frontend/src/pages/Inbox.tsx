import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { inboxQuery } from "../api/inbox";
import { ChangeSetReview } from "../components/inbox/ChangeSetReview";
import { BUTTON } from "../components/ui";

/**
 * The change sets proposed to the profile, to review one change at a time (FR-REV-1, FR-REV-2)
 * and apply as a new version (FR-REV-3). Nothing changes in the profile until they're applied.
 */
export function Inbox() {
  const inbox = useQuery(inboxQuery);
  const [applied, setApplied] = useState<string | null>(null);
  return (
    <section aria-labelledby="inbox-title">
      <title>Inbox | Work Journal</title>
      <h1 id="inbox-title" className="text-2xl font-semibold">
        Inbox
      </h1>
      <p className="mt-2 text-stone-600">
        Review the changes proposed to your profile. Nothing changes until you apply them.
      </p>
      <p role="status" className="mt-4 text-sm text-emerald-800 empty:hidden">
        {applied}
      </p>
      {inbox.isPending ? (
        <p role="status" className="mt-6 text-stone-600">
          Loading your inbox...
        </p>
      ) : inbox.isError ? (
        <div role="alert" className="mt-6">
          <p className="text-stone-700">Your inbox didn&apos;t load.</p>
          <button type="button" onClick={() => void inbox.refetch()} className={`mt-3 ${BUTTON}`}>
            Try again
          </button>
        </div>
      ) : inbox.data.changeSets.length === 0 ? (
        <div className="mt-6 rounded-md border border-dashed border-stone-300 p-6 text-stone-600">
          <p>Nothing to review.</p>
          <p className="mt-1 text-sm">
            When you send /refresh to the bot, the changes it proposes from your journal appear
            here.
          </p>
        </div>
      ) : (
        <div className="max-w-3xl">
          {inbox.data.changeSets.map((changeSet) => (
            <ChangeSetReview
              key={changeSet.id}
              changeSet={changeSet}
              profileVersion={inbox.data.profileVersion}
              onApplied={(version) => {
                setApplied(
                  version === null
                    ? "Closed without changes: your profile stays as it was."
                    : `Applied: your profile is now at version ${String(version)}.`,
                );
              }}
            />
          ))}
        </div>
      )}
    </section>
  );
}
