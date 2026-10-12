import type { VerifierReport } from "../../api/inbox";

const CHECKS: Record<VerifierReport["findings"][number]["check"], string> = {
  numbers: "Numbers",
  claims: "Claims",
  style: "Style",
  rules: "Resume rules",
};

const VERDICTS: Record<VerifierReport["claims"][number]["verdict"], string> = {
  supported: "Supported",
  unsupported: "Not supported",
  contradicted: "Contradicted",
};

function plural(count: number, one: string): string {
  return `${String(count)} ${one}${count === 1 ? "" : "s"}`;
}

/** What the verifier found in a change's text (FR-FID-8): passed or flagged, with each finding
 * and, on request, its verdict on each claim. */
export function VerifierSummary({ report }: { report: VerifierReport }) {
  const warnings = report.findings.filter((finding) => finding.severity === "warning").length;
  return (
    <div className="mt-3 text-sm">
      {report.passed ? (
        <p className="font-medium text-emerald-800">
          Verifier: passed{warnings > 0 ? ` with ${plural(warnings, "warning")}` : ""}
        </p>
      ) : (
        <p className="font-medium text-red-800">
          Verifier: flagged. Accept it only if you're sure it's true.
        </p>
      )}
      {report.findings.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-stone-700">
          {report.findings.map((finding, index) => (
            <li key={index}>
              <span className={finding.severity === "error" ? "text-red-800" : "text-amber-800"}>
                {finding.severity === "error" ? "Error" : "Warning"}
              </span>{" "}
              ({CHECKS[finding.check]}): {finding.message}
            </li>
          ))}
        </ul>
      )}
      {report.claims.length > 0 && (
        <details className="mt-1 text-xs text-stone-600">
          <summary className="w-fit cursor-pointer rounded">
            {plural(report.claims.length, "claim")} checked against the facts
          </summary>
          <ul className="mt-1 space-y-0.5 border-l-2 border-stone-200 pl-3">
            {report.claims.map((claim, index) => (
              <li key={index}>
                {VERDICTS[claim.verdict]}: &quot;{claim.claim}&quot;
                {claim.term ? ` (${claim.term})` : ""}
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
