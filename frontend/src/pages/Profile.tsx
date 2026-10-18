import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { useMe } from "../api/auth";
import { profileQuery, type LintView, type Profile as ProfileData } from "../api/profile";
import { BasicsCard } from "../components/profile/BasicsCard";
import { ItemCard } from "../components/profile/ItemCard";
import { SAVED_BY, type Entry } from "../components/profile/labels";
import { LintSummary } from "../components/profile/LintSummary";
import { VersionHistory } from "../components/profile/VersionHistory";
import { BUTTON, FOCUS } from "../components/ui";
import { dateAndTime } from "../format";

/** The page's sections, in order, for the headings and the links to them. */
const SECTIONS = {
  basics: { id: "profile-basics", title: "Basics" },
  work: { id: "profile-work", title: "Work" },
  education: { id: "profile-education", title: "Education" },
  projects: { id: "profile-projects", title: "Projects" },
  rules: { id: "profile-rules", title: "Resume rules" },
  history: { id: "profile-history", title: "History" },
} as const;

function Section({
  section,
  children,
}: {
  section: (typeof SECTIONS)[keyof typeof SECTIONS];
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={section.id}>
      <h2
        id={section.id}
        className="scroll-mt-4 border-b border-stone-200 pb-1 text-lg font-semibold"
      >
        {section.title}
      </h2>
      <div className="mt-4 space-y-4">{children}</div>
    </section>
  );
}

/** The cards of one part of the profile, or what to do while it has none. */
function Items({
  entries,
  none,
  ...props
}: {
  entries: Entry[];
  none: string;
  profile: ProfileData;
  lint: LintView;
  baseVersion: number;
  onSaved: (message: string) => void;
}) {
  if (entries.length === 0) {
    return <p className="text-sm text-stone-600">{none}</p>;
  }
  return entries.map((entry) => (
    <ItemCard
      key={entry.item.id}
      entry={entry}
      findings={props.lint.findings}
      roleTypes={props.profile.roleTypes}
      baseVersion={props.baseVersion}
      onSaved={props.onSaved}
    />
  ));
}

/**
 * The master profile's latest version: basics, roles, education, projects and their bullets,
 * each edited in place, with the resume rules' findings. Every save is a change set by you and
 * a new version (FR-PRF-11); the resumes, portfolio page and LinkedIn text catch up at the next
 * refresh (FR-REV-7). The history lists every version with what it changed, and restores any of
 * them as a new version (FR-PRF-3).
 */
export function Profile() {
  const page = useQuery(profileQuery);
  const me = useMe();
  const [status, setStatus] = useState<string | null>(null);
  const data = page.data;
  return (
    <section aria-labelledby="profile-title">
      <title>Profile | Work Journal</title>
      <h1 id="profile-title" className="text-2xl font-semibold">
        Profile
      </h1>
      <p className="mt-2 text-stone-600">
        Your master profile, which your resumes, portfolio page and LinkedIn text are made from.
        Each save makes a new version, and they catch up at your next /refresh.
      </p>
      <p role="status" className="mt-4 text-sm text-emerald-800 empty:hidden">
        {status}
      </p>
      {page.isPending ? (
        <p role="status" className="mt-6 text-stone-600">
          Loading your profile...
        </p>
      ) : page.isError ? (
        <div role="alert" className="mt-6">
          <p className="text-stone-700">Your profile didn&apos;t load.</p>
          <button type="button" onClick={() => void page.refetch()} className={`mt-3 ${BUTTON}`}>
            Try again
          </button>
        </div>
      ) : !data?.profile || data.version === null || data.lint === null ? (
        <div className="mt-6 rounded-md border border-dashed border-stone-300 p-6 text-stone-600">
          <p>No profile yet.</p>
          <p className="mt-1 text-sm">
            Load your master resume with{" "}
            <code className="rounded bg-stone-100 px-1">
              uv run wj db load-profile --user {me.data?.githubLogin ?? "<github-login>"}
            </code>
            .
          </p>
        </div>
      ) : (
        <div className="mt-6 lg:grid lg:grid-cols-[10rem_minmax(0,1fr)] lg:gap-8">
          <nav aria-label="Sections" className="mb-6 lg:sticky lg:top-4 lg:mb-0 lg:self-start">
            <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm lg:block lg:space-y-1">
              {Object.values(SECTIONS).map((section) => (
                <li key={section.id}>
                  <a
                    href={`#${section.id}`}
                    className={`rounded text-emerald-800 underline-offset-2 hover:underline ${FOCUS}`}
                  >
                    {section.title}
                  </a>
                </li>
              ))}
            </ul>
          </nav>
          <div className="max-w-3xl space-y-10">
            <p className="text-sm text-stone-600">
              Showing version {data.version.number}, saved {SAVED_BY[data.version.author]} on{" "}
              {dateAndTime(data.version.createdAt)}.
            </p>
            <Section section={SECTIONS.basics}>
              <BasicsCard
                basics={data.profile.basics}
                findings={data.lint.findings}
                baseVersion={data.version.number}
                onSaved={setStatus}
              />
            </Section>
            <Section section={SECTIONS.work}>
              <Items
                entries={data.profile.work.map((item) => ({ section: "work", item }))}
                none="No roles yet."
                profile={data.profile}
                lint={data.lint}
                baseVersion={data.version.number}
                onSaved={setStatus}
              />
            </Section>
            <Section section={SECTIONS.education}>
              <Items
                entries={data.profile.education.map((item) => ({ section: "education", item }))}
                none="No education yet."
                profile={data.profile}
                lint={data.lint}
                baseVersion={data.version.number}
                onSaved={setStatus}
              />
            </Section>
            <Section section={SECTIONS.projects}>
              <Items
                entries={data.profile.projects.map((item) => ({ section: "projects", item }))}
                none="No projects yet."
                profile={data.profile}
                lint={data.lint}
                baseVersion={data.version.number}
                onSaved={setStatus}
              />
            </Section>
            <LintSummary lint={data.lint} titleId={SECTIONS.rules.id} />
            <VersionHistory
              current={data.version.number}
              titleId={SECTIONS.history.id}
              onRestored={setStatus}
            />
          </div>
        </div>
      )}
    </section>
  );
}
