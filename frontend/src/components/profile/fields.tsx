import { useId, type ReactNode } from "react";

import { failureMessage } from "../../api/auth";
import { BUTTON, INPUT, LABEL, PRIMARY_BUTTON } from "../ui";

/**
 * A card's form, edited in place: Save sends it, and Cancel or Escape closes it. What's wrong
 * with it, or why the save didn't work, shows above the buttons. A `stale` form can't be saved,
 * since saving it would undo a change made since it opened.
 */
export function EditForm({
  label,
  action,
  problem,
  stale,
  error,
  pending,
  onSubmit,
  onCancel,
  children,
}: {
  label: string;
  /** What saving does, for an error: `save the bullet`. */
  action: string;
  problem: string | null;
  /** Why the form can't be saved as it is, if it can't. */
  stale: string | null;
  error: Error | null;
  pending: boolean;
  onSubmit: () => void;
  onCancel: () => void;
  children: ReactNode;
}) {
  const failure = problem ?? (error === null ? null : failureMessage(action, error));
  return (
    <form
      aria-label={label}
      onSubmit={(event) => {
        event.preventDefault();
        onSubmit();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          onCancel();
        }
      }}
      className="space-y-3"
    >
      {children}
      {stale !== null && (
        <p role="alert" className="text-sm text-red-800">
          {stale}
        </p>
      )}
      {failure !== null && (
        <p role="alert" className="text-sm text-red-800">
          {failure}
        </p>
      )}
      <div className="flex gap-2">
        <button type="submit" disabled={pending || stale !== null} className={PRIMARY_BUTTON}>
          Save
        </button>
        <button type="button" onClick={onCancel} className={BUTTON}>
          Cancel
        </button>
      </div>
    </form>
  );
}

interface Field {
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Said under the field, and read with it. */
  hint?: string | undefined;
  required?: boolean | undefined;
  autoFocus?: boolean | undefined;
  className?: string | undefined;
}

function Hint({ id, hint }: { id: string; hint: string | undefined }) {
  return hint === undefined ? null : (
    <p id={id} className="mt-1 text-xs text-stone-500">
      {hint}
    </p>
  );
}

/**
 * A labelled line of text. `inputMode` picks the on-screen keyboard; the browser doesn't check
 * the format, so a value the profile already holds can always be saved again.
 */
export function TextField({
  label,
  value,
  onChange,
  hint,
  required,
  autoFocus,
  className,
  maxLength,
  inputMode,
}: Field & { maxLength: number; inputMode?: "email" | "tel" | "url" | undefined }) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className={LABEL}>
        {label}
      </label>
      <input
        id={id}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        maxLength={maxLength}
        inputMode={inputMode}
        required={required}
        autoFocus={autoFocus}
        aria-describedby={hint === undefined ? undefined : `${id}-hint`}
        className={INPUT}
      />
      <Hint id={`${id}-hint`} hint={hint} />
    </div>
  );
}

/** A labelled box of text. */
export function TextArea({
  label,
  value,
  onChange,
  hint,
  required,
  autoFocus,
  className,
  maxLength,
  rows = 3,
}: Field & { maxLength: number; rows?: number }) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className={LABEL}>
        {label}
      </label>
      <textarea
        id={id}
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        rows={rows}
        maxLength={maxLength}
        required={required}
        autoFocus={autoFocus}
        aria-describedby={hint === undefined ? undefined : `${id}-hint`}
        className={INPUT}
      />
      <Hint id={`${id}-hint`} hint={hint} />
    </div>
  );
}

/** A labelled choice of one of `options`, by value. */
export function SelectField<Value extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: Value;
  options: Record<Value, string>;
  onChange: (value: Value) => void;
  className?: string | undefined;
}) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className={LABEL}>
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => {
          onChange(event.target.value as Value);
        }}
        className={INPUT}
      >
        {(Object.entries(options) as [Value, string][]).map(([option, text]) => (
          <option key={option} value={option}>
            {text}
          </option>
        ))}
      </select>
    </div>
  );
}

/** A labelled month, such as 2026-10. Browsers without a month picker show a text box. */
export function MonthField({ label, value, onChange, hint, required, className }: Field) {
  const id = useId();
  return (
    <div className={className}>
      <label htmlFor={id} className={LABEL}>
        {label}
      </label>
      <input
        id={id}
        type="month"
        placeholder="2026-10"
        value={value}
        onChange={(event) => {
          onChange(event.target.value);
        }}
        required={required}
        aria-describedby={hint === undefined ? undefined : `${id}-hint`}
        className={INPUT}
      />
      <Hint id={`${id}-hint`} hint={hint} />
    </div>
  );
}
