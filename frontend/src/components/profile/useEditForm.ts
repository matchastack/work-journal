import { useState } from "react";

import type { ProfilePage } from "../../api/profile";
import { savedMessage, type Result } from "./drafts";

/** A save, such as `useSaveBullet()`. */
interface Saver<Input> {
  mutate: (input: Input, options: { onSuccess: (page: ProfilePage) => void }) => void;
  error: Error | null;
  isPending: boolean;
}

/**
 * A card's form: its draft, what's wrong with it, and saving it. `form` holds what `EditForm`
 * needs from it.
 *
 * `current` is the card's draft as the page now shows it. The page saves against the newest
 * version it has read, so if the card itself changes while the form is open (the profile was
 * read again after a change in another tab, say), saving the draft would undo that change: the
 * form is then `stale` and can't be saved. A change elsewhere in the profile doesn't matter,
 * since only the fields that differ from the newest version are saved.
 */
export function useEditForm<Draft, Edit, Input>({
  current,
  editOf,
  subject,
  baseVersion,
  mutation,
  input,
  onSaved,
  onDone,
}: {
  current: Draft;
  editOf: (draft: Draft, baseVersion: number) => Result<Edit>;
  /** What the card is, to start a message: `This bullet`. */
  subject: string;
  baseVersion: number;
  mutation: Saver<Input>;
  /** What `mutation` takes to send the edit, such as `{ id, edit }`. */
  input: (edit: Edit) => Input;
  onSaved: (message: string) => void;
  onDone: () => void;
}) {
  const [start] = useState(current);
  const [draft, setDraft] = useState(current);
  const [problem, setProblem] = useState<string | null>(null);
  const stale =
    JSON.stringify(current) === JSON.stringify(start)
      ? null
      : `${subject} changed while you were editing, so saving now would undo that change. Cancel to see the newest version, then edit again.`;
  const submit = () => {
    const result = editOf(draft, baseVersion);
    if ("problem" in result) {
      setProblem(result.problem);
      return;
    }
    setProblem(null);
    mutation.mutate(input(result.edit), {
      onSuccess: (page) => {
        onSaved(savedMessage(page.version?.number, baseVersion));
        onDone();
      },
    });
  };
  return {
    draft,
    change: (changes: Partial<Draft>) => {
      setDraft((previous) => ({ ...previous, ...changes }));
    },
    form: {
      problem,
      stale,
      error: mutation.error,
      pending: mutation.isPending,
      onSubmit: submit,
      onCancel: onDone,
    },
  };
}
