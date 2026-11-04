import { Link } from "react-router";

import { failureMessage } from "../../api/auth";
import { usePublish, useUnpublish, type PortfolioView, type TermMatch } from "../../api/portfolio";
import { dateAndTime } from "../../format";
import { SettingsSection } from "../settings/SettingsSection";
import { BUTTON, FOCUS, PRIMARY_BUTTON } from "../ui";

const LINK = `font-medium text-emerald-800 underline underline-offset-2 hover:text-emerald-700 ${FOCUS}`;
const VISIBILITY = { public: "Public", resume_only: "Resume only", private: "Private" } as const;

/** Each confidential term the page names, with where it is (NFR-PRIV-4). */
function ConfidentialMatches({ published, matches }: { published: boolean; matches: TermMatch[] }) {
  const terms = new Map<string, string[]>();
  for (const match of matches) {
    terms.set(match.term, [...(terms.get(match.term) ?? []), match.where]);
  }
  return (
    <div role="alert" className="rounded border border-red-300 bg-red-50 p-3 text-red-900">
      <p className="font-medium">
        {published
          ? "Visitors get \"Page not found\" while your page names a confidential term."
          : "Your page names a confidential term, so it can't be published."}
      </p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5">
        {[...terms].map(([term, places]) => (
          <li key={term}>
            Error: &quot;{term}&quot; in {places.join("; ")}
          </li>
        ))}
      </ul>
      <p className="mt-1">
        Take it out of your profile, or make that text Resume only or Private; the page shows the
        change after your next refresh. Or, if it may be public, remove it from your confidential
        terms in Settings.
      </p>
    </div>
  );
}

/** Whether the page is published, at which address and version, and the button that changes it
 * (FR-PRT-1, FR-PRT-3), with the settings it follows (FR-SET-3). */
export function PublishingSection({ view }: { view: PortfolioView }) {
  const publish = usePublish();
  const unpublish = useUnpublish();
  const error = publish.error ?? unpublish.error;
  const pending = publish.isPending || unpublish.isPending;
  const address = view.path === null ? null : `${globalThis.location.origin}${view.path}`;
  const { settings } = view;
  return (
    <SettingsSection
      title="Publishing"
      description="The page shows the profile version from your latest refresh, and moves to the next refresh's on its own."
    >
      <div className="space-y-3 text-sm">
        {address === null ? (
          <p>
            Your page needs a handle, the name in its address, before you can publish it.{" "}
            <Link to="/settings" className={LINK}>
              Choose one in Settings
            </Link>
            .
          </p>
        ) : (
          <p>
            Address:{" "}
            <a href={address} target="_blank" rel="noreferrer" className={`break-all ${LINK}`}>
              {address}
            </a>
          </p>
        )}
        <p role="status" className="font-medium">
          {view.published && view.publishedAt !== null
            ? `Published since ${dateAndTime(view.publishedAt)}.`
            : "Not published."}
        </p>
        {view.confidential.length > 0 && (
          <ConfidentialMatches published={view.published} matches={view.confidential} />
        )}
        {view.version === null ? (
          <p className="text-stone-600">
            There&apos;s no profile to show yet. Load yours with <code>wj db load-profile</code>{" "}
            first.
          </p>
        ) : (
          <p className="text-stone-600">
            {view.published ? "It shows" : "It will show"} version {view.version} of your profile.
            {view.newestVersion !== null && view.newestVersion > view.version
              ? ` Version ${String(view.newestVersion)} reaches it at your next refresh.`
              : ""}
          </p>
        )}
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-stone-700">
          <dt className="text-stone-500">Open to work</dt>
          <dd>{settings.openToWork ? "Shown on the page" : "Not shown"}</dd>
          <dt className="text-stone-500">Search engines</dt>
          <dd>{settings.indexing ? "May index the page" : "Asked not to index it"}</dd>
          <dt className="text-stone-500">Default visibility</dt>
          <dd>{VISIBILITY[settings.defaultVisibility]}, for your website and online profiles</dd>
        </dl>
        <p>
          <Link to="/settings" className={LINK}>
            Change these in Settings
          </Link>
        </p>
        <div className="flex flex-wrap items-center gap-3 pt-1">
          {view.published ? (
            <button
              type="button"
              disabled={pending}
              onClick={() => {
                publish.reset();
                unpublish.mutate(undefined);
              }}
              className={BUTTON}
            >
              Unpublish
            </button>
          ) : (
            <button
              type="button"
              disabled={
                pending ||
                view.handle === null ||
                view.version === null ||
                view.confidential.length > 0
              }
              onClick={() => {
                unpublish.reset();
                publish.mutate(undefined);
              }}
              className={PRIMARY_BUTTON}
            >
              Publish
            </button>
          )}
          {error !== null && (
            <p role="alert" className="text-red-700">
              {failureMessage(view.published ? "unpublish the page" : "publish the page", error)}
            </p>
          )}
        </div>
        {view.published && (
          <p className="text-xs text-stone-500">
            Unpublishing takes the page down at once, though copies kept by browsers and caches can
            show for up to two minutes.
          </p>
        )}
      </div>
    </SettingsSection>
  );
}
