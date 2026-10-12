import { useEffect, useId, useRef } from "react";

import { failureMessage } from "../../api/auth";
import { useAcceptPassed, useApply, type InboxChangeSet } from "../../api/inbox";
import { dateAndTime } from "../../format";
import { BUTTON, PRIMARY_BUTTON } from "../ui";
import { acceptId } from "./ids";
import { OpReview } from "./OpReview";

function plural(count: number, one: string, many = `${one}s`): string {
  return `${String(count)} ${count === 1 ? one : many}`;
}

/**
 * One proposed change set: its changes to decide on, bulk accept for those the verifier passed,
 * and applying the accepted ones once every change is decided (FR-REV-3). After each decision,
 * focus moves to the next change still waiting, so the review goes on from the keyboard.
 */
export function ChangeSetReview({
  changeSet,
  profileVersion,
  onApplied,
}: {
  changeSet: InboxChangeSet;
  profileVersion: number | null;
  onApplied: (version: number | null) => void;
}) {
  const id = useId();
  const acceptPassed = useAcceptPassed();
  const apply = useApply();
  const focusNext = useRef<{ after: number; waiting: number } | null>(null);
  const ops = changeSet.ops;
  const waiting = ops.filter((op) => op.status === "proposed");
  const accepted = ops.filter((op) => op.status === "accepted").length;
  const rejected = ops.filter((op) => op.status === "rejected").length;
  const flagged = waiting.filter((op) => op.verifierReport?.passed === false).length;
  const passed = waiting.length - flagged;
  const failure = apply.error ?? acceptPassed.error;
  let hint = "Applying makes a new version of your profile; earlier versions stay.";
  if (flagged > 0) {
    hint = `Decide on every change to apply them. The verifier flagged ${plural(flagged, "change")}: decide on ${flagged === 1 ? "it" : "each"} yourself.`;
  } else if (waiting.length > 0) {
    hint = "Decide on every change to apply them.";
  }
  /** Once the decision just made reaches the inbox, focus the next change still waiting after
   * `after`, or Apply when none is. It's noted when the decision is made, since the button
   * that made it is disabled, and so loses focus, while it's saved. */
  const decidedAfter = (after: number) => {
    focusNext.current = { after, waiting: waiting.length };
  };
  useEffect(() => {
    const decided = focusNext.current;
    if (decided === null || waiting.length === decided.waiting) {
      return;
    }
    focusNext.current = null;
    const next = waiting.find((op) => op.position > decided.after) ?? waiting[0];
    const target = next === undefined ? `${id}-apply` : acceptId(changeSet.id, next.position);
    document.getElementById(target)?.focus();
  }, [waiting, changeSet.id, id]);
  const stale =
    changeSet.baseVersion !== null &&
    profileVersion !== null &&
    changeSet.baseVersion !== profileVersion;
  return (
    <section aria-labelledby={`${id}-title`} className="mt-8">
      <div className="rounded-lg border border-stone-200 bg-white p-4">
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {changeSet.summary ?? "Proposed changes"}
        </h2>
        <p className="mt-1 text-sm text-stone-600">
          Proposed {dateAndTime(changeSet.createdAt)}
          {changeSet.author === "ai" ? " from your journal" : " by you"}:{" "}
          {plural(ops.length, "change")}, {accepted} accepted, {rejected} rejected, {waiting.length}{" "}
          waiting.
        </p>
        {stale && (
          <p className="mt-1 text-sm text-stone-600">
            Proposed against version {changeSet.baseVersion}; your profile is now at version{" "}
            {profileVersion}, and the changes apply to it.
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={passed === 0 || acceptPassed.isPending}
            onClick={() => {
              decidedAfter(-1);
              acceptPassed.mutate(changeSet.id);
            }}
            className={BUTTON}
          >
            Accept all that passed ({passed})
          </button>
          <button
            id={`${id}-apply`}
            type="button"
            disabled={waiting.length > 0 || apply.isPending}
            onClick={() => {
              apply.mutate(changeSet.id, { onSuccess: onApplied });
            }}
            className={PRIMARY_BUTTON}
          >
            {accepted > 0
              ? `Apply ${plural(accepted, "accepted change")}`
              : "Close without changes"}
          </button>
        </div>
        <p className="mt-2 text-xs text-stone-600">{hint}</p>
        {failure !== null && (
          <p role="alert" className="mt-2 text-sm text-red-800">
            {failureMessage(apply.error ? "apply the changes" : "accept them", failure)}
          </p>
        )}
      </div>
      <ol className="mt-4 space-y-3">
        {ops.map((op) => (
          <OpReview key={op.position} changeSetId={changeSet.id} op={op} onDecided={decidedAfter} />
        ))}
      </ol>
    </section>
  );
}
