import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Chart } from "../components/Chart";
import { Badge, Button, Card, Empty, ErrorBox, Field, Grid, Note, PageHeader, Prov, Skeleton, Stat } from "../components/ui";
import { engine } from "../lib/bridge";
import { fmtDate, fmtFeature, isNum } from "../lib/format";
import { useEngine, useGlobalRange } from "../lib/hooks";

const STATE_LABEL: Record<string, string> = { sleep: "Asleep", exercise: "Exercise", active: "Moving", calm_low_movement: "Calm, low movement",
  elevated_low_movement: "Elevated HR, low movement", awake: "Awake (no step data)", no_data: "No data" };

export default function Stress() {
  const range = useGlobalRange();
  const start = range.days < 14 ? new Date(Date.parse(range.end) - 29 * 86400000).toISOString().slice(0, 10) : range.start;
  const sum = useEngine<any>("stress.summary", { start, end: range.end }, { enabled: !!range.status });
  const [day, setDay] = useState<string | null>(null);
  const sel = day ?? range.end;
  const tl = useEngine<any>("stress.timeline", { date: sel }, { enabled: !!range.status });
  if (sum.error) return <ErrorBox error={sum.error} />;
  if (!sum.data) return <Skeleton h={500} />;
  const d = sum.data.daily as any[];
  const dates = d.map((x) => x.date);
  const corr = sum.data.subjective_vs_physiological;
  return (
    <div data-testid="stress-page">
      <PageHeader title="Stress & Nervous System" subtitle={sum.data.note} />
      <Grid cols={2}>
        <Card title="Elevated heart rate at low movement" subtitle="Minutes per day · bars; your self-rated stress (1–10) shown in the chart below" actions={<Prov kind="calculated" />}>
          <Chart height={230} title="Elevated heart rate at low movement" zoom onClick={(p: any) => p?.name && setDay(p.name)}
            context={{ metrics: ["arousal_min", "j:stress"], start, end: range.end, title: "Physiological arousal vs reported stress" }}
            table={{ columns: ["Date", "Elevated min", "Self-rated stress"], rows: d.map((x) => [x.date, x.arousal_min == null ? null : Math.round(x.arousal_min), x["j:stress"]]) }}
            option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: dates, axisLabel: { formatter: (v: string) => v.slice(5) } }, yAxis: { type: "value", name: "min" },
              series: [{ type: "bar", data: d.map((x) => ({ value: x.arousal_min, itemStyle: { color: x.date === sel ? p.s2 : p.s3, borderRadius: [4, 4, 0, 0] } })), barMaxWidth: 12 }] })} />
          <Chart height={120} title="Self-rated stress" option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: dates, axisLabel: { formatter: (v: string) => v.slice(5) } },
            yAxis: { type: "value", min: 1, max: 10, name: "stress" }, series: [{ type: "line", data: d.map((x) => x["j:stress"]), connectNulls: false, symbolSize: 6, lineStyle: { color: p.s4, width: 2 }, itemStyle: { color: p.s4 } }] })} />
        </Card>
        <Card title={`Intraday timeline · ${fmtDate(sel, { weekday: "long", day: "numeric", month: "short" })}`} subtitle="15-minute blocks: heart rate, coloured by state" testId="stress-timeline">
          {!tl.data ? <Skeleton h={260} /> : tl.data.status === "insufficient_data" ? <Empty title="Insufficient data">{tl.data.missing_inputs.join(", ")}</Empty> : (
            <>
              <Chart height={240} title="Heart-rate state timeline" option={(p) => {
                const pts = tl.data.points;
                const col: Record<string, string> = { sleep: p.s2, exercise: p.s4, active: p.s1, calm_low_movement: p.muted, elevated_low_movement: p.s3, awake: p.border, no_data: p.border };
                return { legend: { show: false }, xAxis: { type: "category", data: pts.map((x: any) => new Date(x.t).toTimeString().slice(0, 5)) },
                  yAxis: { type: "value", name: "bpm", scale: true },
                  tooltip: { formatter: (x: any) => { const pt = pts[x[0].dataIndex]; return `${x[0].name} · ${pt.hr ?? "—"} bpm · ${STATE_LABEL[pt.state]}${pt.elevation != null ? ` (${pt.elevation > 0 ? "+" : ""}${pt.elevation} vs usual)` : ""}`; } },
                  series: [{ type: "bar", data: pts.map((x: any) => ({ value: x.hr, itemStyle: { color: col[x.state], borderRadius: [2, 2, 0, 0] } })), barCategoryGap: "15%" }] };
              }} />
              <div className="mt-2 flex flex-wrap gap-3 text-[11px] text-fg2">
                {["sleep", "exercise", "active", "calm_low_movement", "elevated_low_movement"].map((s) => (
                  <span key={s} className="flex items-center gap-1"><i className="inline-block h-2 w-3 rounded-sm" style={{ background: getComputedStyle(document.documentElement).getPropertyValue(
                    { sleep: "--series-2", exercise: "--series-4", active: "--series-1", calm_low_movement: "--text-muted", elevated_low_movement: "--series-3" }[s]!).trim() }} />{STATE_LABEL[s]}</span>))}
              </div>
              <Note>{tl.data.interpretation_note} Reference: your usual low-movement heart rate {fmtFeature("hr", tl.data.summary.reference_lowmove_hr, "bpm")}.</Note>
            </>
          )}
        </Card>
        <Card title="Workday vs weekend" subtitle="Averages over the selected period">
          <table className="w-full text-xs"><thead className="text-muted"><tr><th className="text-left font-normal" /><th className="text-right font-normal">Weekdays</th><th className="text-right font-normal">Weekend</th></tr></thead>
            <tbody>{[["arousal_min", "Elevated HR at low movement (min)"], ["lowmove_hr_median", "Low-movement HR"], ["rhr", "Resting HR"], ["hrv_night_rmssd", "Overnight HRV"], ["j:stress", "Self-rated stress"], ["j:mood", "Mood"]].map(([k, l]) => (
              <tr key={k} className="border-t border-line"><td className="py-1.5 text-fg2">{l}</td><td className="num text-right">{fmtFeature(k, sum.data.weekday[k])}</td><td className="num text-right">{fmtFeature(k, sum.data.weekend[k])}</td></tr>))}</tbody></table>
          <div className="mt-3 flex gap-6">
            <Stat label="Morning elevated (avg/day)" value={isNum(sum.data.morning_vs_afternoon.morning_elevated_min_avg) ? `${Math.round(sum.data.morning_vs_afternoon.morning_elevated_min_avg)} min` : "—"} sub="06:00–12:00" />
            <Stat label="Afternoon elevated (avg/day)" value={isNum(sum.data.morning_vs_afternoon.afternoon_elevated_min_avg) ? `${Math.round(sum.data.morning_vs_afternoon.afternoon_elevated_min_avg)} min` : "—"} sub={`12:00–18:00 · last ${sum.data.morning_vs_afternoon.days} days`} />
          </div>
        </Card>
        <Card title="How you feel vs what your body shows" subtitle="Self-rated stress vs elevated HR minutes (same day)">
          {corr.r == null ? <Empty title="Not enough days with both values">{corr.explanation}</Empty> : (
            <>
              <div className="mb-2 flex items-center gap-3"><Stat label="Spearman ρ" value={corr.r.toFixed(2)} sub={`95% CI ${corr.ci[0].toFixed(2)} to ${corr.ci[1].toFixed(2)} · n = ${corr.n}`} />
                <Badge tone={corr.verdict === "inconclusive" ? "neutral" : "accent"}>{corr.verdict.replaceAll("_", " ")}</Badge></div>
              <Chart height={200} title="Stress vs arousal" option={(p) => ({ legend: { show: false }, tooltip: { trigger: "item", formatter: (x: any) => `${x.value[2]}: stress ${x.value[0]}, ${Math.round(x.value[1])} min` },
                xAxis: { type: "value", name: "self-rated stress", min: 1, max: 10 }, yAxis: { type: "value", name: "min" },
                series: [{ type: "scatter", symbolSize: 9, itemStyle: { color: p.s3, borderColor: p.surface, borderWidth: 2 },
                  data: d.filter((x) => x["j:stress"] != null && x.arousal_min != null).map((x) => [x["j:stress"], x.arousal_min, x.date]) }] })} />
              <Note>{corr.explanation}</Note>
            </>
          )}
        </Card>
        <StressJournal date={range.end} />
        <Card title="Suggestions" subtitle="General, low-risk ideas — not treatment">
          <ul className="list-disc space-y-1 pl-5 text-xs text-fg2">
            <li>Short movement breaks (5–10 min walk) during long sedentary blocks often lower heart rate afterwards — check your timeline after trying it.</li>
            <li>A few minutes of slow breathing (about 6 breaths per minute) is a simple relaxation exercise.</li>
            <li>Consistent sleep timing tends to support steadier resting heart rate.</li>
            <li>If you feel persistently overwhelmed, anxious or low, talk to a healthcare professional.</li>
          </ul>
        </Card>
      </Grid>
    </div>
  );
}

