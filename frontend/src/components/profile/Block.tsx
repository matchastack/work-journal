import { useId, type ReactNode } from "react";

import { TEXT_BUTTON } from "../ui";
import { useEditing } from "./useEditing";

/**
 * Part of the profile, shown with an Edit button that opens its form in place: a page card
 * (`card`) such as Skills, or a block inside a card (`block`) such as a role's tailoring rules.
 */
export function Block({
  title,
  kind,
  editLabel,
  form,
  children,
}: {
  title: string;
  kind: "card" | "block";
  /** The Edit button's name: `Edit tailoring rules`. */
  editLabel: string;
  /** The form, given what closes it. */
  form: (onDone: () => void) => ReactNode;
  children: ReactNode;
}) {
  const { editing, button, open, close } = useEditing();
  const titleId = useId();
  const Heading = kind === "card" ? "h3" : "h4";
  return (
    <section
      aria-labelledby={titleId}
      className={
        kind === "card"
          ? "rounded-lg border border-stone-200 bg-white p-4 shadow-sm"
          : "mt-3 border-t border-stone-100 pt-3"
      }
    >
      <Heading
        id={titleId}
        className={kind === "card" ? "font-medium" : "text-sm font-medium text-stone-800"}
      >
        {title}
      </Heading>
      {editing ? (
        <div className="mt-2">{form(close)}</div>
      ) : (
        <>
          <div className="mt-1 text-sm text-stone-700">{children}</div>
          <button ref={button} type="button" onClick={open} className={`mt-2 ${TEXT_BUTTON}`}>
            {editLabel}
          </button>
        </>
      )}
    </section>
  );
}

/** Label: value pairs, leaving out empty values. */
export function Facts({ label, facts }: { label: string; facts: [string, ReactNode][] }) {
  const shown = facts.filter(([, value]) => value !== null && value !== "" && value !== false);
  if (shown.length === 0) {
    return null;
  }
  return (
    <dl aria-label={label} className="grid gap-x-3 gap-y-1 sm:grid-cols-[auto_1fr]">
      {shown.map(([term, value]) => (
        <div key={term} className="contents">
          <dt className="text-stone-500">{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}
