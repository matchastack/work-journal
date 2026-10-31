import { useId, useState } from "react";

import {
  useSaveConfidentialTerms,
  useSaveStyleNotes,
  type ConfidentialTerms,
  type StyleNotes,
} from "../../api/settings";
import { INPUT, LABEL, PRIMARY_BUTTON } from "../ui";
import { HINT, lines } from "./form";
import { SaveResult, SettingsSection } from "./SettingsSection";

/** The most items one list may hold, as the API allows. */
const MAX_ITEMS = 200;

function tooMany(items: readonly string[], what: string): string | null {
  return items.length > MAX_ITEMS ? `Keep it to ${String(MAX_ITEMS)} ${what} or fewer.` : null;
}

function tooLong(items: readonly string[], limit: number, what: string): string | null {
  const long = items.find((item) => item.length > limit);
  return long === undefined
    ? null
    : `Each ${what} can have up to ${String(limit)} characters; "${long.slice(0, 30)}..." is longer.`;
}

/** The words to avoid and the style notes, one per line (FR-SET-4). */
export function StyleNotesForm({ notes }: { notes: StyleNotes }) {
  const [avoid, setAvoid] = useState(() => notes.avoid.join("\n"));
  const [preferences, setPreferences] = useState(() =>
    notes.notes.map((note) => note.text).join("\n"),
  );
  const [problem, setProblem] = useState<string | null>(null);
  const save = useSaveStyleNotes();
  const id = useId();
  const edited = () => {
    setProblem(null);
    save.reset();
  };
  const fromRejections = notes.notes.filter((note) => note.source === "rejection").length;
  return (
    <SettingsSection
      title="Style notes"
      description="How you like your bullets and LinkedIn text written."
    >
      <form
        aria-label="Style notes"
        onSubmit={(event) => {
          event.preventDefault();
          const words = lines(avoid);
          const texts = lines(preferences);
          const found =
            tooMany(words, "words") ??
            tooLong(words, 100, "word or phrase") ??
            tooMany(texts, "notes") ??
            tooLong(texts, 500, "note");
          setProblem(found);
          if (found === null) {
            save.mutate(
              { avoid: words, notes: texts },
              {
                onSuccess: (saved) => {
                  setAvoid(saved.avoid.join("\n"));
                  setPreferences(saved.notes.map((note) => note.text).join("\n"));
                },
              },
            );
          }
        }}
        className="space-y-4"
      >
        <div>
          <label htmlFor={`${id}-avoid`} className={LABEL}>
            Words to avoid
          </label>
          <textarea
            id={`${id}-avoid`}
            rows={4}
            value={avoid}
            onChange={(event) => {
              setAvoid(event.target.value);
              edited();
            }}
            aria-describedby={`${id}-avoid-hint`}
            className={INPUT}
          />
          <p id={`${id}-avoid-hint`} className={HINT}>
            One word or phrase per line, such as spearheaded. The style check flags them in
            everything written for you.
          </p>
        </div>
        <div>
          <label htmlFor={`${id}-notes`} className={LABEL}>
            Notes
          </label>
          <textarea
            id={`${id}-notes`}
            rows={5}
            value={preferences}
            onChange={(event) => {
              setPreferences(event.target.value);
              edited();
            }}
            aria-describedby={`${id}-notes-hint`}
            className={INPUT}
          />
          <p id={`${id}-notes-hint`} className={HINT}>
            One preference per line, such as &quot;Say what it fixed&quot;. The LinkedIn text
            follows them for now. The reasons you give for rejecting a change in the Inbox are added
            here{fromRejections > 0 ? ` (${String(fromRejections)} so far)` : ""}.
          </p>
        </div>
        {problem !== null && (
          <p role="alert" className="text-sm text-red-700">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={save.isPending} className={PRIMARY_BUTTON}>
            Save style notes
          </button>
          <SaveResult action="save the style notes" saved={save.isSuccess} error={save.error} />
        </div>
      </form>
    </SettingsSection>
  );
}

/** The confidential terms, one per line (FR-SET-5). */
export function ConfidentialTermsForm({ terms }: { terms: ConfidentialTerms }) {
  const [text, setText] = useState(() => terms.terms.join("\n"));
  const [problem, setProblem] = useState<string | null>(null);
  const save = useSaveConfidentialTerms();
  const id = useId();
  return (
    <SettingsSection
      title="Confidential terms"
      description="Names and words that must never appear in anything public: your portfolio page, published PDFs and LinkedIn text. They're kept now and blocked once the confidentiality checks are built."
    >
      <form
        aria-label="Confidential terms"
        onSubmit={(event) => {
          event.preventDefault();
          const items = lines(text);
          const found = tooMany(items, "terms") ?? tooLong(items, 100, "term");
          setProblem(found);
          if (found === null) {
            save.mutate(
              { terms: items },
              {
                onSuccess: (saved) => {
                  setText(saved.terms.join("\n"));
                },
              },
            );
          }
        }}
        className="space-y-4"
      >
        <div>
          <label htmlFor={`${id}-terms`} className={LABEL}>
            Terms
          </label>
          <textarea
            id={`${id}-terms`}
            rows={4}
            value={text}
            onChange={(event) => {
              setText(event.target.value);
              setProblem(null);
              save.reset();
            }}
            aria-describedby={`${id}-terms-hint`}
            className={INPUT}
          />
          <p id={`${id}-terms-hint`} className={HINT}>
            One per line, such as a client&apos;s or an internal project&apos;s name. They match
            whole words, in any case.
          </p>
        </div>
        {problem !== null && (
          <p role="alert" className="text-sm text-red-700">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={save.isPending} className={PRIMARY_BUTTON}>
            Save confidential terms
          </button>
          <SaveResult
            action="save the confidential terms"
            saved={save.isSuccess}
            error={save.error}
          />
        </div>
      </form>
    </SettingsSection>
  );
}
