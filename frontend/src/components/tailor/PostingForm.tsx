import { useId, useState } from "react";

import { failureMessage } from "../../api/auth";
import { useNewDraft } from "../../api/tailor";
import { INPUT, LABEL, PRIMARY_BUTTON } from "../ui";

/** Paste a job posting to tailor a resume to it (FR-TLR-1). */
export function PostingForm({ onCreated }: { onCreated: (draftId: string) => void }) {
  const id = useId();
  const [text, setText] = useState("");
  const newDraft = useNewDraft();
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (text.trim() === "") {
          return;
        }
        newDraft.mutate(text, {
          onSuccess: (draft) => {
            setText("");
            onCreated(draft.id);
          },
        });
      }}
      aria-labelledby={`${id}-title`}
      className="mt-6"
    >
      <h2 id={`${id}-title`} className="text-lg font-semibold">
        New tailored resume
      </h2>
      <label htmlFor={`${id}-posting`} className={`mt-3 ${LABEL}`}>
        Job posting
      </label>
      <textarea
        id={`${id}-posting`}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
        rows={8}
        required
        aria-describedby={`${id}-hint`}
        className={`${INPUT} font-mono`}
      />
      <p id={`${id}-hint`} className="mt-1 text-xs text-stone-600">
        Paste the whole posting: title, company, requirements and responsibilities.
      </p>
      <div className="mt-3 flex items-center gap-3">
        <button
          type="submit"
          disabled={newDraft.isPending || text.trim() === ""}
          className={PRIMARY_BUTTON}
        >
          {newDraft.isPending ? "Sending..." : "Tailor a resume"}
        </button>
        <p role="status" className="text-sm text-red-800 empty:hidden">
          {newDraft.isError ? failureMessage("start tailoring", newDraft.error) : null}
        </p>
      </div>
    </form>
  );
}
