import { useId, useState } from "react";

import {
  useDeleteFact,
  type JournalEntry,
  type JournalFact,
  type ProjectOption,
  type RoleOption,
} from "../../api/journal";
import { describeMetric, factMonth } from "../../format";
import { FOCUS, TEXT_BUTTON } from "../ui";
import { ConfirmDelete } from "./ConfirmDelete";
import { FactForm } from "./FactForm";
import { hasOwnRole, KIND_LABELS, OWNERSHIP_LABELS, roleText } from "./labels";

/** A fact saved from an entry, shown inline (FR-JRN-5), or read from GitHub, with no entry and
 * links to the pull requests and commits it rests on (FR-GH-10). It can be edited and deleted
 * (FR-EXT-3); deleting one the profile cites names the bullets first (FR-JRN-6). */
export function FactItem({
  fact,
  entry,
  roles,
  projects,
}: {
  fact: JournalFact;
  entry: JournalEntry | null;
  roles: readonly RoleOption[];
  projects: readonly ProjectOption[];
}) {
  const [mode, setMode] = useState<"view" | "edit" | "delete">("view");
  const remove = useDeleteFact();
  const statementId = useId();
  const view = () => {
    setMode("view");
  };
  if (mode === "edit") {
    return (
      <li className="rounded border border-stone-200 bg-stone-50 p-3">
        <FactForm fact={fact} entry={entry} roles={roles} projects={projects} onDone={view} />
      </li>
    );
  }
  const project = projects.find((option) => option.id === fact.projectId);
  const details = [
    KIND_LABELS[fact.kind],
    fact.ownership ? OWNERSHIP_LABELS[fact.ownership] : null,
    fact.date ? factMonth(fact.date) : null,
    entry !== null && hasOwnRole(fact, entry) ? (roleText(fact, roles) ?? "No role") : null,
    fact.projectId
      ? `Project: ${project?.name ?? fact.projectId}`
      : fact.origin === "github" && fact.proposedItem
        ? `New project: ${fact.proposedItem}`
        : null,
    fact.tools.length > 0 ? `Tools: ${fact.tools.join(", ")}` : null,
  ].filter((detail) => detail !== null);
  const cited = fact.citedBy.length;
  return (
    <li className="rounded border border-stone-200 bg-stone-50 p-3">
      <p id={statementId}>{fact.statement}</p>
      <ul aria-label="About this fact" className="mt-2 flex flex-wrap gap-1.5 text-xs">
        {details.map((detail) => (
          <li
            key={detail}
            className="rounded bg-white px-1.5 py-0.5 text-stone-700 ring-1 ring-stone-200"
          >
            {detail}
          </li>
        ))}
      </ul>
      {fact.metrics.length > 0 && (
        <ul aria-label="Metrics" className="mt-2 space-y-0.5 text-xs text-stone-700">
          {fact.metrics.map((metric, index) => (
            <li key={index}>{describeMetric(metric)}</li>
          ))}
        </ul>
      )}
      {fact.outcome && <p className="mt-1 text-xs text-stone-700">Outcome: {fact.outcome}</p>}
      {fact.source && <SourceLinks source={fact.source} />}
      {cited > 0 && (
        <p className="mt-1 text-xs text-emerald-800">
          Cited by {cited === 1 ? "a bullet" : `${String(cited)} bullets`} in your profile
        </p>
      )}
      <div className="mt-2 flex gap-3">
        <button
          type="button"
          aria-label="Edit fact"
          aria-describedby={statementId}
          onClick={() => {
            setMode("edit");
          }}
          className={TEXT_BUTTON}
        >
          Edit
        </button>
        <button
          type="button"
          aria-label="Delete fact"
          aria-describedby={statementId}
          onClick={() => {
            setMode("delete");
          }}
          className={TEXT_BUTTON}
        >
          Delete
        </button>
      </div>
      {mode === "delete" && (
        <ConfirmDelete
          question="Delete this fact? This can't be undone."
          citations={fact.citedBy}
          confirmLabel="Delete this fact"
          pending={remove.isPending}
          error={remove.error}
          onConfirm={() => {
            remove.mutate({ id: fact.id, confirm: cited > 0 });
          }}
          onCancel={() => {
            remove.reset();
            view();
          }}
        />
      )}
    </li>
  );
}

type Source = NonNullable<JournalFact["source"]>;

/** The pull requests and commits a fact from GitHub rests on, as links. */
function SourceLinks({ source }: { source: Source }) {
  return (
    <p className="mt-1 text-xs text-stone-700">
      From{" "}
      {source.items.map((item, index) => (
        <span key={`${item.kind}-${item.ref}`}>
          {index > 0 && ", "}
          <a
            href={item.url}
            rel="noreferrer"
            className={`rounded text-emerald-800 underline underline-offset-2 ${FOCUS}`}
          >
            {item.kind === "pull"
              ? `pull request ${item.ref}`
              : item.kind === "commit"
                ? `commit ${item.ref.slice(0, 7)}`
                : "the repository's description"}
          </a>
        </span>
      ))}
    </p>
  );
}
