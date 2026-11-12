import { useId, useState } from "react";

import type { TailoredResume } from "../../api/resumes";
import { calendarDate } from "../../format";
import { BUTTON } from "../ui";
import {
  Badge,
  CutReport,
  DownloadLink,
  Findings,
  PageBadge,
  PdfPreview,
  PdfWarnings,
} from "./ResumeParts";

/** Whether the resume may be sent. One that breaks a resume rule is still kept, but mustn't be
 * sent until it's fixed (R2). */
function SendBadge({ resume }: { resume: TailoredResume }) {
  if (resume.rules === null) {
    return <Badge tone="waiting">Rules not checked</Badge>;
  }
  if (!resume.sendable) {
    return <Badge tone="bad">Don&apos;t send</Badge>;
  }
  return <Badge tone="good">Passes the resume rules</Badge>;
}

/** A resume tailored to one job posting, as its application logged it (FR-TLR-8). Its preview
 * opens on request, so a long list doesn't load every PDF at once. */
export function TailoredCard({ resume }: { resume: TailoredResume }) {
  const id = useId();
  const [previewing, setPreviewing] = useState(false);
  const title = `${resume.position} at ${resume.company}`;
  const { pdf } = resume;
  return (
    <li>
      <article
        aria-labelledby={`${id}-title`}
        className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm"
      >
        <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h3 id={`${id}-title`} className="font-medium">
            {title}
          </h3>
          <SendBadge resume={resume} />
          {pdf !== null && <PageBadge pages={pdf.pages} onePage />}
          {pdf !== null && (
            <div className="ml-auto flex gap-2">
              <button
                type="button"
                aria-expanded={previewing}
                aria-controls={`${id}-preview`}
                onClick={() => {
                  setPreviewing(!previewing);
                }}
                className={BUTTON}
              >
                {previewing ? "Hide preview" : "Show preview"}
              </button>
              <DownloadLink pdf={pdf} label={`Download the PDF for ${title}`} />
            </div>
          )}
        </header>
        <p className="mt-1 text-sm text-stone-600">
          Tailored on {calendarDate(resume.appliedOn)}
          <span className="text-stone-400"> | </span>
          <span className="font-mono text-xs">{resume.id}</span>
        </p>
        {!resume.sendable && resume.rules !== null && (
          <p className="mt-2 rounded-md bg-red-50 p-3 text-sm text-red-900">
            It breaks a resume rule: don&apos;t send it until the errors under Resume rules are
            fixed.
          </p>
        )}
        {pdf === null ? (
          <p className="mt-2 text-sm text-stone-600">No PDF was kept for this resume.</p>
        ) : (
          <>
            <div id={`${id}-preview`}>
              {previewing && <PdfPreview pdf={pdf} title={`Preview of the resume for ${title}`} />}
            </div>
            <PdfWarnings warnings={pdf.warnings} />
            <CutReport cuts={pdf.cuts} none="Nothing was cut: it fit one page as chosen." />
          </>
        )}
        <Findings
          title="Resume rules"
          report={resume.rules}
          missing="Not checked: the rule findings weren't kept with this resume."
        />
      </article>
    </li>
  );
}
