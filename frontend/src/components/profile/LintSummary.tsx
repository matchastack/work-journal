import type { LintView } from "../../api/profile";
import { SEVERITY_LABELS } from "./labels";

function plural(count: number, one: string): string {
  return `${String(count)} ${one}${count === 1 ? "" : "s"}`;
}

/**
 * The resume rules' findings on the whole profile, as `wj lint` reports them, with where each
 * problem is. They never stop a save: the master profile may hold work in progress, and a
 * resume that breaks a rule mustn't be sent until it's fixed (R2).
 */
export function LintSummary({ lint, titleId }: { lint: LintView; titleId: string }) {
  const errors = lint.findings.filter((finding) => finding.severity === "error").length;
  const warnings = lint.findings.length - errors;
  return (
    <section aria-labelledby={titleId}>
      <h2 id={titleId} className="scroll-mt-4 border-b border-stone-200 pb-1 text-lg font-semibold">
        Resume rules
      </h2>
      {lint.findings.length === 0 ? (
        <p className="mt-3 text-sm text-stone-600">No problems found.</p>
      ) : (
        <>
          <p className="mt-3 text-sm text-stone-600">
            {plural(errors, "error")} and {plural(warnings, "warning")}. They don&apos;t stop a
            save, but fix the errors before you send a resume.
          </p>
          <ul className="mt-3 space-y-2 text-sm">
            {lint.findings.map((finding, index) => (
              <li
                key={index}
                className={`rounded border p-2 ${
                  finding.severity === "error"
                    ? "border-red-200 bg-red-50 text-red-950"
                    : "border-amber-200 bg-amber-50 text-amber-950"
                }`}
              >
                <span className="font-medium">{SEVERITY_LABELS[finding.severity]}:</span>{" "}
                {finding.where}: {finding.message}
              </li>
            ))}
          </ul>
        </>
      )}
      {lint.notes.length > 0 && (
        <div className="mt-3 text-sm text-stone-600">
          <p>What the rules couldn&apos;t check:</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {lint.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