function StressJournal({ date }: { date: string }) {
  const qc = useQueryClient();
  const day = useEngine<any>("journal.day_get", { date });
  const [vals, setVals] = useState<Record<string, any>>({});
  const [note, setNote] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const cur = (k: string) => vals[k] ?? day.data?.entries?.[k]?.value_num ?? day.data?.entries?.[k]?.value_text ?? null;
  const save = async () => {
    await engine("journal.day_set", { date, values: vals, note: note ?? day.data?.note ?? null });
    setSaved(true); setVals({});
    qc.invalidateQueries();
  };
  const scale = (k: string, label: string) => (
    <Field label={`${label}: ${cur(k) ?? "—"}`}>
      <input type="range" min={1} max={10} step={1} value={cur(k) ?? 5} aria-label={label}
        onChange={(e) => { setSaved(false); setVals({ ...vals, [k]: Number(e.target.value) }); }} className="w-full accent-[var(--accent)]" />
    </Field>
  );
  return (
    <Card title={`Stress journal · ${fmtDate(date)}`} subtitle="Quick self-report (stored locally; private habits never go to cloud AI)">
      <div className="grid grid-cols-2 gap-3">
        {scale("stress", "Stress")}{scale("mood", "Mood")}{scale("energy", "Perceived energy")}{scale("sleepiness", "Sleepiness")}
        <Field label="Workload">
          <select className="w-full rounded-lg border border-line bg-s2 px-2 py-1.5 text-[13px]" value={cur("workload") ?? ""} onChange={(e) => { setSaved(false); setVals({ ...vals, workload: e.target.value || null }); }}>
            <option value="">—</option>{["light", "normal", "heavy", "day off"].map((w) => <option key={w}>{w}</option>)}</select>
        </Field>
        <Field label="Note"><input className="w-full rounded-lg border border-line bg-s2 px-2 py-1.5 text-[13px]" value={note ?? day.data?.note ?? ""} onChange={(e) => { setSaved(false); setNote(e.target.value); }} maxLength={5000} /></Field>
      </div>
      <div className="mt-3 flex items-center gap-2"><Button variant="primary" onClick={save} disabled={!Object.keys(vals).length && note == null}>Save</Button>{saved && <span className="text-xs text-good">Saved</span>}</div>
    </Card>
  );
}
