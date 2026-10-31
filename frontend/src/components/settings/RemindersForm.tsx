import { useId, useState } from "react";

import {
  useSaveReminders,
  type ReminderChange,
  type ReminderView,
  type Weekday,
} from "../../api/settings";
import { INPUT, LABEL, PRIMARY_BUTTON, TEXT_BUTTON } from "../ui";
import { HINT } from "./form";
import { SaveResult, SettingsSection } from "./SettingsSection";

const DAYS: readonly { day: Weekday; name: string }[] = [
  { day: "mon", name: "Monday" },
  { day: "tue", name: "Tuesday" },
  { day: "wed", name: "Wednesday" },
  { day: "thu", name: "Thursday" },
  { day: "fri", name: "Friday" },
  { day: "sat", name: "Saturday" },
  { day: "sun", name: "Sunday" },
];

interface Draft {
  days: Weekday[];
  time: string;
  timeZone: string;
  paused: boolean;
}

function draftOf(reminders: ReminderView): Draft {
  const { schedule } = reminders;
  return {
    days: [...schedule.days],
    time: schedule.time,
    timeZone: schedule.timeZone,
    paused: reminders.paused,
  };
}

/** The time zones the browser knows, for suggestions; the API checks the one chosen. */
function timeZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return [];
  }
}

function browserTimeZone(): string | undefined {
  return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** What's wrong with the draft, if anything. */
function problemOf(draft: Draft): string | null {
  if (draft.days.length === 0) {
    return "Choose at least one day.";
  }
  if (!/^([01]\d|2[0-3]):[0-5]\d$/.test(draft.time)) {
    return "Choose a time, such as 18:00.";
  }
  if (draft.timeZone.trim() === "") {
    return "Choose a time zone, such as Asia/Singapore.";
  }
  return null;
}

/** The reminder schedule, time zone and pause (FR-SET-2). */
export function RemindersForm({ reminders }: { reminders: ReminderView }) {
  const [draft, setDraft] = useState(() => draftOf(reminders));
  const [problem, setProblem] = useState<string | null>(null);
  const save = useSaveReminders();
  const id = useId();
  const field = (name: string) => `${id}-${name}`;
  const change = (changes: Partial<Draft>) => {
    setDraft((current) => ({ ...current, ...changes }));
    setProblem(null);
    save.reset();
  };
  const send = (settings: ReminderChange) => {
    save.mutate(settings, {
      onSuccess: (saved) => {
        setDraft(draftOf(saved));
      },
    });
  };
  const zones = timeZones();
  const here = browserTimeZone();
  const { default: fallback } = reminders;
  const defaultNames = DAYS.filter(({ day }) => fallback.days.includes(day)).map(({ name }) =>
    name.slice(0, 3),
  );
  return (
    <SettingsSection
      title="Reminders"
      description={
        <>
          <p>
            The bot reminds you to journal on these days, unless you journaled in the last 3 days.
            {reminders.paused ? " Reminders are paused now." : ` Now: ${reminders.summary}.`}
          </p>
          <p>
            On the hosted app, reminders go out with the daily tick, at 18:00 Singapore time. For
            another time, move the tick&apos;s hour too.
          </p>
        </>
      }
    >
      <form
        aria-label="Reminders"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          const found = problemOf(draft);
          setProblem(found);
          if (found === null) {
            send({
              days: draft.days,
              time: draft.time,
              timeZone: draft.timeZone.trim(),
              // Unchanged, the pause is left as it is: /pause may have come since the page loaded.
              paused: draft.paused === reminders.paused ? null : draft.paused,
            });
          }
        }}
        className="space-y-4"
      >
        <fieldset>
          <legend className={LABEL}>Days</legend>
          <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
            {DAYS.map(({ day, name }) => (
              <label key={day} className="flex items-center gap-1.5 text-sm">
                <input
                  type="checkbox"
                  checked={draft.days.includes(day)}
                  onChange={(event) => {
                    const chosen = event.target.checked
                      ? [...draft.days, day]
                      : draft.days.filter((other) => other !== day);
                    change({ days: DAYS.map((d) => d.day).filter((d) => chosen.includes(d)) });
                  }}
                />
                {name}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="grid gap-4 sm:grid-cols-[10rem_minmax(0,1fr)]">
          <div>
            <label htmlFor={field("time")} className={LABEL}>
              Time
            </label>
            <input
              id={field("time")}
              type="time"
              step={60}
              required
              value={draft.time}
              onChange={(event) => {
                change({ time: event.target.value });
              }}
              className={INPUT}
            />
          </div>
          <div>
            <label htmlFor={field("zone")} className={LABEL}>
              Time zone
            </label>
            <input
              id={field("zone")}
              list={field("zones")}
              required
              autoComplete="off"
              spellCheck={false}
              value={draft.timeZone}
              onChange={(event) => {
                change({ timeZone: event.target.value });
              }}
              aria-describedby={field("zone-hint")}
              className={INPUT}
            />
            <datalist id={field("zones")}>
              {zones.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
            <p id={field("zone-hint")} className={HINT}>
              A name such as Asia/Singapore.{" "}
              {here !== undefined && here !== draft.timeZone && (
                <button
                  type="button"
                  onClick={() => {
                    change({ timeZone: here });
                  }}
                  className={TEXT_BUTTON}
                >
                  Use this device&apos;s ({here})
                </button>
              )}
            </p>
          </div>
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.paused}
            onChange={(event) => {
              change({ paused: event.target.checked });
            }}
            className="mt-0.5"
          />
          <span>
            Pause reminders. <code>/pause</code> and <code>/resume</code> in Telegram do the same.
          </span>
        </label>
        {problem !== null && (
          <p role="alert" className="text-sm text-red-700">
            {problem}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={save.isPending} className={PRIMARY_BUTTON}>
            Save reminders
          </button>
          {reminders.chosen && (
            <button
              type="button"
              disabled={save.isPending}
              onClick={() => {
                setProblem(null);
                send({
                  days: null,
                  time: null,
                  timeZone: null,
                  paused: draft.paused === reminders.paused ? null : draft.paused,
                });
              }}
              className={TEXT_BUTTON}
            >
              Use the default schedule ({defaultNames.join(", ")} at {fallback.time},{" "}
              {fallback.timeZone})
            </button>
          )}
          <SaveResult action="save the reminders" saved={save.isSuccess} error={save.error} />
        </div>
      </form>
    </SettingsSection>
  );
}
