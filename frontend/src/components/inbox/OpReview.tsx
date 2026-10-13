import { Fragment, useId, useState } from "react";
import { Link } from "react-router";

import { failureMessage } from "../../api/auth";
import { useDecide, useEditText, type InboxOp, type Output } from "../../api/inbox";
import { BUTTON, DANGER_BUTTON, FOCUS, INPUT, LABEL, PRIMARY_BUTTON, TEXT_BUTTON } from "../ui";
import { acceptId } from "./ids";
import { VerifierSummary } from "./VerifierSummary";

const OUTPUTS: Record<Output, string> = {
  master_resume: "Master resume",
  tailored_resumes: "Tailored resumes",
  portfolio: "Portfolio page",
  linkedin: "LinkedIn",
};

const STATUS: Record<InboxOp["status"], { label: string; className: string }> = {
  proposed: { label: "Waiting", className: "bg-amber-50 text-amber-900 ring-amber-200" },
  accepted: { label: "Accepted", className: "bg-emerald-50 text-emerald-900 ring-emerald-200" },
  rejected: { label: "Rejected", className: "bg-stone-100 text-stone-700 ring-stone-300" },
};

/** The text before and after, a word at a time: removed words struck through, added ones
 * underlined. */
function Diff({ op }: { op: InboxOp }) {
  if (op.diff.length > 0) {
    return (
      <p className="mt-2 rounded bg-stone-50 p-2 leading-relaxed">
        {op.diff.map((part, index) =>
          part.kind === "same" ? (
            <span key={index}>{part.text}</span>
          ) : part.kind === "removed" ? (
            <Fragment key={index}>
              <del className="bg-red-100 text-red-900">{part.text}</del>
              {/* Keep a removed last word apart from the word that replaces it. */}
              {op.diff[index + 1]?.kind === "added" && !/\s$/.test(part.text) && " "}
            </Fragment>
          ) : (
            <ins key={index} className="bg-emerald-100 text-emerald-900 no-underline">
              {part.text}
            </ins>
          ),
        )}
      </p>
    );
  }
  return (
    <dl className="mt-2 grid gap-x-3 gap-y-1 rounded bg-stone-50 p-2 sm:grid-cols-[auto_1fr]">
      {op.before !== null && (
        <>
          <dt className="text-xs font-medium text-stone-600">Before</dt>
          <dd>{op.before}</dd>
        </>
      )}
      {op.after !== null && (
        <>
          <dt className="text-xs font-medium text-stone-600">
            {op.before === null ? "New" : "After"}
          </dt>
          <dd>{op.after}</dd>
        </>
      )}
    </dl>
  );
}

