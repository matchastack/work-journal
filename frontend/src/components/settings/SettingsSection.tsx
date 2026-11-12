import { useId, type ReactNode } from "react";

import { failureMessage } from "../../api/auth";

/** One group of settings, as a card with its heading and what it's for. */
export function SettingsSection({
  title,
  description,
  children,
}: {
  title: string;
  description: ReactNode;
  children: ReactNode;
}) {
  const id = useId();
  return (
    <section
      aria-labelledby={id}
      className="rounded-md border border-stone-200 bg-white p-4 shadow-sm sm:p-6"
    >
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      <div className="mt-1 space-y-1 text-sm text-stone-600">{description}</div>
      <div className="mt-4">{children}</div>
    </section>
  );
}

/** What came of saving a group: saved, or why not. The status stays in place, so a screen
 * reader hears "Saved." when it appears. */
export function SaveResult({
  action,
  saved,
  error,
}: {
  action: string;
  saved: boolean;
  error: Error | null;
}) {
  if (error !== null) {
    return (
      <p role="alert" className="text-sm text-red-700">
        {failureMessage(action, error)}
      </p>
    );
  }
  return (
    <p role="status" className="text-sm text-emerald-800">
      {saved ? "Saved." : ""}
    </p>
  );
}
