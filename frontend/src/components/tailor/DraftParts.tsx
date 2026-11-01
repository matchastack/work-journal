import type { ReactNode } from "react";

import { failureMessage } from "../../api/auth";
import {
  applicationPdfUrl,
  draftPdfUrl,
  useDownload,
  type AtsReport,
  type CoverageReport,
  type DraftOutline,
  type JobPosting,
  type TailorDraft,
} from "../../api/tailor";
import { calendarDate } from "../../format";
import { Command, CutReport, PageBadge, PdfWarnings } from "../resumes/ResumeParts";
import { BUTTON, FOCUS, PRIMARY_BUTTON } from "../ui";

const KINDS = { must_have: "must-have", nice_to_have: "nice-to-have", key_term: "key term" };

function Section({ id, title, children }: { id: string; title: string; children: ReactNode }) {
  return (
    <section aria-labelledby={id} className="mt-6">
      <h3 id={id} className="font-semibold">
        {title}
      </h3>
      {children}
    </section>
  );
}

function Terms({ label, terms }: { label: string; terms: readonly string[] }) {
  if (terms.length === 0) {
    return null;
  }
  return (
    <div className="mt-2">
      <dt className="text-xs font-medium text-stone-600">{label}</dt>
      <dd className="text-sm text-stone-800">{terms.join(", ")}</dd>
    </div>
  );
}

/** The posting's requirements, as parsed (FR-TLR-1), with its exact spellings. */
export function PostingSection({
  id,
  posting,
  dropped,
}: {
  id: string;
  posting: JobPosting;
  dropped: readonly string[];
}) {
  return (
    <Section id={id} title="The posting's requirements">
      <dl>
        <Terms label="Seniority" terms={posting.seniority ? [posting.seniority] : []} />
        <Terms label="Must-have" terms={posting.mustHave} />
        <Terms label="Nice-to-have" terms={posting.niceToHave} />
        <Terms label="Key terms" terms={posting.keyTerms} />
      </dl>
      {posting.responsibilities.length > 0 && (
        <>
          <p className="mt-2 text-xs font-medium text-stone-600">Responsibilities</p>
          <ul className="list-disc pl-5 text-sm text-stone-800">
            {posting.responsibilities.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </>
      )}
      {dropped.length > 0 && (
        <p className="mt-2 text-xs text-stone-600">
          Left out, since the posting doesn&apos;t contain them: {dropped.join(", ")}.
        </p>
      )}
    </Section>
  );
}

/** Which of the posting's terms the resume shows, and the gaps, which are never added
 * (FR-TLR-6). */
export function CoverageSection({
  id,
  coverage,
  fitted,
}: {
  id: string;
  coverage: CoverageReport;
  fitted: boolean;
}) {
  const total = coverage.covered.length + coverage.missing.length;
  return (
    <Section id={id} title="Keyword coverage">
      <p className="mt-1 text-sm text-stone-600">
        {total === 0
          ? "The posting names no skills or key terms to cover."
          : `Covers ${String(coverage.covered.length)} of the posting's ${String(total)} skills and key terms${
              fitted ? "" : ", before the resume is fitted to one page"
            }.`}
      </p>
      {coverage.covered.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-sm text-stone-700">
          {coverage.covered.map((entry) => (
            <li key={entry.term}>
              <span className="text-emerald-800">{entry.term}</span> ({KINDS[entry.kind]}):{" "}
              <span className="text-xs text-stone-600">{entry.locations.join(", ")}</span>
            </li>
          ))}
        </ul>
      )}
      {coverage.missing.length > 0 && (
        <p className="mt-2 text-sm text-stone-700">
          Gaps, which are never added: {coverage.missing.map((entry) => entry.term).join(", ")}
        </p>
      )}
    </Section>
  );
}

