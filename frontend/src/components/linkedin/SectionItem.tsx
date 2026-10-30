import { useId, useState } from "react";

import type { LinkedInSection } from "../../api/linkedin";
import { VerifierSummary } from "../inbox/VerifierSummary";
import { BUTTON } from "../ui";

function count(value: number): string {
  return value.toLocaleString("en-US");
}

/**
 * One section of the LinkedIn pack to paste (FR-LIN-3): its text, its length against
 * LinkedIn's limit, a Copy button and what the verifier found. A section that passed has a
 * Pasted box for the checklist; a flagged one isn't marked done with the others, so it has none.
 */
export function SectionItem({
  section,
  pasted,
  onPasted,
}: {
  section: LinkedInSection;
  pasted: boolean;
  onPasted: (pasted: boolean) => void;
}) {
  const id = useId();
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const over = section.length > section.limit;
  const copyText = async () => {
    try {
      await navigator.clipboard.writeText(section.text);
      setCopy("copied");
    } catch {
      // No clipboard (an http:// address other than localhost), or the browser refused.
      setCopy("failed");
    }
  };
  return (
    <li>
      <article
        aria-labelledby={`${id}-title`}
        className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm"
      >
        <header className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h2 id={`${id}-title`} className="font-medium">
            {section.label}
          </h2>
          <p className={`text-xs ${over ? "font-medium text-red-800" : "text-stone-600"}`}>
            {count(section.length)} of {count(section.limit)} characters
            {over ? ": over LinkedIn's limit" : ""}
          </p>
        </header>
        <p className="mt-2 rounded bg-stone-50 p-2 leading-relaxed whitespace-pre-wrap">
          {section.text}
        </p>
        {section.notes.map((note, index) => (
          <p key={index} className="mt-2 text-sm text-amber-900">
            Check: {note}
          </p>
        ))}
        {section.report !== null && (
          <VerifierSummary
            report={section.report}
            advice="Paste it only if you're sure it's true."
          />
        )}
        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            type="button"
            aria-describedby={`${id}-title`}
            onClick={() => void copyText()}
            className={BUTTON}
          >
            Copy
          </button>
          {section.flagged ? (
            <p className="text-sm text-stone-600">
              Not marked done with the others: it&apos;s written again next time.
            </p>
          ) : (
            <label className="inline-flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={pasted}
                onChange={(event) => {
                  onPasted(event.target.checked);
                }}
                className="size-4 accent-emerald-800"
              />
              Pasted into LinkedIn
            </label>
          )}
          <span role="status" className="text-sm text-emerald-800">
            {copy === "copied" ? "Copied" : ""}
          </span>
        </div>
        {copy === "failed" && (
          <p role="alert" className="mt-2 text-sm text-red-800">
            Couldn&apos;t copy it. Select the text above and copy it yourself.
          </p>
        )}
      </article>
    </li>
  );
}
