/**
 * The single place that turns stored timestamps into text. Rule for the whole project:
 * stored and transmitted instants are UTC (ISO 8601 with "Z" / "+00:00", or epoch seconds
 * as a number); only this module turns them into text, always in the browser's local time
 * zone. The UI language is the module locale: "en" in the public build, "pl-PL" in the
 * private build (set once with setTimeLocale). Strings without a zone designator are read
 * as UTC, which is how old stored values were written.
 */
export type TimeInput = string | number | Date | null | undefined;
export interface TimeOptions { locale?: string; timeZone?: string }

let moduleLocale = "en";
export function setTimeLocale(locale: string): void { moduleLocale = locale; }
export function timeLocale(): string { return moduleLocale; }

const polish = (locale: string) => locale.toLowerCase().startsWith("pl");
const ZONE = /(Z|[+-]\d{2}(:?\d{2})?)$/i;

/** Epoch seconds (number) or ISO text (UTC when it carries no zone) to a Date; null when unreadable. */
export function parseInstant(value: TimeInput): Date | null {
  if (value == null || value === "") return null;
  let date: Date;
  if (value instanceof Date) date = value;
  else if (typeof value === "number") date = new Date(value * 1000);
  else {
    let text = value.trim().replace(" ", "T");
    if (/^\d{4}-\d{2}-\d{2}$/.test(text)) text += "T00:00:00Z";
    else if (!ZONE.test(text)) text += "Z";
    date = new Date(text);
  }
  return Number.isFinite(date.getTime()) ? date : null;
}

function format(value: TimeInput, style: Intl.DateTimeFormatOptions, options: TimeOptions): string {
  const date = parseInstant(value);
  if (!date) return "";
  return new Intl.DateTimeFormat(options.locale ?? moduleLocale, { ...style, timeZone: options.timeZone }).format(date);
}

export const formatDate = (value: TimeInput, options: TimeOptions = {}) =>
  format(value, { day: "2-digit", month: "2-digit", year: "numeric" }, options);
export const formatTime = (value: TimeInput, options: TimeOptions = {}) =>
  format(value, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }, options);
export const formatDateTime = (value: TimeInput, options: TimeOptions = {}) =>
  format(value, { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }, options);

/** "5 min temu" / "5 min. ago"; future instants read "za 5 min" / "in 5 min.". */
export function formatRelative(value: TimeInput, now: TimeInput = Date.now() / 1000, options: TimeOptions = {}): string {
  const date = parseInstant(value), base = parseInstant(now);
  if (!date || !base) return "";
  const seconds = Math.round((date.getTime() - base.getTime()) / 1000), abs = Math.abs(seconds);
  const [amount, unit]: [number, Intl.RelativeTimeFormatUnit] =
    abs < 45 ? [0, "second"] : abs < 3600 ? [Math.round(seconds / 60), "minute"] : abs < 86400 ? [Math.round(seconds / 3600), "hour"] : [Math.round(seconds / 86400), "day"];
  return new Intl.RelativeTimeFormat(options.locale ?? moduleLocale, { numeric: "auto", style: "short" }).format(amount, unit);
}

/** Length of time in seconds: "1 godz. 25 min", "12 min", "40 s" (English: "1 h 25 min"). */
export function formatDuration(seconds: number | null | undefined, options: TimeOptions = {}): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "";
  const hourWord = polish(options.locale ?? moduleLocale) ? "godz." : "h";
  const minutes = Math.floor(seconds / 60);
  if (minutes >= 60) return `${Math.floor(minutes / 60)} ${hourWord} ${minutes % 60} min`;
  return minutes ? `${minutes} min` : `${Math.floor(seconds)} s`;
}

/** Default name stamped with the viewer's local date and time, e.g. "tournament-2026-11-03 17:51". */
export function timestampName(prefix: string, now: TimeInput = Date.now() / 1000, options: Pick<TimeOptions, "timeZone"> = {}): string {
  const date = parseInstant(now) ?? new Date();
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23", timeZone: options.timeZone,
  }).formatToParts(date);
  const part = (type: string) => parts.find(p => p.type === type)?.value ?? "";
  return `${prefix}-${part("year")}-${part("month")}-${part("day")} ${part("hour")}:${part("minute")}`;
}
