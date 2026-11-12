import { useEffect, useId, useRef, useState } from "react";

import { failureMessage } from "../../api/auth";
import { EXPORT_URL, useDeleteAllData } from "../../api/settings";
import { BUTTON, DANGER_BUTTON, INPUT, LABEL } from "../ui";
import { SettingsSection } from "./SettingsSection";

/**
 * Asks before deleting everything, in place: the GitHub username must be typed first. Focus
 * starts in the box, and Escape cancels.
 */
function ConfirmDeleteAll({ login, onCancel }: { login: string; onCancel: () => void }) {
  const [typed, setTyped] = useState("");
  const remove = useDeleteAllData();
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
  }, []);
  const confirmed = typed.trim().toLowerCase() === login.toLowerCase();
  return (
    <div
      role="dialog"
      aria-labelledby={`${id}-title`}
      aria-describedby={`${id}-what`}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          onCancel();
        }
      }}
      className="mt-4 rounded border border-red-200 bg-red-50 p-4 text-sm text-red-950"
    >
      <h3 id={`${id}-title`} className="font-semibold">
        Delete all your data?
      </h3>
      <p id={`${id}-what`} className="mt-1">
        This deletes your account and everything stored for it: your journal, facts, profile
        versions, proposed changes, applications, resumes and settings, and it unlinks Telegram.
        It can&apos;t be undone. Your messages stay in your Telegram chat.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          if (confirmed) {
            remove.mutate(typed.trim());
          }
        }}
        className="mt-3"
      >
        <label htmlFor={`${id}-login`} className={LABEL}>
          Type your GitHub username, {login}, to confirm
        </label>
        <input
          ref={input}
          id={`${id}-login`}
          value={typed}
          autoComplete="off"
          spellCheck={false}
          onChange={(event) => {
            setTyped(event.target.value);
            remove.reset();
          }}
          className={`${INPUT} max-w-xs`}
        />
        {remove.isError && (
          <p role="alert" className="mt-2 text-red-800">
            {failureMessage("delete your data", remove.error)}
          </p>
        )}
        <div className="mt-3 flex gap-2">
          <button
            type="submit"
            disabled={!confirmed || remove.isPending}
            className={DANGER_BUTTON}
          >
            {remove.isPending ? "Deleting..." : "Delete everything"}
          </button>
          <button type="button" onClick={onCancel} className={BUTTON}>
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}

/** Export all data, and delete all of it (FR-SET-6, NFR-PRIV-3). */
export function DataSection({ login }: { login: string | null }) {
  const [asking, setAsking] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  return (
    <SettingsSection
      title="Your data"
      description="Everything stored for you is yours to take or to delete."
    >
      <div className="space-y-4">
        <div>
          <a href={EXPORT_URL} download className={BUTTON}>
            Download all my data
          </a>
          <p className="mt-1 text-xs text-stone-500">
            One JSON file with your journal, facts, profile versions, proposed changes,
            applications, resumes and settings.
          </p>
        </div>
        <div>
          <button
            ref={opener}
            type="button"
            disabled={login === null}
            aria-expanded={asking}
            onClick={() => {
              setAsking(true);
            }}
            className={DANGER_BUTTON}
          >
            Delete all my data
          </button>
          {login === null && (
            <p className="mt-1 text-xs text-stone-500">
              Sign in with GitHub again to delete your data.
            </p>
          )}
          {asking && login !== null && (
            <ConfirmDeleteAll
              login={login}
              onCancel={() => {
                setAsking(false);
                opener.current?.focus();
              }}
            />
          )}
        </div>
      </div>
    </SettingsSection>
  );
}
