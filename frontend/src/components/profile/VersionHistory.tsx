import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";

import { failureMessage } from "../../api/auth";
import {
  useRestore,
  versionQuery,
  versionsQuery,
  type VersionChange,
  type VersionSummary,
} from "../../api/profile";
import { dateAndTime } from "../../format";
import { BUTTON, PRIMARY_BUTTON, TEXT_BUTTON } from "../ui";
import { WordDiff } from "../WordDiff";
import { AUTHOR_LABELS } from "./labels";
import { useEditing } from "./useEditing";

/** What a version did, in a line: its change set's summary, or what kind of version it is. */
function versionSummary(version: VersionSummary): string {
  if (version.restoredFrom !== null) {
    return `Restored version ${String(version.restoredFrom)}`;
  }
  if (version.parent === null) {
    return "The profile as first loaded";
  }
  return (
    version.summary ?? (version.author === "owner" ? "Your edits" : "Changes from your journal")
  );
}

/** A change's value before and after: a word at a time when it's reworded, or as two values. */
function Values({ change }: { change: VersionChange }) {
  if (change.diff.length > 0) {
    return <WordDiff parts={change.diff} />;
  }
  const rows: [string, string][] =
    change.kind === "added"
      ? [["Added", change.after ?? "none"]]
      : change.kind === "removed"
        ? [["Removed", change.before ?? "none"]]
        : [
            ["Before", change.before ?? "none"],
            ["After", change.after ?? "none"],
          ];
  return (
    <dl className="mt-2 grid gap-x-3 gap-y-1 rounded bg-stone-50 p-2 sm:grid-cols-[auto_1fr]">
      {rows.map(([label, value]) => (
        <div key={label} className="contents">
          <dt className="text-xs font-medium text-stone-600">{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** What a version changed against its parent, read when it's first shown. */
function VersionChanges({ number, parent }: { number: number; parent: number }) {
  const detail = useQuery(versionQuery(number));
  if (detail.isPending) {
    return (
      <p role="status" className="mt-2 text-sm text-stone-600">
        Loading the changes...
      </p>
    );
  }
  if (detail.isError) {
    return (
      <div role="alert" className="mt-2 text-sm">
        <p className="text-stone-700">The changes didn&apos;t load.</p>
        <button type="button" onClick={() => void detail.refetch()} className={`mt-2 ${BUTTON}`}>
          Try again
        </button>
      </div>
    );
  }
  const changes = detail.data.changes;
  if (changes.length === 0) {
    return <p className="mt-2 text-sm text-stone-600">Nothing changed against version {parent}.</p>;
  }
  return (
    <div className="mt-2 text-sm">
      <p className="text-xs text-stone-600">Against version {parent}:</p>
      <ul aria-label={`Changes in version ${String(number)}`} className="mt-1 space-y-3">
        {changes.map((change, index) => (
          <li key={index}>
            <p className="font-medium">{change.title}</p>
            {change.context !== null && (
              <p className="text-xs text-stone-600">In the bullet: {change.context}</p>
            )}
            <Values change={change} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * Asks before restoring, in place: restoring saves a copy of the version as a new one, and the
 * versions in between stay (FR-PRF-3). Focus starts on Cancel, and Escape cancels.
 */
function ConfirmRestore({
  number,
  current,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  number: number;
  current: number;
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
      className="mt-3 rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-950"
    >
      <p id={id} className="font-medium">
        Restore version {number}? Your profile becomes a copy of it, saved as version {current + 1}.
        The versions in between stay in the history.
      </p>
      {error !== null && (
        <p role="alert" className="mt-2 text-red-800">
          {failureMessage("restore it", error)}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <button type="button" onClick={onConfirm} disabled={pending} className={PRIMARY_BUTTON}>
          Restore
        </button>
        <button ref={cancel} type="button" onClick={onCancel} className={BUTTON}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function VersionRow({
  version,
  current,
  onRestored,
}: {
  version: VersionSummary;
  current: number;
  onRestored: (message: string) => void;
}) {
  const [shown, setShown] = useState(false);
  const { editing: confirming, button: restoreButton, open, close } = useEditing();
  const restore = useRestore();
  const titleId = useId();
  const changesId = useId();
  const { number, parent } = version;
  return (
    <li className="rounded-lg border border-stone-200 bg-white p-3 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3">
        <h3 id={titleId} className="font-medium">
          Version {number}
          {number === current && (
            <>
              {" "}
              <span className="font-normal text-stone-600">(current)</span>
            </>
          )}
        </h3>
        <p className="text-xs text-stone-600">{dateAndTime(version.createdAt)}</p>
      </div>
      <p className="mt-1 text-sm text-stone-700">{versionSummary(version)}</p>
      <p className="text-xs text-stone-600">{AUTHOR_LABELS[version.author]}</p>
      <div className="mt-2 flex gap-3">
        {parent !== null && (
          <button
            type="button"
            aria-expanded={shown}
            aria-controls={changesId}
            aria-describedby={titleId}
            onClick={() => {
              setShown(!shown);
            }}
            className={TEXT_BUTTON}
          >
            {shown ? "Hide changes" : "Show changes"}
          </button>
        )}
        {number !== current && (
          <button
            ref={restoreButton}
            type="button"
            onClick={() => {
              restore.reset();
              open();
            }}
            className={TEXT_BUTTON}
          >
            Restore version {number}
          </button>
        )}
      </div>
      {parent !== null && (
        <div id={changesId} hidden={!shown}>
          {shown && <VersionChanges number={number} parent={parent} />}
        </div>
      )}
      {confirming && (
        <ConfirmRestore
          number={number}
          current={current}
          pending={restore.isPending}
          error={restore.error}
          onConfirm={() => {
            restore.mutate(
              { number, baseVersion: current },
              {
                onSuccess: (page) => {
                  onRestored(
                    `Restored version ${String(number)} as version ${String(page.version?.number)}.`,
                  );
                  close();
                },
              },
            );
          }}
          onCancel={close}
        />
      )}
    </li>
  );
}

/**
 * Every version of the profile, newest first: who saved it and what it did, what it changed
 * against its parent, and restoring it as a new version (FR-PRF-2, FR-PRF-3).
 */
export function VersionHistory({
  current,
  titleId,
  onRestored,
}: {
  /** The version the page shows, which is the newest. */
  current: number;
  titleId: string;
  onRestored: (message: string) => void;
}) {
  const versions = useQuery(versionsQuery);
  return (
    <section aria-labelledby={titleId}>
      <h2 id={titleId} className="scroll-mt-4 border-b border-stone-200 pb-1 text-lg font-semibold">
        History
      </h2>
      <p className="mt-3 text-sm text-stone-600">
        Every version of your profile, newest first. Restoring one saves a copy of it as a new
        version, and the versions in between stay.
      </p>
      {versions.isPending ? (
        <p role="status" className="mt-3 text-sm text-stone-600">
          Loading the history...
        </p>
      ) : versions.isError ? (
        <div role="alert" className="mt-3 text-sm">
          <p className="text-stone-700">The history didn&apos;t load.</p>
          <button
            type="button"
            onClick={() => void versions.refetch()}
            className={`mt-2 ${BUTTON}`}
          >
            Try again
          </button>
        </div>
      ) : (
        <ol aria-label="Versions" className="mt-4 space-y-3">
          {versions.data.versions.map((version) => (
            <VersionRow
              key={version.number}
              version={version}
              current={current}
              onRestored={onRestored}
            />
          ))}
        </ol>
      )}
    </section>
  );
}