/** The ATS check of the PDF's text (FR-TLR-9). */
function AtsCheck({ ats }: { ats: AtsReport }) {
  return (
    <div className="mt-4 text-sm">
      <h4 className="font-medium">ATS check</h4>
      {ats.findings.length === 0 ? (
        <p className="mt-1 text-emerald-800">
          Passed: the PDF&apos;s text has its {String(ats.terms)} covered terms and its{" "}
          {String(ats.sections.length)} sections, in order.
        </p>
      ) : (
        <ul className="mt-1 space-y-0.5 text-amber-800">
          {ats.findings.map((finding, index) => (
            <li key={index}>{finding.message}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The PDF of the resume your choices make: waiting for a computer with TeX Live (C6),
 * rendered, or failed. */
export function RenderSection({ id, draft }: { id: string; draft: TailorDraft }) {
  const render = draft.render ?? null;
  return (
    <Section id={id} title="PDF">
      {render === null ? (
        <p className="mt-1 text-sm text-stone-600">Not rendered.</p>
      ) : render.status === "waiting" ? (
        <div role="status" className="mt-2 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          <p className="font-medium">Waiting for a computer with TeX Live to render it.</p>
          <p className="mt-1">
            The hosted app can&apos;t run LaTeX. On your computer, where TeX Live is installed, run{" "}
            <Command>uv run wj worker</Command> (it renders each change as you make it) or{" "}
            <Command>uv run wj tick</Command> once. This page updates by itself.
          </p>
        </div>
      ) : render.status === "failed" ? (
        <p role="alert" className="mt-2 rounded-md bg-red-50 p-3 text-sm text-red-900">
          It couldn&apos;t be rendered: {render.error ?? "no reason was given."}
        </p>
      ) : render.pdf ? (
        <>
          <div className="mt-2">
            <PageBadge pages={render.pdf.pages} onePage />
          </div>
          <iframe
            src={`${draftPdfUrl(draft.id)}?v=${render.pdf.sha256}`}
            title={`Preview of the resume for ${draft.posting?.title ?? "the posting"}`}
            className="mt-3 h-[36rem] w-full rounded border border-stone-200 bg-stone-100"
          />
          <p className="mt-1 text-xs text-stone-600">
            No preview?{" "}
            <a
              href={draftPdfUrl(draft.id)}
              target="_blank"
              rel="noreferrer"
              className={`rounded text-emerald-800 underline underline-offset-2 ${FOCUS}`}
            >
              Open the PDF in a new tab
            </a>
            .
          </p>
          <PdfWarnings warnings={render.pdf.warnings} />
          <CutReport cuts={render.pdf.cuts} none="Nothing was cut: it fit one page as chosen." />
          {render.ats && <AtsCheck ats={render.ats} />}
        </>
      ) : null}
    </Section>
  );
}

/** Download the PDF, which logs the application once (FR-TLR-8). A resume that breaks a rule
 * can be downloaded, but mustn't be sent (R2). */
export function DownloadSection({ id, draft }: { id: string; draft: TailorDraft }) {
  const download = useDownload(draft.id);
  const application = draft.application ?? null;
  // An undecided skill breaks R3 until it is decided, which the download waits for anyway.
  const breaksRules =
    draft.skills.every((skill) => skill.decision !== null) &&
    (draft.rules?.findings ?? []).some((finding) => finding.severity === "error");
  if (application !== null) {
    return (
      <Section id={id} title="Download">
        <p className="mt-1 text-sm text-stone-700">
          Logged as application <span className="font-mono text-xs">{application.id}</span> on{" "}
          {calendarDate(application.appliedOn)}.
        </p>
        {!application.sendable && (
          <p className="mt-2 rounded-md bg-red-50 p-3 text-sm text-red-900">
            Don&apos;t send it: it breaks a resume rule. Fix the errors under Resume rules and
            tailor it again.
          </p>
        )}
        <a href={applicationPdfUrl(application.pdfSha256)} download className={`mt-3 ${BUTTON}`}>
          Download PDF
        </a>
      </Section>
    );
  }
  return (
    <Section id={id} title="Download">
      {draft.blocked.length > 0 ? (
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-stone-700">
          {draft.blocked.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-sm text-stone-600">
          Downloading logs the application with this posting, today&apos;s date and the PDF.
        </p>
      )}
      {breaksRules && (
        <p className="mt-2 rounded-md bg-red-50 p-3 text-sm text-red-900">
          It breaks a resume rule: you can download it, but don&apos;t send it until the errors
          under Resume rules are fixed.
        </p>
      )}
      <div className="mt-3 flex items-center gap-3">
        <button
          type="button"
          disabled={draft.blocked.length > 0 || download.isPending}
          onClick={() => {
            download.mutate();
          }}
          className={PRIMARY_BUTTON}
        >
          {download.isPending ? "Logging..." : "Download PDF and log the application"}
        </button>
        <p role="status" className="text-sm text-red-800 empty:hidden">
          {download.isError ? failureMessage("download it", download.error) : null}
        </p>
      </div>
    </Section>
  );
}

/** What the draft shows, before it's fitted to one page. */
export function OutlineSection({ outline }: { outline: DraftOutline }) {
  const items = [...outline.work, ...outline.projects];
  return (
    <details className="mt-6 text-sm">
      <summary className={`w-fit cursor-pointer rounded font-semibold ${FOCUS}`}>
        What the draft shows
      </summary>
      <div className="mt-2 space-y-3 border-l-2 border-stone-200 pl-3">
        {outline.summary && <p className="text-stone-700">{outline.summary}</p>}
        {items.map((item) => (
          <div key={item.id}>
            <p className="font-medium">
              {item.name}
              {item.title ? `: ${item.title}` : ""}
            </p>
            <ul className="list-disc pl-5 text-stone-700">
              {item.bullets.map((bullet) => (
                <li key={bullet.id}>{bullet.text}</li>
              ))}
            </ul>
          </div>
        ))}
        {outline.skills.map((line) => (
          <p key={line.label} className="text-stone-700">
            <span className="font-medium">{line.label}:</span> {line.skills.join(", ")}
          </p>
        ))}
      </div>
    </details>
  );
}
