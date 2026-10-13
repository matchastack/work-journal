import { useId, useState } from "react";

import { failureMessage } from "../../api/auth";
import { useDeleteMessage, useEditMessage, type JournalMessage } from "../../api/journal";
import { clockTime, dateAndTime } from "../../format";
import { BUTTON, INPUT, LABEL, PRIMARY_BUTTON, TEXT_BUTTON } from "../ui";
import { ConfirmDelete } from "./ConfirmDelete";

const MESSAGE_LIMIT = 4096;

/** One message in an entry, with its earlier text (FR-JRN-3). It can be edited, and deleted
 * unless it's the entry's only one. */
export function MessageItem({
  message,
  deletable,
}: {
  message: JournalMessage;
  deletable: boolean;
}) {
  const [mode, setMode] = useState<"view" | "edit" | "delete">("view");
  const remove = useDeleteMessage();
  const sent = clockTime(message.sentAt);
  if (mode === "edit") {
    return (
      <MessageForm
        message={message}
        onDone={() => {
          setMode("view");
        }}
      />
    );
  }
  return (
    <div>
      <p className="break-words whitespace-pre-wrap">{message.text}</p>
      <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-stone-500">
        <span>Sent {sent}</span>
        <button
          type="button"
          aria-label={`Edit the message sent at ${sent}`}
          onClick={() => {
            setMode("edit");
          }}
          className={TEXT_BUTTON}
        >
          Edit
        </button>
        {deletable && (
          <button
            type="button"
            aria-label={`Delete the message sent at ${sent}`}
            onClick={() => {
              setMode("delete");
            }}
            className={TEXT_BUTTON}
          >
            Delete
          </button>
        )}
      </div>
      {message.edits.length > 0 && (
        <details className="mt-1 text-xs text-stone-600">
          <summary className="cursor-pointer w-fit rounded">
            {message.edits.length === 1
              ? "Edited once"
              : `Edited ${String(message.edits.length)} times`}
          </summary>
          <ol className="mt-1 space-y-1 border-l-2 border-stone-200 pl-3">
            {message.edits.map((edit, index) => (
              <li key={index}>
                <span className="font-medium">Until {dateAndTime(edit.replacedAt)}:</span>{" "}
                <span className="whitespace-pre-wrap">{edit.text}</span>
              </li>
            ))}
          </ol>
        </details>
      )}
      {mode === "delete" && (
        <ConfirmDelete
          question="Delete this message? This can't be undone."
          confirmLabel="Delete this message"
          pending={remove.isPending}
          error={remove.error}
          onConfirm={() => {
            remove.mutate(message.id);
          }}
          onCancel={() => {
            remove.reset();
            setMode("view");
          }}
        />
      )}
    </div>
  );
}

function MessageForm({ message, onDone }: { message: JournalMessage; onDone: () => void }) {
  const [text, setText] = useState(message.text);
  const edit = useEditMessage();
  const id = useId();
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        edit.mutate({ id: message.id, text }, { onSuccess: onDone });
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          onDone();
        }
      }}
    >
      <label htmlFor={id} className={LABEL}>
        Message sent at {clockTime(message.sentAt)}
      </label>
      <textarea
        id={id}
        value={text}
        onChange={(event) => {
          setText(event.target.value);
        }}
        rows={Math.min(12, text.split("\n").length + 2)}
        maxLength={MESSAGE_LIMIT}
        required
        autoFocus
        className={INPUT}
      />
      <p className="mt-1 text-xs text-stone-500">
        Your earlier text is kept. The facts already saved from this entry stay as they are; edit
        them below.
      </p>
      {edit.isError && (
        <p role="alert" className="mt-1 text-sm text-red-800">
          {failureMessage("save the message", edit.error)}
        </p>
      )}
      <div className="mt-2 flex gap-2">
        <button
          type="submit"
          disabled={edit.isPending || text.trim() === ""}
          className={PRIMARY_BUTTON}
        >
          Save
        </button>
        <button type="button" onClick={onDone} className={BUTTON}>
          Cancel
        </button>
      </div>
    </form>
  );
}
