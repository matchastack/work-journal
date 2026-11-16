import { useId } from "react";

import type { JournalFact, ProjectOption, RepositoryFacts } from "../../api/journal";
import { monthTitle } from "../../format";
import { FOCUS } from "../ui";
import { FactItem } from "./FactItem";

interface Month {
  /** `YYYY-MM`, or empty for the facts without a date. */
  key: string;
  facts: JournalFact[];
}

/** A repository's facts by month, as they come: newest first, the undated last. */
function byMonth(facts: readonly JournalFact[]): Month[] {
  const months: Month[] = [];
  for (const fact of facts) {
    const key = fact.date ?? "";
    const last = months.at(-1);
    if (last?.key === key) {
      last.facts.push(fact);
    } else {
      months.push({ key, facts: [fact] });
    }
  }
  return months;
}

function Repository({
  repository,
  projects,
}: {
  repository: RepositoryFacts;
  projects: readonly ProjectOption[];
}) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <h3 id={id} className="font-medium">
        <a
          href={repository.url}
          rel="noreferrer"
          className={`rounded text-emerald-800 underline underline-offset-2 ${FOCUS}`}
        >
          {repository.repository}
        </a>
        {repository.private === true && (
          <span className="ml-2 text-sm font-normal text-stone-500">(private)</span>
        )}
      </h3>
      {repository.facts.length === 0 ? (
        <p className="mt-1 text-sm text-stone-600">No facts from it yet.</p>
      ) : (
        byMonth(repository.facts).map((month) => (
          <div key={month.key} className="mt-3">
            <h4 className="text-sm font-medium text-stone-700">
              {month.key ? monthTitle(month.key) : "No date"}
            </h4>
            <ul className="mt-2 space-y-2">
              {month.facts.map((fact) => (
                <FactItem key={fact.id} fact={fact} entry={null} roles={[]} projects={projects} />
              ))}
            </ul>
          </div>
        ))
      )}
    </section>
  );
}

/**
 * The facts read from the linked GitHub repositories (FR-GH-10): each repository, and its facts
 * by month, with links to the pull requests and commits they rest on. They're edited and
 * deleted as the journal's are (FR-EXT-3).
 */
export function GitHubFacts({
  repositories,
  projects,
}: {
  repositories: readonly RepositoryFacts[];
  projects: readonly ProjectOption[];
}) {
  if (repositories.length === 0) {
    return null;
  }
  return (
    <section aria-labelledby="github-title" className="mt-10 max-w-3xl">
      <h2
        id="github-title"
        className="scroll-mt-4 border-b border-stone-200 pb-1 text-lg font-semibold"
      >
        From GitHub
      </h2>
      <p className="mt-2 text-sm text-stone-600">
        The facts read from your linked repositories, newest first.
      </p>
      <div className="mt-4 space-y-8">
        {repositories.map((repository) => (
          <Repository key={repository.repository} repository={repository} projects={projects} />
        ))}
      </div>
    </section>
  );
}
