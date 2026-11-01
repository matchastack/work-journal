import { useId, useState } from "react";

import {
  resumeKey,
  useSaveResumes,
  type PortfolioView,
  type PublishedResume,
} from "../../api/portfolio";
import { HINT } from "../settings/form";
import { SaveResult, SettingsSection } from "../settings/SettingsSection";
import { FOCUS, PRIMARY_BUTTON } from "../ui";

/** The most resumes the page offers, and the longest button text, as the API allows. */
const MAX_RESUMES = 5;
const MAX_LABEL = 60;

interface Row {
  key: string;
  source: PublishedResume["source"];
  applicationId: string | null;
  name: string;
  label: string;
  chosen: boolean;
  available: boolean;
}

/** The chosen resumes in their order, then the others the owner may offer. */
function rowsOf(view: PortfolioView): Row[] {
  const options = new Map(view.options.map((option) => [resumeKey(option), option]));
  const chosen = view.resumes.map((resume): Row => {
    const key = resumeKey(resume);
    const option = options.get(key);
    return {
      key,
      source: resume.source,
      applicationId: resume.applicationId ?? null,
      name: option?.name ?? (resume.source === "master" ? "Master resume" : "A tailored resume"),
      label: resume.label,
      chosen: true,
      available: resume.available,
    };
  });
  const keys = new Set(chosen.map((row) => row.key));
  const others = view.options
    .filter((option) => !keys.has(resumeKey(option)))
    .map(
      (option): Row => ({
        key: resumeKey(option),
        source: option.source,
        applicationId: option.applicationId ?? null,
        name: option.name,
        label: option.label,
        chosen: false,
        available: true,
      }),
    );
  return [...chosen, ...others];
}

/** The resumes visitors may download from the page, each with its button's text (FR-PRT-7). */
export function ResumesSection({ view }: { view: PortfolioView }) {
  const [rows, setRows] = useState(() => rowsOf(view));
  const [problem, setProblem] = useState<string | null>(null);
  const save = useSaveResumes();
  const id = useId();
  const change = (key: string, changes: Partial<Row>) => {
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...changes } : row)));
    setProblem(null);
    save.reset();
  };
  return (
    <SettingsSection
      title="Resume downloads"
      description={
        <>
          <p>Visitors can download the resumes you choose, in this order.</p>
          <p>
            A resume shows what you marked Resume only, such as your phone number, and anyone can
            download it. A tailored resume with an error can&apos;t be offered.
          </p>
        </>
      }
    >
      {rows.length === 0 ? (
        <p className="text-sm text-stone-600">
          No resumes to offer yet. The master resume appears once a refresh renders it, and a
          tailored resume once <code>wj tailor --db</code> makes one that passes its checks.
        </p>
      ) : (
        <form
          aria-label="Resume downloads"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            const chosen = rows.filter((row) => row.chosen);
            if (chosen.length > MAX_RESUMES) {
              setProblem(`Choose up to ${String(MAX_RESUMES)} resumes.`);
              return;
            }
            const labels = chosen.map((row) => row.label.trim());
            if (labels.some((label) => label === "" || label.length > MAX_LABEL)) {
              setProblem(
                `Give each resume a button text of up to ${String(MAX_LABEL)} characters.`,
              );
              return;
            }
            save.mutate(
              chosen.map((row, index) => ({
                source: row.source,
                applicationId: row.applicationId,
                label: labels[index] ?? row.label,
              })),
              {
                onSuccess: (saved) => {
                  setRows(rowsOf(saved));
                },
              },
            );
          }}
          className="space-y-4"
        >
          <ul className="space-y-3">
            {rows.map((row) => {
              const labelId = `${id}-${row.key}-label`;
              return (
                <li key={row.key} className="rounded border border-stone-200 p-3">
                  <label className="flex items-start gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={row.chosen}
                      onChange={(event) => {
                        change(row.key, { chosen: event.target.checked });
                      }}
                      className="mt-0.5"
                    />
                    <span>
                      Offer {row.name}
                      {!row.available && (
                        <span className="block text-xs text-amber-800">
                          Not offered now: it has no PDF that may be sent.
                        </span>
                      )}
                    </span>
                  </label>
                  {row.chosen && (
                    <div className="mt-2 ml-6">
                      <label htmlFor={labelId} className="block text-xs font-medium text-stone-700">
                        Button text
                      </label>
                      <input
                        id={labelId}
                        value={row.label}
                        maxLength={MAX_LABEL}
                        onChange={(event) => {
                          change(row.key, { label: event.target.value });
                        }}
                        className={`mt-1 block w-full max-w-xs rounded border border-stone-300 bg-white px-2 py-1.5 text-sm ${FOCUS}`}
                      />
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
          <p className={HINT}>Visitors see the button text, not the company a resume was for.</p>
          {problem !== null && (
            <p role="alert" className="text-sm text-red-700">
              {problem}
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3">
            <button type="submit" disabled={save.isPending} className={PRIMARY_BUTTON}>
              Save resume downloads
            </button>
            <SaveResult
              action="save the resume downloads"
              saved={save.isSuccess}
              error={save.error}
            />
          </div>
        </form>
      )}
    </SettingsSection>
  );
}
