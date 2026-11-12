import { useQuery } from "@tanstack/react-query";

import { portfolioQuery } from "../api/portfolio";
import { PreviewSection } from "../components/portfolio/PreviewSection";
import { PublishingSection } from "../components/portfolio/PublishingSection";
import { ResumesSection } from "../components/portfolio/ResumesSection";
import { BUTTON } from "../components/ui";

/**
 * The public portfolio page (FR-PRT-1, FR-PRT-3, FR-PRT-7): preview it, choose the resumes
 * visitors can download, and publish or unpublish it. Its handle, open to work, indexing and
 * default visibility are set on the Settings page (FR-SET-3).
 */
export function Portfolio() {
  const portfolio = useQuery(portfolioQuery);
  return (
    <section aria-labelledby="portfolio-title">
      <title>Portfolio | Work Journal</title>
      <h1 id="portfolio-title" className="text-2xl font-semibold">
        Portfolio
      </h1>
      <p className="mt-2 text-stone-600">
        Your public page. Preview it, choose the resumes visitors can download, and publish it when
        you&apos;re ready.
      </p>
      {portfolio.isPending ? (
        <p role="status" className="mt-6 text-stone-600">
          Loading your portfolio page...
        </p>
      ) : portfolio.isError ? (
        <div role="alert" className="mt-6">
          <p className="text-stone-700">Your portfolio page didn&apos;t load.</p>
          <button
            type="button"
            onClick={() => void portfolio.refetch()}
            className={`mt-3 ${BUTTON}`}
          >
            Try again
          </button>
        </div>
      ) : (
        <div className="mt-6 space-y-6">
          <div className="max-w-3xl space-y-6">
            <PublishingSection view={portfolio.data} />
            <ResumesSection view={portfolio.data} />
          </div>
          <div className="max-w-5xl">
            <PreviewSection view={portfolio.data} version={portfolio.dataUpdatedAt} />
          </div>
        </div>
      )}
    </section>
  );
}
