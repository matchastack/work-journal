import { useId } from "react";

import type { Bullet, LintFinding, RoleType } from "../../api/profile";
import { TEXT_BUTTON } from "../ui";
import { BulletForm } from "./BulletForm";
import { Findings } from "./Findings";
import { STRENGTH_LABELS, VERIFICATION_LABELS } from "./labels";
import { useEditing } from "./useEditing";

const BADGE = "rounded bg-white px-1.5 py-0.5 ring-1";

function statusBadge(bullet: Bullet): string | null {
  if (bullet.status === "benched") {
    return `Benched: ${bullet.statusReason ?? ""}`;
  }
  if (bullet.status === "planned") {
    return bullet.statusReason ? `Planned: ${bullet.statusReason}` : "Planned";
  }
  return null;
}

/**
 * A bullet with its status, strength, verification and tags, and the resume-rule findings about
 * it. Benched and planned bullets never show on a resume (FR-PRF-4), so they're muted. It's
 * edited in place.
 */
export function BulletItem({
  bullet,
  path,
  findings,
  roleTypes,
  baseVersion,
  onSaved,
}: {
  bullet: Bullet;
  path: string;
  findings: readonly LintFinding[];
  roleTypes: readonly RoleType[];
  baseVersion: number;
  onSaved: (message: string) => void;
}) {
  const { editing, button, open, close } = useEditing();
  const textId = useId();
  if (editing) {
    return (
      <li className="rounded border border-stone-200 bg-stone-50 p-3">
        <BulletForm
          bullet={bullet}
          roleTypes={roleTypes}
          baseVersion={baseVersion}
          onSaved={onSaved}
          onDone={close}
        />
      </li>
    );
  }
  const status = statusBadge(bullet);
  const verification = VERIFICATION_LABELS[bullet.verification];
  return (
    <li className="rounded border border-stone-200 bg-stone-50 p-3">
      <p id={textId} className={status === null ? "" : "text-stone-500"}>
        {bullet.text}
      </p>
      <ul aria-label="About this bullet" className="mt-2 flex flex-wrap gap-1.5 text-xs">
        {status !== null && <li className={`${BADGE} text-amber-900 ring-amber-200`}>{status}</li>}
        <li className={`${BADGE} text-stone-700 ring-stone-200`}>
          Strength: {STRENGTH_LABELS[bullet.strength]}
        </li>
        <li className={`${BADGE} text-stone-700 ring-stone-200`}>
          {bullet.verificationNote ? `${verification}: ${bullet.verificationNote}` : verification}
        </li>
        {bullet.tags.length > 0 && (
          <li className={`${BADGE} text-stone-700 ring-stone-200`}>
            Tags: {bullet.tags.join(", ")}
          </li>
        )}
      </ul>
      <Findings findings={findings} path={path} />
      <button
        ref={button}
        type="button"
        aria-label="Edit bullet"
        aria-describedby={textId}
        onClick={open}
        className={`mt-2 ${TEXT_BUTTON}`}
      >
        Edit
      </button>
    </li>
  );
}
