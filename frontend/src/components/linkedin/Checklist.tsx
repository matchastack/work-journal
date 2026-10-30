import { useId, useState } from "react";

import { failureMessage } from "../../api/auth";
import type { LinkedInPack, MarkDone } from "../../api/linkedin";
import { PRIMARY_BUTTON } from "../ui";
import { SectionItem } from "./SectionItem";

function plural(count: number, one: string): string {
  return `${String(count)} ${one}${count === 1 ? "" : "s"}`;
}

/**
 * The pack's changed sections as a checklist (FR-LIN-2, FR-LIN-3): the owner copies each one
 * into LinkedIn and ticks it, takes off what the profile no longer has, then marks the pack done,
 * which stores the snapshot later packs are compared with. Flagged sections aren't marked done,
 * so they needn't be ticked. The page holds `markDone`, so its outcome outlives the checklist.
 */
export function Checklist({ pack, markDone }: { pack: LinkedInPack; markDone: MarkDone }) {
  const id = useId();
  // What's ticked, for the pack it was ticked on: a pack written since starts unticked.
  const [checked, setChecked] = useState<{ digest: string; keys: ReadonlySet<string> }>({
    digest: pack.digest,
    keys: new Set(),
  });
  const ticked = checked.digest === pack.digest ? checked.keys : new Set<string>();
  const tick = (key: string, on: boolean) => {
    const keys = new Set(ticked);
    if (on) {
      keys.add(key);
    } else {
      keys.delete(key);
    }
    setChecked({ digest: pack.digest, keys });
  };
  const needed = [
    ...pack.sections.filter((section) => !section.flagged).map((section) => section.key),
    ...pack.removed.map((label) => `removed:${label}`),
  ];
  const left = needed.filter((key) => !ticked.has(key)).length;
  return (
    <div className="max-w-3xl">
      {pack.sections.length > 0 && (
        <ol aria-label="Sections to paste" className="mt-6 space-y-3">
          {pack.sections.map((section) => (
            <SectionItem
              // A pack written since starts afresh, without "Copied".
              key={`${pack.digest}:${section.key}`}
              section={section}
              pasted={ticked.has(section.key)}
              onPasted={(on) => {
                tick(section.key, on);
              }}
            />
          ))}
        </ol>
      )}
      {pack.removed.length > 0 && (
        <section
          aria-labelledby={`${id}-removed`}
          className="mt-6 rounded-lg border border-stone-200 bg-white p-4 shadow-sm"
        >
          <h2 id={`${id}-removed`} className="font-medium">
            Take off LinkedIn
          </h2>
          <p className="mt-1 text-sm text-stone-600">
            Your public profile no longer has these, so remove them from LinkedIn.
          </p>
          <ul className="mt-2 space-y-1">
            {pack.removed.map((label) => (
              <li key={label}>
                <label className="inline-flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={ticked.has(`removed:${label}`)}
                    onChange={(event) => {
                      tick(`removed:${label}`, event.target.checked);
                    }}
                    className="size-4 accent-emerald-800"
                  />
                  {label}
                </label>
              </li>
            ))}
          </ul>
        </section>
      )}
      {needed.length === 0 ? (
        <p className="mt-6 text-sm text-stone-600">
          The verifier flagged every section listed, so there&apos;s nothing to mark done:
          they&apos;re written again next time.
        </p>
      ) : (
        <div className="mt-6 rounded-lg border border-stone-200 bg-white p-4">
          <button
            type="button"
            disabled={left > 0 || markDone.isPending}
            onClick={() => {
              markDone.mutate(pack.digest);
            }}
            className={PRIMARY_BUTTON}
          >
            Mark done
          </button>
          <p className="mt-2 text-xs text-stone-600">
            {left > 0
              ? `Tick each item once you've done it on LinkedIn: ${plural(left, "item")} left. Then mark the pack done.`
              : "Marking the pack done saves what your LinkedIn profile now says, so this page lists only what changes after it."}
          </p>
          {markDone.error !== null && (
            <p role="alert" className="mt-2 text-sm text-red-800">
              {failureMessage("mark the pack done", markDone.error)}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
