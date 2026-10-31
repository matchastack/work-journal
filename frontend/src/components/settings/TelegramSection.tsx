import { useQuery } from "@tanstack/react-query";
import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState } from "react";

import { failureMessage } from "../../api/auth";
import { settingsQuery, useNewTelegramLink, type TelegramDeepLink } from "../../api/settings";
import { clockTime, dateAndTime } from "../../format";
import { BUTTON, PRIMARY_BUTTON } from "../ui";
import { SettingsSection } from "./SettingsSection";

/** How often the page checks whether the chat got linked, while a link is shown. */
export const LINK_CHECK_MS = 3000;
/** The longest delay a browser timer takes; a longer one would fire at once. */
const LONGEST_TIMER_MS = 2 ** 31 - 1;

interface Shown {
  link: TelegramDeepLink;
  /** When the chat was linked as the link was made, to tell when a new link is used. */
  linkedBefore: string | null;
}

/**
 * The Telegram link (FR-SET-1): whether the chat is linked, and a one-time link to the bot as a
 * deep link and a QR code. While the link is shown, the page checks every few seconds whether
 * it was used.
 */
export function TelegramSection() {
  const [shown, setShown] = useState<Shown | null>(null);
  const [justLinked, setJustLinked] = useState(false);
  const settings = useQuery({
    ...settingsQuery,
    refetchInterval: shown === null ? false : LINK_CHECK_MS,
  });
  const newLink = useNewTelegramLink();
  const telegram = settings.data?.telegram;

  // A new link, once used, links the chat: the link goes, and the page says so.
  const linkedAt = telegram?.linked ? telegram.linkedAt : null;
  if (shown !== null && linkedAt !== null && linkedAt !== shown.linkedBefore) {
    setShown(null);
    setJustLinked(true);
  }

  // The link stops working when it expires, so it goes then.
  const expiresAt = shown?.link.expiresAt;
  useEffect(() => {
    if (expiresAt === undefined) {
      return;
    }
    const timer = setTimeout(
      () => {
        setShown(null);
      },
      Math.min(Math.max(0, new Date(expiresAt).getTime() - Date.now()), LONGEST_TIMER_MS),
    );
    return () => {
      clearTimeout(timer);
    };
  }, [expiresAt]);

  if (telegram === undefined) {
    return null;
  }
  const makeLink = () => {
    setJustLinked(false);
    newLink.mutate(undefined, {
      onSuccess: (link) => {
        setShown({ link, linkedBefore: linkedAt });
      },
    });
  };
  return (
    <SettingsSection
      title="Telegram"
      description="The bot journals only for the chat linked to your account. Linking another chat moves the link there."
    >
      <p>
        {telegram.linked && telegram.linkedAt !== null ? (
          <>
            <span className="font-medium text-emerald-800">Linked</span> since{" "}
            {dateAndTime(telegram.linkedAt)}.
          </>
        ) : (
          <span className="font-medium">Not linked yet.</span>
        )}
      </p>
      <p role="status" className="mt-1 text-sm text-emerald-800">
        {justLinked ? "Your chat is linked. Send the bot a note to try it." : ""}
      </p>
      {shown === null ? (
        <button
          type="button"
          onClick={makeLink}
          disabled={newLink.isPending}
          className={`mt-3 ${telegram.linked ? BUTTON : PRIMARY_BUTTON}`}
        >
          {newLink.isPending ? "Making a link..." : telegram.linked ? "Relink" : "Link Telegram"}
        </button>
      ) : (
        <div className="mt-3 flex flex-col gap-4 rounded border border-stone-200 bg-stone-50 p-4 sm:flex-row sm:items-center">
          <QRCodeSVG
            value={shown.link.url}
            size={176}
            marginSize={2}
            title="QR code of the link to the bot"
            role="img"
            className="shrink-0 rounded bg-white"
          />
          <div className="text-sm">
            <p>
              Open the link in Telegram, or scan the code with your phone&apos;s camera, then
              press <strong>Start</strong>. It works once, until {clockTime(shown.link.expiresAt)}
              .
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <a
                href={shown.link.url}
                target="_blank"
                rel="noreferrer"
                className={PRIMARY_BUTTON}
              >
                Open in Telegram
              </a>
              <button type="button" onClick={makeLink} className={BUTTON}>
                Make a new link
              </button>
            </div>
            <p className="mt-2 text-xs text-stone-500">Waiting for the bot to link your chat...</p>
          </div>
        </div>
      )}
      {newLink.isError && (
        <p role="alert" className="mt-2 text-sm text-red-700">
          {failureMessage("make a link", newLink.error)}
        </p>
      )}
    </SettingsSection>
  );
}
