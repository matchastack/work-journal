import { useId, useRef, type ReactNode } from "react";

import { failureMessage } from "../../api/auth";
import { BUTTON, FOCUS, INPUT, LABEL, PRIMARY_BUTTON, TEXT_BUTTON } from "../ui";

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

/** A labelled yes-or-no. */
export function CheckboxField({
  label,
  checked,
  onChange,
  hint,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  hint?: string | undefined;
}) {
  const id = useId();
  return (
    <div>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="checkbox"
          checked={checked}
          onChange={(event) => {
            onChange(event.target.checked);
          }}
          aria-describedby={hint === undefined ? undefined : `${id}-hint`}
          className={`h-4 w-4 rounded border-stone-300 ${FOCUS}`}
        />
        <label htmlFor={id} className="text-sm text-stone-800">
          {label}
        </label>
      </div>
      <Hint id={`${id}-hint`} hint={hint} />
    </div>
  );
}

/**
 * A list of rows in a form, each in a box of its own, named `{noun} 1`, `{noun} 2` and so on,
 * with a Remove button each and an Add button while there's room.
 */
export function Rows<Row extends { key: number }>({
  legend,
  noun,
  rows,
  max,
  empty,
  onChange,
  makeRow,
  children,
}: {
  legend: string;
  /** What a row is, capitalised: `Swap`. */
  noun: string;
  rows: readonly Row[];
  max: number;
  /** Said while there are no rows. */
  empty: string;
  onChange: (rows: Row[]) => void;
  /** A new, blank row with this key. */
  makeRow: (key: number) => Row;
  children: (row: Row, change: (changes: Partial<Row>) => void, index: number) => ReactNode;
}) {
  const nextKey = useRef(Math.max(-1, ...rows.map((row) => row.key)) + 1);
  return (
    <fieldset className="space-y-2">
      <legend className={LABEL}>{legend}</legend>
      {rows.length === 0 && <p className="text-sm text-stone-500">{empty}</p>}
      {rows.map((row, index) => {
        const name = `${noun} ${String(index + 1)}`;
        return (
          <fieldset key={row.key} className="rounded border border-stone-200 bg-white p-2">
            <legend className="px-1 text-xs text-stone-600">{name}</legend>
            {children(
              row,
              (changes) => {
                onChange(rows.map((each) => (each.key === row.key ? { ...each, ...changes } : each)));
              },
              index,
            )}
            <button
              type="button"
              onClick={() => {
                onChange(rows.filter((each) => each.key !== row.key));
              }}
              className={`mt-2 ${TEXT_BUTTON}`}
            >
              Remove {name.toLowerCase()}
            </button>
          </fieldset>
        );
      })}
      {rows.length < max && (
        <button
          type="button"
          onClick={() => {
            onChange([...rows, makeRow(nextKey.current++)]);
          }}
          className={TEXT_BUTTON}
        >
          Add {/^[aeiou]/i.test(noun) ? "an" : "a"} {noun.toLowerCase()}
        </button>
      )}
    </fieldset>
  );
}
