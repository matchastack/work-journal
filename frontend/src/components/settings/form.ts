/** Helpers shared by the Settings page's forms. */

/** The non-empty lines of a text box, each without spaces around it. */
export function lines(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

/** A short explanation under a field. */
export const HINT = "mt-1 text-xs text-stone-500";
