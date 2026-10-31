import { useId, useState } from "react";

import { useSavePortfolio, type PortfolioSettings, type Visibility } from "../../api/settings";
import { FOCUS, INPUT, LABEL, PRIMARY_BUTTON } from "../ui";
import { HINT } from "./form";
import { SaveResult, SettingsSection } from "./SettingsSection";

/** A handle as the API keeps it: 2 to 40 letters, digits and single hyphens. */
const HANDLE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

const VISIBILITIES: readonly { value: Visibility; label: string }[] = [
  { value: "public", label: "Public: shown on the page" },
  { value: "resume_only", label: "Resume only: only in resumes you send" },
  { value: "private", label: "Private: shown nowhere" },
];

interface Draft {
  handle: string;
  defaultVisibility: Visibility;
  openToWork: boolean;
  indexing: boolean;
}

function draftOf(portfolio: PortfolioSettings): Draft {
  return { ...portfolio, handle: portfolio.handle ?? "" };
}

/** The portfolio page's handle, default visibility, open to work and indexing (FR-SET-3). */
export function PortfolioForm({ portfolio }: { portfolio: PortfolioSettings }) {
  const [draft, setDraft] = useState(() => draftOf(portfolio));
  const [problem, setProblem] = useState<string | null>(null);
  const save = useSavePortfolio();
  const id = useId();
  const field = (name: string) => `${id}-${name}`;
  const change = (changes: Partial<Draft>) => {
    setDraft((current) => ({ ...current, ...changes }));
    setProblem(null);
    save.reset();
  };
  return (
    <SettingsSection
      title="Portfolio page"
      description="Your public page at /p/<handle>. These settings are kept now and take effect when the public portfolio page is built."
    >
      <form
        aria-label="Portfolio page"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          const handle = draft.handle.trim().toLowerCase();
          if (handle !== "" && (handle.length < 2 || handle.length > 40 || !HANDLE.test(handle))) {
            setProblem(
              "The handle needs 2 to 40 letters, digits and single hyphens, starting and ending with a letter or digit.",
            );
            return;
          }
          save.mutate(
            { ...draft, handle: handle === "" ? null : handle },
            {
              onSuccess: (saved) => {
                setDraft(draftOf(saved));
              },
            },
          );
        }}
        className="space-y-4"
      >
        <div>
          <label htmlFor={field("handle")} className={LABEL}>
            Handle
          </label>
          <div className="mt-1 flex items-center gap-1 text-sm">
            <span aria-hidden="true" className="text-stone-500">
              /p/
            </span>
            <input
              id={field("handle")}
              value={draft.handle}
              autoComplete="off"
              spellCheck={false}
              maxLength={40}
              onChange={(event) => {
                change({ handle: event.target.value });
              }}
              aria-describedby={field("handle-hint")}
              className={`block w-full max-w-xs rounded border border-stone-300 bg-white px-2 py-1.5 text-sm ${FOCUS}`}
            />
          </div>
          <p id={field("handle-hint")} className={HINT}>
            Lowercase letters, digits and hyphens, such as casey-example. Leave it empty until you
            choose one.
          </p>
        </div>
        <div>
          <label htmlFor={field("visibility")} className={LABEL}>
            Default visibility
          </label>
          <select
            id={field("visibility")}
            value={draft.defaultVisibility}
            onChange={(event) => {
              change({ defaultVisibility: event.target.value as Visibility });
            }}
            aria-describedby={field("visibility-hint")}
            className={`${INPUT} max-w-xs`}
          >
            {VISIBILITIES.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <p id={field("visibility-hint")} className={HINT}>
            For what you haven&apos;t given a visibility of its own.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.openToWork}
            onChange={(event) => {
              change({ openToWork: event.target.checked });
            }}
          />
          Show that I&apos;m open to work
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.indexing}
            onChange={(event) => {
              change({ indexing: event.target.checked });
            }}
          />
          Let search engines index the page
        </label>
        {problem !== null && (
          <p role="alert" className="text-sm text-red-700">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={save.isPending} className={PRIMARY_BUTTON}>
            Save portfolio settings
          </button>
          <SaveResult
            action="save the portfolio settings"
            saved={save.isSuccess}
            error={save.error}
          />
        </div>
      </form>
    </SettingsSection>
  );
}
