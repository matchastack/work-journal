import { Fragment } from "react";

import type { components } from "../api/schema";

export type DiffPart = components["schemas"]["DiffPart"];

/** The text before and after, a word at a time: removed words struck through, added ones
 * underlined. */
export function WordDiff({ parts }: { parts: readonly DiffPart[] }) {
  return (
    <p className="mt-2 rounded bg-stone-50 p-2 leading-relaxed">
      {parts.map((part, index) =>
        part.kind === "same" ? (
          <span key={index}>{part.text}</span>
        ) : part.kind === "removed" ? (
          <Fragment key={index}>
            <del className="bg-red-100 text-red-900">{part.text}</del>
            {/* Keep a removed last word apart from the word that replaces it. */}
            {parts[index + 1]?.kind === "added" && !/\s$/.test(part.text) && " "}
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
