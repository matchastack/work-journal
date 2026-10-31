import { useQuery } from "@tanstack/react-query";

import { resumesQuery, type Resumes as ResumeList } from "../api/resumes";
import { MasterCard } from "../components/resumes/MasterCard";
import { Command } from "../components/resumes/ResumeParts";
import { TailoredCard } from "../components/resumes/TailoredCard";
import { BUTTON } from "../components/ui";

const EMPTY = "mt-4 rounded-md border border-dashed border-stone-300 p-6 text-stone-600";

function MasterSection({ resumes }: { resumes: ResumeList }) {
  const { master, lastRendered, profileVersion } = resumes;
  return (
    <section aria-labelledby="master-title" className="mt-8">
      <h2 id="master-title" className="text-lg font-semibold">
        Master resume
      </h2>
      <p className="mt-1 text-sm text-stone-600">
        Your whole profile on as many pages as it takes, for review. It&apos;s rendered again after
        each refresh.
      </p>
      {master === null ? (
        <div className={EMPTY}>
          <p>No master resume yet.</p>
          <p className="mt-1 text-sm">
            Send /refresh to the bot and apply its changes in the Inbox. Then run{" "}
            <Command>uv run wj tick</Command> on your computer, where TeX Live is installed, to
            render it.
          </p>
        </div>
      ) : (
        <div className="mt-4 space-y-4">
          {profileVersion !== null && profileVersion > master.version && (
            <p className="text-sm text-stone-700">
              Your profile is at version {String(profileVersion)}, newer than this resume. Send
              /refresh to the bot to bring it up to date.
            </p>
          )}
          <MasterCard master={master} title="The master resume" />
          {lastRendered !== null && (
            <MasterCard
              master={lastRendered}
              title={`The last one rendered: version ${String(lastRendered.version)}`}
            />
          )}
        </div>
      )}
    </section>
  );
}

function TailoredSection({ resumes }: { resumes: ResumeList }) {
  return (
    <section aria-labelledby="tailored-title" className="mt-10">
      <h2 id="tailored-title" className="text-lg font-semibold">
        Tailored resumes
      </h2>
      <p className="mt-1 text-sm text-stone-600">
        One page each, made for a job posting, newest first. Made once, they never change.
      </p>
      {resumes.tailored.length === 0 ? (
        <div className={EMPTY}>
          <p>No tailored resumes yet.</p>
          <p className="mt-1 text-sm">
            Run <Command>uv run wj tailor &lt;posting-file&gt; --db</Command> on your computer; each
            resume it makes appears here.
          </p>
        </div>
      ) : (
        <ul className="mt-4 space-y-4">
          {resumes.tailored.map((resume) => (
            <TailoredCard key={resume.id} resume={resume} />
          ))}
        </ul>
      )}
    </section>
  );
}

/**
 * The master resume from the latest refresh (FR-RES-5, FR-RES-6) and every tailored resume, each
 * with a preview of its PDF, a download, its page count, what was cut to fit and what the
 * resume rules found. The hosted app can't run LaTeX (C6): it shows what was rendered on the
 * owner's computer, and says which command renders what's missing.
 */
export function Resumes() {
  const resumes = useQuery(resumesQuery);
  return (
    <section aria-labelledby="resumes-title" className="max-w-4xl">
      <title>Resumes | Work Journal</title>
      <h1 id="resumes-title" className="text-2xl font-semibold">
        Resumes
      </h1>
      <p className="mt-2 text-stone-600">
        Preview and download your master resume and the resumes tailored to job postings.
      </p>
      {resumes.isPending ? (
        <p role="status" className="mt-6 text-stone-600">
          Loading your resumes...
        </p>
      ) : resumes.isError ? (
        <div role="alert" className="mt-6">
          <p className="text-stone-700">Your resumes didn&apos;t load.</p>
          <button type="button" onClick={() => void resumes.refetch()} className={`mt-3 ${BUTTON}`}>
            Try again
          </button>
        </div>
      ) : (
        <>
          <MasterSection resumes={resumes.data} />
          <TailoredSection resumes={resumes.data} />
        </>
      )}
    </section>
  );
}
