import { useEffect, useId, useRef } from "react";

import { failureMessage } from "../../api/auth";
import type { Citation } from "../../api/journal";
import { BUTTON, DANGER_BUTTON } from "../ui";

function citedWarning(count: number): string {
  return count === 1
    ? "A bullet in your profile cites this. The bullet stays, but loses the fact behind it:"
    : `${String(count)} bullets in your profile cite this. They stay, but lose the facts behind them:`;
}

/**
 * Asks before deleting, in place, and names the profile's bullets that cite what's deleted
 * (FR-JRN-6). Focus starts on Cancel, and Escape cancels.
 */
export function ConfirmDelete({
  question,
  citations = [],
  confirmLabel,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  question: string;
  citations?: readonly Citation[];
  confirmLabel: string;
  pending: boolean;
  error: Error | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const id = useId();
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    cancel.current?.focus();
  }, []);
  return (
    <div
      role="group"
      aria-labelledby={id}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          onCancel();
        }
      }}
      className="mt-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-950"
    >
      <p id={id} className="font-medium">
        {question}
      </p>
      {citations.length > 0 && (
        <div className="mt-2">
          <p>{citedWarning(citations.length)}</p>
          <ul className="mt-1 list-disc space-y-1 pl-5">
            {citations.map((citation) => (
              <li key={citation.bulletId}>{citation.text}</li>
            ))}
          </ul>
        </div>
      )}
      {error !== null && (
        <p role="alert" className="mt-2 text-red-800">
          {failureMessage("delete it", error)}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <button type="button" onClick={onConfirm} disabled={pending} className={DANGER_BUTTON}>
          {confirmLabel}
        </button>
        <button ref={cancel} type="button" onClick={onCancel} className={BUTTON}>
          Cancel
        </button>
      </div>
    </div>
  );
}
