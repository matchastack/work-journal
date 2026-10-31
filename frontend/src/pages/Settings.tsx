import { useQuery } from "@tanstack/react-query";

import { useMe } from "../api/auth";
import { settingsQuery } from "../api/settings";
import { DataSection } from "../components/settings/DataSection";
import { PortfolioForm } from "../components/settings/PortfolioForm";
import { RemindersForm } from "../components/settings/RemindersForm";
import { TelegramSection } from "../components/settings/TelegramSection";
import { ConfidentialTermsForm, StyleNotesForm } from "../components/settings/WritingForms";
import { BUTTON } from "../components/ui";

/**
 * Every preference in one place (FR-SET-1 to FR-SET-5): the Telegram link, the reminders, the
 * portfolio page, the style notes and the confidential terms, each saved on its own; and the
 * owner's data, to download or delete (FR-SET-6).
 */
export function Settings() {
  const settings = useQuery(settingsQuery);
  const me = useMe();
  return (
    <section aria-labelledby="settings-title">
      <title>Settings | Work Journal</title>
      <h1 id="settings-title" className="text-2xl font-semibold">
        Settings
      </h1>
      <p className="mt-2 text-stone-600">
        Your preferences in one place. Each section saves on its own.
      </p>
      {settings.isPending ? (
        <p role="status" className="mt-6 text-stone-600">
          Loading your settings...
        </p>
      ) : settings.isError ? (
        <div role="alert" className="mt-6">
          <p className="text-stone-700">Your settings didn&apos;t load.</p>
          <button
            type="button"
            onClick={() => void settings.refetch()}
            className={`mt-3 ${BUTTON}`}
          >
            Try again
          </button>
        </div>
      ) : (
        <div className="mt-6 max-w-3xl space-y-6">
          <TelegramSection />
          <RemindersForm reminders={settings.data.reminders} />
          <PortfolioForm portfolio={settings.data.portfolio} />
          <StyleNotesForm notes={settings.data.styleNotes} />
          <ConfidentialTermsForm terms={settings.data.confidentialTerms} />
          <DataSection login={me.data?.githubLogin ?? null} />
        </div>
      )}
    </section>
  );
}
