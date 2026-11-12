import { useId } from "react";

import type { MasterResume } from "../../api/resumes";
import { dateAndTime } from "../../format";
import {
  Badge,
  Command,
  CutReport,
  DownloadLink,
  Findings,
  PageBadge,
  PdfPreview,
  PdfWarnings,
} from "./ResumeParts";

/** Where a refresh's master document is: the hosted app can't run LaTeX (C6), so it waits for
 * the owner's computer to render it. */
function Status({ master }: { master: MasterResume }) {
  const version = String(master.version);
  switch (master.status) {
    case "updated":
      return (
        <p className="mt-1 text-sm text-stone-600">
          Profile version {version}, rendered {dateAndTime(master.changedAt)}.
        </p>
      );
    case "pending":
      return (
        <p className="mt-2 rounded-md bg-amber-50 p-3 text-sm text-amber-900">
          Version {version} waits to be rendered. The hosted app can&apos;t run LaTeX, so run{" "}
          <Command>uv run wj tick</Command> on your computer, where TeX Live is installed, and it
          appears here.
        </p>
      );
    case "failed":
      return (
        <p className="mt-2 rounded-md bg-red-50 p-3 text-sm text-red-900">
          Version {version} couldn&apos;t be rendered. Run <Command>uv run wj render --db</Command>{" "}
          on your computer to see why; the next refresh tries again.
        </p>
      );
  }
}

const STATUS_BADGE: Record<
  MasterResume["status"],
  { tone: "good" | "waiting" | "bad"; label: string }
> = {
  updated: { tone: "good", label: "Rendered" },
  pending: { tone: "waiting", label: "Waiting to be rendered" },
  failed: { tone: "bad", label: "Couldn't be rendered" },
};

/** The full master document of a refresh (FR-RES-5, FR-RES-6): every page, so its page count is
 * only information, and nothing is cut. */
export function MasterCard({ master, title }: { master: MasterResume; title: string }) {
  const id = useId();
  const status = STATUS_BADGE[master.status];
  const { pdf } = master;
  return (
    <article
      aria-labelledby={`${id}-title`}
      className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm"
    >
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h3 id={`${id}-title`} className="font-medium">
          {title}
        </h3>
        <Badge tone={status.tone}>{status.label}</Badge>
        {pdf !== null && <PageBadge pages={pdf.pages} onePage={false} />}
        {pdf !== null && (
          <div className="ml-auto">
            <DownloadLink pdf={pdf} label={`Download the PDF of ${title.toLowerCase()}`} />
          </div>
        )}
      </header>
      <Status master={master} />
      {pdf !== null && (
        <>
          <PdfPreview pdf={pdf} title={`Preview of ${title.toLowerCase()}`} />
          <PdfWarnings warnings={pdf.warnings} />
          <CutReport
            cuts={pdf.cuts}
            none="Nothing was cut: the master document shows everything."
          />
        </>
      )}
      <Findings
        title={`Profile check of version ${String(master.version)}`}
        report={master.lint}
        missing="Not checked: this profile version is gone."
      />
    </article>
  );
}
