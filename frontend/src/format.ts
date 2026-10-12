/**
 * Dates, times and metrics as the web app writes them, in the browser's time zone. They're
 * built from parts rather than with `toLocaleString`, whose times can hold a narrow no-break
 * space: everything the app writes is ASCII (FR-WRT-6).
 */

import type { Metric, Qualifier } from "./api/journal";

const MONTHS = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

function shortMonth(date: Date): string {
  return MONTHS[date.getMonth()]?.slice(0, 3) ?? "";
}

/** The month a moment falls in, e.g. `2026-10`. */
export function monthKey(iso: string): string {
  const date = new Date(iso);
  return `${String(date.getFullYear())}-${pad(date.getMonth() + 1)}`;
}

/** A month such as `2026-10` as `October 2026`. */
export function monthTitle(key: string): string {
  const [year = "", month = ""] = key.split("-");
  return `${MONTHS[Number(month) - 1] ?? month} ${year}`;
}

/** E.g. `17:05`. */
export function clockTime(iso: string): string {
  const date = new Date(iso);
  return `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** E.g. `Sun 4 Oct, 17:05`. */
export function dayAndTime(iso: string): string {
  const date = new Date(iso);
  return `${DAYS[date.getDay()] ?? ""} ${String(date.getDate())} ${shortMonth(date)}, ${clockTime(iso)}`;
}

/** E.g. `4 Oct 2026, 17:05`. */
export function dateAndTime(iso: string): string {
  const date = new Date(iso);
  return `${String(date.getDate())} ${shortMonth(date)} ${String(date.getFullYear())}, ${clockTime(iso)}`;
}

/** A fact's `YYYY-MM` date as `Oct 2026`. */
export function factMonth(yearMonth: string): string {
  const [year = "", month = ""] = yearMonth.split("-");
  return `${MONTHS[Number(month) - 1]?.slice(0, 3) ?? month} ${year}`;
}

const QUALIFIER_WORDS: Record<Qualifier, string> = {
  exact: "",
  approximately: "about ",
  at_least: "at least ",
  more_than: "more than ",
  at_most: "up to ",
  less_than: "less than ",
};

function number(value: number): string {
  return value.toLocaleString("en-US", { maximumFractionDigits: 6 });
}

/** E.g. `export duration: about 50 to 12 minutes`, `accuracy: 93%` or `team: 2-3`. */
export function describeMetric(metric: Metric): string {
  const { value } = metric;
  let numbers: string;
  if (value.kind === "single") {
    numbers = number(value.value);
  } else if (value.kind === "change") {
    numbers = `${number(value.before)} to ${number(value.after)}`;
  } else {
    numbers = `${number(value.low)}-${number(value.high)}`;
  }
  const unit = metric.unit ?? "";
  const joined = unit === "%" || unit === "x" ? unit : unit ? ` ${unit}` : "";
  return `${metric.subject}: ${QUALIFIER_WORDS[metric.qualifier]}${numbers}${joined}`;
}
