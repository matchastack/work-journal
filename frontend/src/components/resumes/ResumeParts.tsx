import type { ReactNode } from "react";

import { pdfUrls, type LintReport, type ResumeCut, type ResumePdf } from "../../api/resumes";
import { BUTTON, FOCUS } from "../ui";

const TONES = {
  neutral: "bg-stone-100 text-stone-700 ring-stone-300",
  good: "bg-emerald-50 text-emerald-900 ring-emerald-200",
  bad: "bg-red-50 text-red-900 ring-red-200",
  waiting: "bg-amber-50 text-amber-900 ring-amber-200",
} as const;

const CUT_KINDS: Record<ResumeCut["kind"], string> = {
  bullet: "Bullet",
  role: "Role",
  project: "Project",
};

function plural(count: number, one: string): string {
  return `${String(count)} ${one}${count === 1 ? "" : "s"}`;
}

/** A command to run on the owner's computer. */
export function Command({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-stone-100 px-1 py-0.5 font-mono text-[0.85em]">{children}</code>
  );
}

/** A short status beside a heading. */
export function Badge({ tone, children }: { tone: keyof typeof TONES; children: ReactNode }) {
  return (
    <span className={`rounded-full px-2 py-0.5 text-xs ring-1 ${TONES[tone]}`}>{children}</span>
  );
}

/** How many pages the PDF has. A resume that's sent must fit one page (R6): any other count is
 * shown in red. */
export function PageBadge({ pages, onePage }: { pages: number | null; onePage: boolean }) {
  if (pages === null) {
    return <Badge tone="neutral">Page count not recorded</Badge>;
  }
  if (!onePage) {
    return <Badge tone="neutral">{plural(pages, "page")}</Badge>;
  }
  if (pages !== 1) {
    return <Badge tone="bad">{plural(pages, "page")}: over the one-page limit</Badge>;
  }
  return <Badge tone="good">1 page</Badge>;
}

/** Download the PDF. */
export function DownloadLink({ pdf, label }: { pdf: ResumePdf; label: string }) {
  return (
    <a href={pdfUrls(pdf.sha256).download} download className={BUTTON} aria-label={label}>
      Download PDF
    </a>
  );
}

/** The PDF in the browser's own viewer, with a link for browsers that can't show it in a frame. */
export function PdfPreview({ pdf, title }: { pdf: ResumePdf; title: string }) {
  const { preview } = pdfUrls(pdf.sha256);
  return (
    <div className="mt-4">
      <iframe
        src={preview}
        title={title}
        className="h-[36rem] w-full rounded border border-stone-200 bg-stone-100"
      />
      <p className="mt-1 text-xs text-stone-600">
        No preview?{" "}
        <a
          href={preview}
          target="_blank"
          rel="noreferrer"
          className={`rounded text-emerald-800 underline underline-offset-2 ${FOCUS}`}
        >
          Open the PDF in a new tab
        </a>
        .
      </p>
    </div>
  );
}

/** What was cut to fit the page limit (FR-RES-7), in the order it was cut. */
export function CutReport({ cuts, none }: { cuts: readonly ResumeCut[] | null; none: string }) {
  return (
    <div className="mt-4 text-sm">
      <h4 className="font-medium">Cut report</h4>
      {cuts === null ? (
        <p className="mt-1 text-stone-600">
          Not recorded: this PDF was made before cut reports were kept.
        </p>
      ) : cuts.length === 0 ? (
        <p className="mt-1 text-stone-600">{none}</p>
      ) : (
        <>
          <p className="mt-1 text-stone-600">
            {plural(cuts.length, "item")} cut to fit, lowest value first:
          </p>
          <ol className="mt-1 list-decimal space-y-0.5 pl-5 text-stone-700">
            {cuts.map((cut) => (
              <li key={`${cut.kind}-${cut.id}`}>
                {CUT_KINDS[cut.kind]}: {cut.text}
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}

/** The findings of the profile check or the resume rules: errors first, then warnings, then
 * what code couldn't check. */
export function Findings({
  title,
  report,
  missing,
}: {
  title: string;
  report: LintReport | null;
  missing: string;
}) {
  return (
    <div className="mt-4 text-sm">
      <h4 className="font-medium">{title}</h4>
      {report === null ? (
        <p className="mt-1 text-stone-600">{missing}</p>
      ) : (
        <>
          {report.findings.length === 0 ? (
            <p className="mt-1 text-emerald-800">No problems found.</p>
          ) : (
            <ul className="mt-1 space-y-0.5 text-stone-700">
              {report.findings.map((finding, index) => (
                <li key={index}>
                  <span
                    className={finding.severity === "error" ? "text-red-800" : "text-amber-800"}
                  >
                    {finding.severity === "error" ? "Error" : "Warning"}
                  </span>{" "}
                  ({finding.rule}) {finding.location}: {finding.message}
                </li>
              ))}
            </ul>
          )}
          {report.notes.length > 0 && (
            <ul className="mt-1 space-y-0.5 text-xs text-stone-600">
              {report.notes.map((note, index) => (
                <li key={index}>Not checked: {note}</li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}

/** Warnings about the PDF itself, such as text in a bitmap font. */
export function PdfWarnings({ warnings }: { warnings: readonly string[] }) {
  if (warnings.length === 0) {
    return null;
  }
  return (
    <ul className="mt-3 space-y-0.5 text-sm text-amber-800">
      {warnings.map((warning, index) => (
        <li key={index}>Warning: {warning}</li>
      ))}
    </ul>
  );
}
