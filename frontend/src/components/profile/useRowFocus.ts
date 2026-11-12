import { useEffect, useRef } from "react";

/**
 * Keeps keyboard focus in a list of rows as rows come and go: a new row's first field takes it,
 * and after a removal, the next row's first field does, or the Add button after the last row.
 */
export function useRowFocus() {
  const rows = useRef(new Map<number, HTMLElement>());
  const addButton = useRef<HTMLButtonElement>(null);
  const pending = useRef<number | "add" | null>(null);
  useEffect(() => {
    const target = pending.current;
    if (target === null) {
      return;
    }
    pending.current = null;
    const field =
      target === "add"
        ? addButton.current
        : (rows.current.get(target)?.querySelector<HTMLElement>("input, select, textarea") ?? null);
    field?.focus();
  });
  return {
    /** For a row's box, by its key. */
    rowRef: (key: number) => (element: HTMLElement | null) => {
      if (element === null) {
        rows.current.delete(key);
      } else {
        rows.current.set(key, element);
      }
    },
    addButton,
    /** Call as the row with `key` is added. */
    added: (key: number) => {
      pending.current = key;
    },
    /** Call as the row with `key` is removed from the rows with `keys`. */
    removed: (keys: readonly number[], key: number) => {
      pending.current = keys[keys.indexOf(key) + 1] ?? "add";
    },
  };
}
