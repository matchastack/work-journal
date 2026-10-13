/** Tailwind classes shared by the pages' controls. */

/** A visible outline for keyboard focus. */
export const FOCUS =
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-emerald-700";

export const BUTTON = `inline-flex items-center rounded border border-stone-300 bg-white px-3 py-1.5 text-sm hover:bg-stone-100 disabled:opacity-50 ${FOCUS}`;
export const PRIMARY_BUTTON = `inline-flex items-center rounded bg-emerald-800 px-3 py-1.5 text-sm font-medium text-white hover:bg-emerald-700 disabled:opacity-50 ${FOCUS}`;
export const DANGER_BUTTON = `inline-flex items-center rounded bg-red-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-800 disabled:opacity-50 ${FOCUS}`;
/** A small action next to what it acts on, such as Edit. */
export const TEXT_BUTTON = `rounded text-xs font-medium text-stone-600 underline-offset-2 hover:text-stone-900 hover:underline ${FOCUS}`;

export const LABEL = "block text-xs font-medium text-stone-700";
export const INPUT = `mt-1 block w-full rounded border border-stone-300 bg-white px-2 py-1.5 text-sm ${FOCUS}`;
