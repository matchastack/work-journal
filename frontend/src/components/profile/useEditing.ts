import { useEffect, useRef, useState } from "react";

/**
 * Whether a card's form, or a question such as Restore's, is open. Once it closes, after a save
 * or a cancel, focus goes back to the button that opened it, so the keyboard carries on where it
 * was.
 */
export function useEditing() {
  const [editing, setEditing] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const returning = useRef(false);
  useEffect(() => {
    if (!editing && returning.current) {
      returning.current = false;
      button.current?.focus();
    }
  }, [editing]);
  return {
    editing,
    /** For the Edit button. */
    button,
    open: () => {
      setEditing(true);
    },
    close: () => {
      returning.current = true;
      setEditing(false);
    },
  };
}
