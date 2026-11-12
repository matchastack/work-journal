import { useQuery } from "@tanstack/react-query";
import { useId } from "react";

import { ApiError, failureMessage } from "../../api/auth";
import { draftQuery, useDiscardDraft, useRetryDraft, type TailorDraft } from "../../api/tailor";
import { Findings } from "../resumes/ResumeParts";
import { BUTTON, DANGER_BUTTON } from "../ui";
import { SkillsSection, WordsSection } from "./Choices";
import {
  CoverageSection,
  DownloadSection,
  OutlineSection,
  PostingSection,
  RenderSection,
} from "./DraftParts";

function title(draft: TailorDraft): string {
  const posting = draft.posting;
  if (posting === null) {
    return "A pasted posting";
  }
  return posting.company ? `${posting.title} at ${posting.company}` : posting.title;
}

/** Where the draft's jobs have got to, while they work or when they failed. */
function Progress({ draft }: { draft: TailorDraft }) {
  const retry = useRetryDraft(draft.id);
  switch (draft.status) {
    case "parsing":
      return (
        <p role="status" className="mt-3 text-stone-700">
          Reading the posting...
        </p>
      );
    case "tailoring":
      return (
        <p role="status" className="mt-3 text-stone-700">
          Choosing what your resume shows and wording it in the posting&apos;s language. This takes
          a minute or two.
        </p>
      );
    case "failed":
      return (
        <div role="alert" className="mt-3">
          <p className="text-red-900">{draft.error ?? "It failed."}</p>
          <button
            type="button"
            disabled={retry.isPending}
            onClick={() => {
              retry.mutate(undefined);
            }}
            className={`mt-2 ${BUTTON}`}
          >
            Try again
          </button>
          <p role="status" className="mt-1 text-sm text-red-800 empty:hidden">
            {retry.isError ? failureMessage("try again", retry.error) : null}
          </p>
        </div>
      );
    case "ready":
      return null;
  }
}

function Draft({ draft, onDiscarded }: { draft: TailorDraft; onDiscarded: () => void }) {
  const id = useId();
  const discard = useDiscardDraft(draft.id);
  const ready = draft.status === "ready" && draft.rules !== undefined && draft.rules !== null;
  const locked = draft.application !== undefined && draft.application !== null;
  return (
    <article
      aria-labelledby={`${id}-title`}
      className="mt-4 rounded-lg border border-stone-200 bg-stone-50 p-4"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {title(draft)}
        </h2>
        {draft.application === null || draft.application === undefined ? (
          <button
            type="button"
            disabled={discard.isPending}
            onClick={() => {
              discard.mutate(undefined, { onSuccess: onDiscarded });
            }}
            className={`ml-auto ${DANGER_BUTTON}`}
          >
            Discard draft
          </button>
        ) : null}
      </header>
      {draft.profileVersion !== null && draft.profileVersion !== undefined && (
        <p className="mt-1 text-sm text-stone-600">
          Tailored from version {String(draft.profileVersion)} of your profile
          {draft.roleType ? `, as a ${draft.roleType} resume` : ""}.
        </p>
      )}
      <p role="status" className="mt-1 text-sm text-red-800 empty:hidden">
        {discard.isError ? failureMessage("discard it", discard.error) : null}
      </p>
      <Progress draft={draft} />
      {draft.status === "ready" && draft.error && (
        <p role="alert" className="mt-3 text-red-900">
          {draft.error}
        </p>
      )}
      {draft.posting !== null && (
        <PostingSection id={`${id}-posting`} posting={draft.posting} dropped={draft.droppedTerms} />
      )}
      {ready && (
        <>
          {draft.notes.length > 0 && (
            <section aria-labelledby={`${id}-notes`} className="mt-6">
              <h3 id={`${id}-notes`} className="font-semibold">
                What code changed in the plan
              </h3>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-stone-700">
                {draft.notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </section>
          )}
          <WordsSection draftId={draft.id} bullets={draft.bullets} locked={locked} />
          <SkillsSection draftId={draft.id} skills={draft.skills} locked={locked} />
          <Findings title="Resume rules" report={draft.rules ?? null} missing="Not checked." />
          {draft.coverage && (
            <CoverageSection
              id={`${id}-coverage`}
              coverage={draft.coverage}
              fitted={draft.coverageFitted}
            />
          )}
          <RenderSection id={`${id}-pdf`} draft={draft} />
          <DownloadSection id={`${id}-download`} draft={draft} />
          {draft.outline && <OutlineSection outline={draft.outline} />}
        </>
      )}
    </article>
  );
}

/** One draft, read again while its jobs work (FR-TLR-1 to FR-TLR-9). */
export function DraftView({ draftId, onDiscarded }: { draftId: string; onDiscarded: () => void }) {
  const draft = useQuery(draftQuery(draftId));
  if (draft.isPending) {
    return (
      <p role="status" className="mt-6 text-stone-600">
        Loading the draft...
      </p>
    );
  }
  if (draft.isError) {
    const missing = draft.error instanceof ApiError && draft.error.status === 404;
    return (
      <div role="alert" className="mt-6">
        <p className="text-stone-700">
          {missing
            ? "There's no such draft: it may have been discarded."
            : "The draft didn't load."}
        </p>
        {!missing && (
          <button type="button" onClick={() => void draft.refetch()} className={`mt-3 ${BUTTON}`}>
            Try again
          </button>
        )}
      </div>
    );
  }
  return <Draft draft={draft.data} onDiscarded={onDiscarded} />;
}
