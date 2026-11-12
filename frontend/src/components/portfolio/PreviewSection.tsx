import { PREVIEW_URL, type PortfolioView } from "../../api/portfolio";
import { SettingsSection } from "../settings/SettingsSection";
import { FOCUS } from "../ui";

/**
 * The page as it's published, or as publishing it now would show it, from the API. The frame
 * runs no scripts, so the page's menu and project filters work only in the new tab. `version`
 * reloads it after each change.
 */
export function PreviewSection({ view, version }: { view: PortfolioView; version: number }) {
  return (
    <SettingsSection
      title="Preview"
      description={
        view.published
          ? "Your page as visitors see it now."
          : "Your page as visitors will see it once you publish it."
      }
    >
      {view.version === null ? (
        <p className="text-sm text-stone-600">Nothing to preview until there&apos;s a profile.</p>
      ) : (
        <div className="space-y-2">
          <iframe
            key={version}
            src={PREVIEW_URL}
            title="Preview of your portfolio page"
            sandbox="allow-same-origin allow-downloads"
            className="h-[36rem] w-full rounded border border-stone-300 bg-white"
          />
          <a
            href={PREVIEW_URL}
            target="_blank"
            rel="noreferrer"
            className={`inline-block text-sm font-medium text-emerald-800 underline underline-offset-2 hover:text-emerald-700 ${FOCUS}`}
          >
            Open the preview in a new tab
          </a>
        </div>
      )}
    </SettingsSection>
  );
}
