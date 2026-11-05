import { describe, expect, it } from "vitest";
import { formatDate, formatDateTime, formatDuration, formatRelative, formatTime, parseInstant, setTimeLocale, timestampName } from "../src/ui/time";

const WARSAW = { timeZone: "Europe/Warsaw" };

describe("shared time formatter", () => {
  it("reads UTC strings, naive strings (as UTC) and epoch seconds identically", () => {
    const iso = parseInstant("2026-11-03T16:51:00Z")!.getTime();
    expect(parseInstant("2026-11-03T16:51:00+00:00")!.getTime()).toBe(iso);
    expect(parseInstant("2026-11-03 16:51")!.getTime()).toBe(iso);
    expect(parseInstant(iso / 1000)!.getTime()).toBe(iso);
    expect(parseInstant("garbage")).toBeNull();
    expect(parseInstant(null)).toBeNull();
  });

  it("shows the viewer's zone: 16:51 UTC is 17:51 in Warsaw in November (UTC+1)", () => {
    expect(formatTime("2026-11-03T16:51:00Z", { ...WARSAW, locale: "pl-PL" })).toBe("17:51");
    expect(formatDate("2026-11-03T16:51:00Z", { ...WARSAW, locale: "pl-PL" })).toBe("03.11.2026");
    expect(formatDateTime("2026-11-03T16:51:00Z", { ...WARSAW, locale: "pl-PL" })).toContain("17:51");
  });

  it("follows daylight saving time: same UTC hour, different local hour across the boundary", () => {
    // Europe/Warsaw: clocks go forward on 2026-03-29 (UTC+1 -> UTC+2).
    expect(formatTime("2026-03-28T12:00:00Z", WARSAW)).toBe("13:00");
    expect(formatTime("2026-03-29T12:00:00Z", WARSAW)).toBe("14:00");
    expect(formatTime("2026-03-29T00:30:00Z", WARSAW)).toBe("01:30");
    expect(formatTime("2026-03-29T01:30:00Z", WARSAW)).toBe("03:30");
  });

  it("formats relative time and durations in both locales", () => {
    const now = Date.parse("2026-11-03T17:00:00Z") / 1000;
    expect(formatRelative("2026-11-03T16:55:00Z", now, { locale: "pl-PL" })).toMatch(/5 min.* temu/);
    expect(formatRelative("2026-11-03T16:55:00Z", now, { locale: "en" })).toMatch(/5 min.* ago/);
    expect(formatDuration(5100, { locale: "pl-PL" })).toBe("1 godz. 25 min");
    expect(formatDuration(5100, { locale: "en" })).toBe("1 h 25 min");
    expect(formatDuration(40)).toBe("40 s");
    expect(formatDuration(null)).toBe("");
  });

  it("uses the module locale unless overridden", () => {
    setTimeLocale("pl-PL");
    expect(formatDuration(3700)).toBe("1 godz. 1 min");
    setTimeLocale("en");
    expect(formatDuration(3700)).toBe("1 h 1 min");
  });

  it("names are stamped with local, not UTC, time", () => {
    const now = "2026-11-03T16:51:00Z";
    expect(timestampName("tournament", now, WARSAW)).toBe("tournament-2026-11-03 17:51");
    expect(timestampName("tournament", now, { timeZone: "UTC" })).toBe("tournament-2026-11-03 16:51");
    expect(timestampName("t", "2026-03-29T01:30:00Z", WARSAW)).toBe("t-2026-03-29 03:30");
  });
});
