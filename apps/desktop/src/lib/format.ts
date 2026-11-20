export const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function fmtDuration(min: number | null | undefined): string {
  if (!isNum(min)) return "—";
  const m = Math.round(Math.abs(min));
  return `${min < 0 ? "−" : ""}${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}

export function fmtClock(minFromMidnight: number | null | undefined): string {
  if (!isNum(minFromMidnight)) return "—";
  const m = ((Math.round(minFromMidnight) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

export function fmtNum(v: number | null | undefined, digits = 0, unit?: string): string {
  if (!isNum(v)) return "—";
  const s = v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
  return unit && unit !== "count" && unit !== "score" ? `${s} ${unit}` : s;
}

/** Format a feature value using its unit (minutes become durations, clock features become times). */
export function fmtFeature(key: string, v: number | null | undefined, unit?: string): string {
  if (!isNum(v)) return "—";
  if (/(bedtime|wake|midpoint)_min$/.test(key)) return fmtClock(v);
  if (unit === "min" && Math.abs(v) >= 60) return fmtDuration(v);
  if (unit === "min") return `${Math.round(v)} min`;
  if (unit === "%") return `${v.toFixed(1)} %`;
  if (unit === "count" || key === "steps") return Math.round(v).toLocaleString();
  if (unit === "m") return v >= 1000 ? `${(v / 1000).toFixed(2)} km` : `${Math.round(v)} m`;
  const digits = Math.abs(v) < 10 ? 1 : 0;
  return fmtNum(v, digits, unit);
}

export function fmtDate(iso: string | null | undefined, opts: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" }) {
  if (!iso) return "—";
  const d = new Date(iso.length === 10 ? `${iso}T12:00:00` : iso);
  return d.toLocaleDateString(undefined, opts);
}

export function fmtDelta(cur: number | null | undefined, prev: number | null | undefined, key: string, unit?: string): string {
  if (!isNum(cur) || !isNum(prev)) return "—";
  const d = cur - prev;
  if (/(bedtime|wake|midpoint)_min$/.test(key) || unit === "min") return `${d >= 0 ? "+" : "−"}${Math.round(Math.abs(d))} min`;
  const digits = Math.abs(d) < 10 ? 1 : 0;
  return `${d >= 0 ? "+" : "−"}${Math.abs(d).toFixed(digits)}${unit && unit !== "count" ? ` ${unit}` : ""}`;
}

export function zLabel(z: number | null | undefined): string {
  if (!isNum(z)) return "no baseline yet";
  if (z >= 1.5) return "well above usual";
  if (z >= 0.5) return "above usual";
  if (z > -0.5) return "typical";
  if (z > -1.5) return "below usual";
  return "well below usual";
}

export const CATEGORY_LABEL: Record<string, string> = {
  well_above_usual: "Well above your usual", above_usual: "Above your usual", typical: "Typical for you",
  below_usual: "Below your usual", well_below_usual: "Well below your usual", insufficient_data: "Insufficient data",
};
