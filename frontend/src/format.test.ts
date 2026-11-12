import { describe, expect, it } from "vitest";

import {
  calendarDate,
  clockTime,
  dateAndTime,
  dayAndTime,
  describeMetric,
  factMonth,
  monthKey,
  monthTitle,
} from "./format";

// Tests run with TZ=Asia/Singapore (vite.config.ts), 8 hours ahead of UTC.
describe("dates and times", () => {
  it("are written in the browser's time zone", () => {
    expect(monthKey("2026-09-30T20:30:00Z")).toBe("2026-10");
    expect(dayAndTime("2026-09-30T20:30:00Z")).toBe("Thu 1 Oct, 04:30");
    expect(dateAndTime("2026-10-04T09:05:00Z")).toBe("4 Oct 2026, 17:05");
    expect(clockTime("2026-10-04T01:02:00Z")).toBe("09:02");
  });

  it("name months", () => {
    expect(monthTitle("2026-10")).toBe("October 2026");
    expect(factMonth("2025-01")).toBe("Jan 2025");
  });

  it("write calendar dates as they are, in any time zone", () => {
    expect(calendarDate("2026-10-04")).toBe("4 Oct 2026");
    expect(calendarDate("2026-12-31")).toBe("31 Dec 2026");
  });
});

describe("describeMetric", () => {
  it("writes each kind of value with its qualifier and unit", () => {
    expect(
      describeMetric({
        subject: "export duration",
        value: { kind: "change", before: 50, after: 12 },
        unit: "minutes",
        qualifier: "approximately",
      }),
    ).toBe("export duration: about 50 to 12 minutes");
    expect(
      describeMetric({
        subject: "accuracy",
        value: { kind: "single", value: 93.5 },
        unit: "%",
        qualifier: "exact",
      }),
    ).toBe("accuracy: 93.5%");
    expect(
      describeMetric({
        subject: "requests",
        value: { kind: "range", low: 1500, high: 3000 },
        qualifier: "at_least",
      }),
    ).toBe("requests: at least 1,500-3,000");
  });
});
