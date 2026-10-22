/**
 * Date, time, and number formatting.
 *
 * Timetables are shown in the course timezone, not the browser timezone, so a
 * professor travelling abroad still sees the wall-clock times of their class.
 */

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const WEEKDAY_OPTIONS = WEEKDAYS.map((label, index) => ({ value: index, label }));

function zone(tz) {
  if (!tz) return undefined;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: tz });
    return tz;
  } catch {
    return undefined;
  }
}

function parts(value, tz, options) {
  if (!value) return null;
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const formatter = new Intl.DateTimeFormat("en-GB", { ...options, timeZone: zone(tz) });
  const result = {};
  for (const part of formatter.formatToParts(date)) result[part.type] = part.value;
  return result;
}

export function timeOfDay(value, tz) {
  const p = parts(value, tz, { hour: "2-digit", minute: "2-digit", hour12: false });
  return p ? `${p.hour}:${p.minute}` : "—";
}

export function dateLabel(value, tz) {
  const p = parts(value, tz, { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  return p ? `${p.weekday}, ${p.day} ${p.month} ${p.year}` : "—";
}

export function dateTimeLabel(value, tz) {
  if (!value) return "—";
  return `${dateLabel(value, tz)} · ${timeOfDay(value, tz)}`;
}

export function shortDate(value, tz) {
  const p = parts(value, tz, { day: "numeric", month: "short" });
  return p ? `${p.day} ${p.month}` : "—";
}

/**
 * Formatting a calendar date (YYYY-MM-DD) in a real timezone shifts it by a day
 * for any course west of UTC, because the bare date parses as UTC midnight.
 * Date-only values are therefore formatted as the calendar date they are.
 */
function dateOnly(value) {
  return typeof value === "string" ? value.slice(0, 10) : value;
}

export function dateOnlyLabel(value) {
  return value ? dateLabel(dateOnly(value), "UTC") : "—";
}

export function shortDateOnly(value) {
  return value ? shortDate(dateOnly(value), "UTC") : "—";
}

/** ISO calendar date (YYYY-MM-DD) as seen in the given timezone. */
export function isoDate(value, tz) {
  const p = parts(value, tz, { year: "numeric", month: "2-digit", day: "2-digit" });
  return p ? `${p.year}-${p.month}-${p.day}` : "";
}

export function todayIso(tz) {
  return isoDate(new Date(), tz);
}

export function addDays(iso, days) {
  const date = new Date(`${iso}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function daysBetween(fromIso, toIso) {
  const from = new Date(`${fromIso}T00:00:00Z`).getTime();
  const to = new Date(`${toIso}T00:00:00Z`).getTime();
  return Math.round((to - from) / 86400000);
}

export function weekdayIndex(iso) {
  const day = new Date(`${iso}T00:00:00Z`).getUTCDay();
  return (day + 6) % 7; // Monday = 0
}

export function weekdayName(index) {
  return WEEKDAYS[Number(index)] || "—";
}

export function minutesLabel(minutes) {
  const total = Math.round(Number(minutes) || 0);
  if (total < 60) return `${total} min`;
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

export function percentLabel(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return `${Math.round(number * 10) / 10}%`;
}

export function relativeDays(value, tz) {
  if (!value) return "—";
  const days = daysBetween(todayIso(tz), isoDate(value, tz));
  if (days === 0) return "today";
  if (days === 1) return "tomorrow";
  if (days === -1) return "yesterday";
  if (days > 0) return `in ${days} days`;
  return `${Math.abs(days)} days ago`;
}

export function sanitizeText(text) {
  return (text ?? "").replace(/\s+/g, " ").trim();
}