function Sources({ op }: { op: InboxOp }) {
  if (op.sources.length === 0) {
    return null;
  }
  return (
    <div className="mt-3 text-sm">
      <p className="text-xs font-medium text-stone-600">From these facts</p>
      <ul className="mt-1 space-y-1">
        {op.sources.map((source) => (
          <li key={source.id} className="text-stone-700">
            {source.statement ?? <span className="italic">A fact that has been deleted.</span>}{" "}
            {source.entryId ? (
              <Link
                to={`/journal#${source.entryId}`}
                className={`rounded text-emerald-800 underline underline-offset-2 ${FOCUS}`}
              >
                In your journal
              </Link>
            ) : (
              source.statement !== null && (
                <span className="text-stone-500">(from your imported resume)</span>
              )
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}

/**
 * One proposed change (FR-REV-1), with the owner's decision on it (FR-REV-2): accept it, edit
 * its text, which the verifier checks again, or reject it with an optional reason. A decision can
 * be undone until the change set is applied. `onDecided` is told of a decision as it's made,
 * so focus can move on once it's saved. A change that `needed` another, such as a bullet under a
 * new role, is rejected with it, and waits for that rejection to be undone.
 */
export function OpReview({
  changeSetId,
  op,
  needed,
  onDecided,
}: {
  changeSetId: string;
  op: InboxOp;
  needed?: InboxOp | undefined;
  onDecided: (position: number) => void;
}) {
  const [mode, setMode] = useState<"view" | "reject" | "edit">("view");
  const [reason, setReason] = useState("");
  const [text, setText] = useState(op.after ?? "");
  const decide = useDecide();
  const edit = useEditText();
  const id = useId();
  const at = { changeSetId, position: op.position };
  const status = STATUS[op.status];
  const blocker = needed?.status === "rejected" ? needed : undefined;
  const failure = edit.error
    ? failureMessage("check your text", edit.error)
    : decide.error && failureMessage("save your decision", decide.error);
  /** Leave a form, and the error of what was tried in it. */
  const close = () => {
    decide.reset();
    edit.reset();
    setMode("view");
  };
  return (
    <li>
      <article
        aria-labelledby={`${id}-title`}
        className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm"
      >
        <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <h3 id={`${id}-title`} className="font-medium">
            {op.title}
          </h3>
          <span className={`rounded-full px-2 py-0.5 text-xs ring-1 ${status.className}`}>
            {status.label}
          </span>
        </header>
        {op.context !== null && (
          <p className="mt-1 text-sm text-stone-600">About: &quot;{op.context}&quot;</p>
        )}
        {needed !== undefined && (
          <p className="mt-1 text-sm text-stone-600">
            Goes with &quot;{needed.title}&quot;{needed.after === null ? "" : `: ${needed.after}`}.
          </p>
        )}
        <Diff op={op} />
        <p className="mt-2 text-xs text-stone-600">
          {op.outputs.length > 0
            ? `Changes: ${op.outputs.map((output) => OUTPUTS[output]).join(", ")}`
            : "Changes no output: it's kept for the record."}
        </p>
        <Sources op={op} />
        {op.verifierReport !== null && <VerifierSummary report={op.verifierReport} />}
        {op.status === "rejected" && op.reason !== null && (
          <p className="mt-3 text-sm text-stone-700">Your reason: {op.reason}</p>
        )}
        {failure && (
          <p role="alert" className="mt-3 text-sm text-red-800">
            {failure}
          </p>
        )}
        {mode === "reject" ? (
          <form
            className="mt-3"
            onSubmit={(event) => {
              event.preventDefault();
              onDecided(op.position);
              decide.mutate(
                { ...at, status: "rejected", reason: reason.trim() || undefined },
                {
                  onSuccess: () => {
                    setMode("view");
                  },
                },
              );
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                close();
              }
            }}
          >
            <label htmlFor={`${id}-reason`} className={LABEL}>
              Why? (optional: it becomes a style note for future proposals)
            </label>
            <textarea
              id={`${id}-reason`}
              value={reason}
              onChange={(event) => {
                setReason(event.target.value);
              }}
              rows={2}
              maxLength={500}
              autoFocus
              className={INPUT}
            />
            <div className="mt-2 flex gap-2">
              <button type="submit" disabled={decide.isPending} className={DANGER_BUTTON}>
                Reject
              </button>
              <button type="button" onClick={close} className={BUTTON}>
                Cancel
              </button>
            </div>
          </form>
        ) : mode === "edit" ? (
          <form
            className="mt-3"
            onSubmit={(event) => {
              event.preventDefault();
              edit.mutate(
                { ...at, text },
                {
                  onSuccess: () => {
                    setMode("view");
                  },
                },
              );
            }}
            onKeyDown={(event) => {
              if (event.key === "Escape") {
                close();
              }
            }}
          >
            <label htmlFor={`${id}-text`} className={LABEL}>
              Bullet text
            </label>
            <textarea
              id={`${id}-text`}
              value={text}
              onChange={(event) => {
                setText(event.target.value);
              }}
              rows={3}
              maxLength={500}
              required
              autoFocus
              className={INPUT}
            />
            <p className="mt-1 text-xs text-stone-500">
              The verifier checks your text against the facts, then it waits for your decision.
            </p>
            {edit.isPending && (
              <p role="status" className="mt-1 text-sm text-stone-600">
                Checking with the verifier...
              </p>
            )}
            <div className="mt-2 flex gap-2">
              <button
                type="submit"
                disabled={edit.isPending || text.trim() === ""}
                className={PRIMARY_BUTTON}
              >
                Save and check
              </button>
              <button type="button" onClick={close} className={BUTTON}>
                Cancel
              </button>
            </div>
          </form>
        ) : blocker !== undefined ? (
          <p className="mt-3 text-sm text-stone-700">
            Rejected with &quot;{blocker.title}&quot;, which it needs. Undo that rejection to decide
            on this one.
          </p>
        ) : op.status === "proposed" ? (
          <div className="mt-3 flex flex-wrap gap-2">
            <button
              id={acceptId(changeSetId, op.position)}
              type="button"
              disabled={decide.isPending}
              onClick={() => {
                onDecided(op.position);
                decide.mutate({ ...at, status: "accepted" });
              }}
              className={PRIMARY_BUTTON}
            >
              Accept
            </button>
            {op.editable && (
              <button
                type="button"
                onClick={() => {
                  setText(op.after ?? "");
                  close();
                  setMode("edit");
                }}
                className={BUTTON}
              >
                Edit
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                close();
                setMode("reject");
              }}
              className={BUTTON}
            >
              Reject
            </button>
          </div>
        ) : (
          <button
            type="button"
            disabled={decide.isPending}
            onClick={() => {
              setReason(op.reason ?? "");
              decide.mutate({ ...at, status: "proposed" });
            }}
            className={`mt-3 ${TEXT_BUTTON}`}
          >
            Undo
          </button>
        )}
      </article>
    </li>
  );
}
