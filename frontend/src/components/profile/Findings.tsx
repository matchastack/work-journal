import type { LintFinding } from "../../api/profile";
import { SEVERITY_LABELS } from "./labels";

/** The resume-rule findings about one card, at `path`. A finding about one of its fields says
 * which. They don't stop a save. */
export function Findings({ findings, path }: { findings: readonly LintFinding[]; path: string }) {
  if (findings.length === 0) {
    return null;
  }
  return (
    <ul aria-label="Resume rules" className="mt-2 space-y-1 text-xs">
      {findings.map((finding, index) => (
        <li
          key={index}
          className={finding.severity === "error" ? "text-red-800" : "text-amber-900"}
        >
          <span className="font-medium">{SEVERITY_LABELS[finding.severity]}:</span>{" "}
          {finding.location === path ? "" : `${finding.where}: `}
          {finding.message}
        </li>
      ))}
    </ul>
  );
}
