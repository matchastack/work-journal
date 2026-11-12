import { useId, type ReactNode } from "react";

import { failureMessage } from "../../api/auth";
import {
  useApprovePhrasing,
  useChooseWords,
  useDecideSkill,
  type BulletUse,
  type DraftSkill,
  type DraftWords,
} from "../../api/tailor";
import { VerifierSummary } from "../inbox/VerifierSummary";
import { BUTTON, FOCUS, PRIMARY_BUTTON, TEXT_BUTTON } from "../ui";

const OPTION = "rounded-md border border-stone-200 p-3 has-[:checked]:border-emerald-700";

function Option({
  name,
  value,
  checked,
  disabled,
  label,
  text,
  onChoose,
  children,
}: {
  name: string;
  value: BulletUse;
  checked: boolean;
  disabled: boolean;
  label: string;
  text: string;
  onChoose: (use: BulletUse) => void;
  children?: ReactNode;
}) {
  const id = useId();
  return (
    <div className={OPTION}>
      <div className="flex items-start gap-2">
        <input
          id={id}
          type="radio"
          name={name}
          value={value}
          checked={checked}
          disabled={disabled}
          onChange={() => {
            onChoose(value);
          }}
          className={`mt-1 ${FOCUS}`}
        />
        <label htmlFor={id} className="text-sm">
          <span className="block text-xs font-medium text-stone-600">{label}</span>
          <span className="text-stone-900">{text}</span>
        </label>
      </div>
      {children}
    </div>
  );
}

/** The words of one reworded bullet: its own, the swap worked in, or the new phrasing once
 * it's approved, which saves it as a swap (FR-TLR-3, FR-WRT-5). */
function BulletChoice({
  draftId,
  bullet,
  locked,
}: {
  draftId: string;
  bullet: DraftWords;
  locked: boolean;
}) {
  const name = useId();
  const choose = useChooseWords(draftId);
  const approve = useApprovePhrasing(draftId);
  const busy = locked || choose.isPending || approve.isPending;
  const onChoose = (use: BulletUse) => {
    choose.mutate({ bulletId: bullet.id, use });
  };
  const error = choose.error ?? approve.error;
  return (
    <li>
      <fieldset className="rounded-lg border border-stone-200 bg-white p-4">
        <legend className="px-1 text-sm font-medium">
          {bullet.under}: <span className="font-mono text-xs">{bullet.id}</span>
        </legend>
        <div className="space-y-2">
          <Option
            name={name}
            value="own"
            checked={bullet.use === "own"}
            disabled={busy}
            label="Its own words"
            text={bullet.original}
            onChoose={onChoose}
          />
          {bullet.swap !== null && (
            <Option
              name={name}
              value="swap"
              checked={bullet.use === "swap"}
              disabled={busy}
              label="A saved swap, worked in for this posting"
              text={bullet.swap.text}
              onChoose={onChoose}
            >
              <VerifierSummary report={bullet.swap} />
            </Option>
          )}
          {bullet.phrasing !== null && (
            <Option
              name={name}
              value="phrasing"
              checked={bullet.use === "phrasing"}
              disabled={busy || !bullet.approved}
              label={
                bullet.approved
                  ? "The new phrasing, approved and saved as a swap"
                  : "A new phrasing in the posting's words: approve it to use it"
              }
              text={bullet.phrasing.text}
              onChoose={onChoose}
            >
              <VerifierSummary report={bullet.phrasing} />
              {!locked && !bullet.approved && bullet.phrasing.passed && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => {
                    approve.mutate(bullet.id);
                  }}
                  aria-label={`Approve the new phrasing of ${bullet.id} and save it as a swap`}
                  className={`mt-3 ${PRIMARY_BUTTON}`}
                >
                  Approve and save as a swap
                </button>
              )}
            </Option>
          )}
        </div>
        <p role="status" className="mt-2 text-sm text-red-800 empty:hidden">
          {error ? failureMessage("change it", error) : null}
        </p>
      </fieldset>
    </li>
  );
}

