import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";

import { tailorPageQuery } from "../api/tailor";
import { DraftView } from "../components/tailor/DraftView";
import { ApplicationHistory, DraftList } from "../components/tailor/Lists";
import { PostingForm } from "../components/tailor/PostingForm";
import { BUTTON } from "../components/ui";

/**
 * Paste a job posting to see its parsed requirements, then a tailored draft to adjust: choose
 * each reworded bullet's words, approve new phrasings (saved as swaps), and confirm or drop the
 * skills marked verify-before-shipping. Downloading the PDF logs the application, which the
 * history lists. The hosted app can't run LaTeX (C6): the page says when a draft's PDF waits for
 * a computer with TeX Live, and which command renders it.
 */
export function Tailor() {
  const page = useQuery(tailorPageQuery);
  const [params, setParams] = useSearchParams();
  const selected = params.get("draft");
  const select = (draftId: string | null) => {
    setParams(draftId === null ? {} : { draft: draftId });
  };
  return (
    <section aria-labelledby="tailor-title" className="max-w-4xl">
      <title>Tailor | Work Journal</title>
      <h1 id="tailor-title" className="text-2xl font-semibold">
        Tailor
      </h1>
      <p className="mt-2 text-stone-600">
        Tailor a one-page resume to a job posting, adjust it, and download it. Each download is
        logged as an application.
      </p>
      <PostingForm onCreated={select} />
      {selected !== null && (
        <DraftView
          key={selected}
          draftId={selected}
          onDiscarded={() => {
            select(null);
          }}
        />
      )}
      {page.isPending ? (
        <p role="status" className="mt-6 text-stone-600">
          Loading your drafts and applications...
        </p>
      ) : page.isError ? (
        <div role="alert" className="mt-6">
          <p className="text-stone-700">Your drafts and applications didn&apos;t load.</p>
          <button type="button" onClick={() => void page.refetch()} className={`mt-3 ${BUTTON}`}>
            Try again
          </button>
        </div>
      ) : (
        <>
          <DraftList drafts={page.data.drafts} selected={selected} />
          <ApplicationHistory applications={page.data.applications} />
        </>
      )}
    </section>
  );
}
