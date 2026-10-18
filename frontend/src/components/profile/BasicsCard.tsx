import { useId } from "react";

import type { Basics, LintFinding } from "../../api/profile";
import { TEXT_BUTTON } from "../ui";
import { BasicsForm } from "./BasicsForm";
import { Findings } from "./Findings";
import { BASICS_PATH, findingsAt } from "./paths";
import { useEditing } from "./useEditing";

function details(basics: Basics): string[] {
  const location = [basics.location?.city, basics.location?.region, basics.location?.countryCode]
    .filter((part) => part)
    .join(", ");
  return [
    basics.email,
    basics.phone,
    basics.url,
    location,
    ...basics.profiles.map(({ network, username, url }) => {
      const link = url ?? username;
      return link ? `${network}: ${link}` : network;
    }),
  ].filter((detail): detail is string => typeof detail === "string" && detail !== "");
}

/** Your name, label, contact details, location, summary and profiles, edited in place. */
export function BasicsCard({
  basics,
  findings,
  baseVersion,
  onSaved,
}: {
  basics: Basics;
  findings: readonly LintFinding[];
  baseVersion: number;
  onSaved: (message: string) => void;
}) {
  const { editing, button, open, close } = useEditing();
  const nameId = useId();
  return (
    <article
      aria-labelledby={nameId}
      className="rounded-lg border border-stone-200 bg-white p-4 shadow-sm"
    >
      <h3 id={nameId} className="font-medium">
        {basics.name}
      </h3>
      {editing ? (
        <div className="mt-3">
          <BasicsForm basics={basics} baseVersion={baseVersion} onSaved={onSaved} onDone={close} />
        </div>
      ) : (
        <>
          {basics.label && <p className="text-sm text-stone-700">{basics.label}</p>}
          <ul
            aria-label="Contact details"
            className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-sm text-stone-600"
          >
            {details(basics).map((detail) => (
              <li key={detail}>{detail}</li>
            ))}
          </ul>
          {basics.summary && <p className="mt-2 text-sm text-stone-700">{basics.summary}</p>}
          <Findings findings={findingsAt(findings, BASICS_PATH)} path={BASICS_PATH} />
          <button
            ref={button}
            type="button"
            aria-label="Edit basics"
            onClick={open}
            className={`mt-2 ${TEXT_BUTTON}`}
          >
            Edit
          </button>
        </>
      )}
    </article>
  );
}