/** The bullets with new words to choose from. Once the draft is logged, `locked`: nothing
 * changes. */
export function WordsSection({
  draftId,
  bullets,
  locked,
}: {
  draftId: string;
  bullets: readonly DraftWords[];
  locked: boolean;
}) {
  return (
    <section aria-labelledby={`${draftId}-words`} className="mt-6">
      <h3 id={`${draftId}-words`} className="font-semibold">
        Wording
      </h3>
      {bullets.length === 0 ? (
        <p className="mt-1 text-sm text-stone-600">
          Every bullet keeps its own words: none was reworded for this posting.
        </p>
      ) : (
        <>
          <p className="mt-1 text-sm text-stone-600">
            Choose each bullet&apos;s words. Only new words that passed the verifier are offered.
          </p>
          <ul className="mt-3 space-y-3">
            {bullets.map((bullet) => (
              <BulletChoice key={bullet.id} draftId={draftId} bullet={bullet} locked={locked} />
            ))}
          </ul>
        </>
      )}
    </section>
  );
}

const DECISIONS = { confirmed: "Confirmed", dropped: "Dropped" } as const;

function SkillChoice({
  draftId,
  skill,
  locked,
}: {
  draftId: string;
  skill: DraftSkill;
  locked: boolean;
}) {
  const decide = useDecideSkill(draftId);
  const where =
    skill.locations.length > 0
      ? `Used in ${skill.locations.join(", ")}.`
      : "Left out of this resume.";
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-stone-200 bg-white px-3 py-2">
      <div>
        <p className="font-medium">
          {skill.name}
          {skill.decision !== null && (
            <span className="ml-2 text-sm font-normal text-stone-600">
              {DECISIONS[skill.decision]}
            </span>
          )}
        </p>
        <p className="text-xs text-stone-600">{where}</p>
      </div>
      <div className="ml-auto flex items-center gap-2">
        {locked ? null : skill.decision === null ? (
          <>
            <button
              type="button"
              disabled={decide.isPending}
              onClick={() => {
                decide.mutate({ skill: skill.name, decision: "confirmed" });
              }}
              aria-label={`Confirm ${skill.name}: I can defend it in an interview`}
              className={BUTTON}
            >
              I can defend it
            </button>
            <button
              type="button"
              disabled={decide.isPending}
              onClick={() => {
                decide.mutate({ skill: skill.name, decision: "dropped" });
              }}
              aria-label={`Drop ${skill.name} from this resume`}
              className={BUTTON}
            >
              Drop it
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={decide.isPending}
            onClick={() => {
              decide.mutate({ skill: skill.name, decision: null });
            }}
            aria-label={`Undo the decision on ${skill.name}`}
            className={TEXT_BUTTON}
          >
            Undo
          </button>
        )}
      </div>
      <p role="status" className="w-full text-sm text-red-800 empty:hidden">
        {decide.isError ? failureMessage("decide", decide.error) : null}
      </p>
    </li>
  );
}

/** Each skill marked verify-before-shipping that the draft lists or names: confirm it or drop
 * it before downloading (R3). */
export function SkillsSection({
  draftId,
  skills,
  locked,
}: {
  draftId: string;
  skills: readonly DraftSkill[];
  locked: boolean;
}) {
  if (skills.length === 0) {
    return null;
  }
  return (
    <section aria-labelledby={`${draftId}-skills`} className="mt-6">
      <h3 id={`${draftId}-skills`} className="font-semibold">
        Skills to confirm
      </h3>
      <p className="mt-1 text-sm text-stone-600">
        You marked these verify-before-shipping. Confirm each only if you can defend it in an
        interview; otherwise drop it. A dropped skill leaves the skills lines, but a bullet that
        names it still breaks the rule: choose other words for it.
      </p>
      <ul className="mt-3 space-y-2">
        {skills.map((skill) => (
          <SkillChoice key={skill.name} draftId={draftId} skill={skill} locked={locked} />
        ))}
      </ul>
    </section>
  );
}
