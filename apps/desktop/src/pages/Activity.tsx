import { useMemo } from "react";
import { Chart } from "../components/Chart";
import { Card, Empty, ErrorBox, Grid, Note, PageHeader, Prov, Skeleton, Stat } from "../components/ui";
import { fmtDate, fmtFeature, isNum } from "../lib/format";
import { useEngine, useGlobalRange } from "../lib/hooks";

export default function Activity() {
  const range = useGlobalRange();
  const start = range.days < 14 ? new Date(Date.parse(range.end) - 29 * 86400000).toISOString().slice(0, 10) : range.start;
  const q = useEngine<any>("activity.summary", { start, end: range.end }, { enabled: !!range.status });
  const heat = useMemo(() => {
    const h: any[] = q.data?.steps_heatmap ?? [];
    const days = [...new Set(h.map((x) => x.date))].sort();
    return { days, cells: h.map((x) => [x.hour, days.indexOf(x.date), Math.round(x.steps)]) };
  }, [q.data]);
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Skeleton h={500} />;
  const d = q.data.daily as any[];
  const dates = d.map((x) => x.date);
  const steps = d.map((x) => x.steps).filter(isNum);
  const avg = steps.length ? steps.reduce((a: number, b: number) => a + b, 0) / steps.length : null;
  const ctx = (m: string[], t: string) => ({ metrics: m, start, end: range.end, title: t });
  return (
    <div data-testid="activity-page">
      <PageHeader title="Activity & Fitness" subtitle="Steps, workouts, heart-rate zones and an exploratory training-load model. Steps alone say nothing about medical fitness." />
      <div className="mb-5 grid grid-cols-2 gap-4 rounded-xl border border-line bg-s1 p-4 md:grid-cols-5">
        <Stat label="Avg steps / day" value={avg == null ? "—" : Math.round(avg).toLocaleString()} />
        <Stat label="Workouts" value={q.data.sessions.length} sub={`${q.data.rest_days} rest days`} />
        <Stat label="Exercise time" value={`${Math.round(d.reduce((a, x) => a + (x.exercise_min ?? 0), 0))} min`} />
        <Stat label="Movement consistency" value={isNum(q.data.movement_consistency_cv) ? `CV ${(q.data.movement_consistency_cv * 100).toFixed(0)}%` : "—"} sub="lower = steadier daily steps" />
        <Stat label="Acute : chronic load" value={q.data.load.length ? (q.data.load.at(-1).acute_chronic_ratio?.toFixed(2) ?? "—") : "—"} sub="exploratory" />
      </div>
      <Grid cols={2}>
        <Card explain="ac.steps" title="Daily steps" subtitle="De-duplicated: one source per day (source priority in Settings)">
          <Chart height={240} title="Daily steps" zoom context={ctx(["steps"], "Daily steps")} table={{ columns: ["Date", "Steps"], rows: d.map((x) => [x.date, x.steps == null ? null : Math.round(x.steps)]) }}
            option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: dates, axisLabel: { formatter: (v: string) => v.slice(5) } }, yAxis: { type: "value" },
              series: [{ type: "bar", data: d.map((x) => x.steps), barMaxWidth: 14, itemStyle: { color: p.s1, borderRadius: [4, 4, 0, 0] },
                markLine: avg ? { silent: true, symbol: "none", data: [{ yAxis: avg }], lineStyle: { color: p.muted, type: "dashed" }, label: { formatter: "average", color: p.muted } } : undefined }] })} />
        </Card>
        <Card explain="ac.hourly" title="Steps by hour" subtitle="Last 8 weeks · darker = more steps">
          {!heat.cells.length ? <Empty title="No hourly step data" /> : (
            <Chart height={240} title="Steps heatmap" testId="steps-heatmap" option={(p) => ({ legend: { show: false },
              tooltip: { trigger: "item", formatter: (x: any) => `${heat.days[x.value[1]]} ${x.value[0]}:00 — ${x.value[2]} steps` },
              grid: { left: 50, right: 10, top: 10, bottom: 50 }, xAxis: { type: "category", data: Array.from({ length: 24 }, (_, i) => `${i}`), name: "hour" },
              yAxis: { type: "category", data: heat.days.map((x) => x.slice(5)), axisLabel: { interval: Math.ceil(heat.days.length / 10) } },
              visualMap: { min: 0, max: 3000, orient: "horizontal", left: "center", bottom: 0, itemHeight: 120, textStyle: { color: p.muted }, inRange: { color: ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"] } },
              series: [{ type: "heatmap", data: heat.cells, itemStyle: { borderColor: p.surface, borderWidth: 1 } }] })} />
          )}
        </Card>
        <Card explain="ac.zones" title="Heart-rate zones per day" subtitle="Minutes in % of max HR (Edwards zones)">
          {d.some((x) => x.hr_z1_min != null) ? (
            <Chart height={240} title="HR zones" option={(p) => ({ legend: { data: ["Z1 50–60%", "Z2 60–70%", "Z3 70–80%", "Z4 80–90%", "Z5 90%+"] },
              tooltip: { valueFormatter: (v: any) => `${Math.round(v)} min` }, xAxis: { type: "category", data: dates, axisLabel: { formatter: (v: string) => v.slice(5) } }, yAxis: { type: "value", name: "min" },
              series: [1, 2, 3, 4, 5].map((z, i) => ({ name: ["Z1 50–60%", "Z2 60–70%", "Z3 70–80%", "Z4 80–90%", "Z5 90%+"][i], type: "bar", stack: "z", barMaxWidth: 12,
                data: d.map((x) => x[`hr_z${z}_min`]), itemStyle: { color: ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"][i], borderColor: p.surface, borderWidth: 1 } })) })} />
          ) : <Empty title="Zones unavailable">Set your birth year or max HR in Settings.</Empty>}
        </Card>
        <Card explain="ac.load" title="Training load" subtitle="7-day (acute) and 28-day (chronic) exponentially weighted TRIMP" actions={<Prov kind="estimated" />}>
          {q.data.load.length === 0 ? <Empty title="Insufficient data">Needs at least 14 days with heart-rate zone data in the last 28.</Empty> : (
            <Chart height={240} title="Training load" option={(p) => ({ legend: { data: ["Acute (7 d)", "Chronic (28 d)"] },
              xAxis: { type: "category", data: q.data.load.map((x: any) => x.date), axisLabel: { formatter: (v: string) => v.slice(5) } }, yAxis: { type: "value", name: "TRIMP" },
              series: [{ name: "Acute (7 d)", type: "line", showSymbol: false, data: q.data.load.map((x: any) => x.acute_7d), lineStyle: { color: p.s3, width: 2 }, itemStyle: { color: p.s3 } },
                { name: "Chronic (28 d)", type: "line", showSymbol: false, data: q.data.load.map((x: any) => x.chronic_28d), lineStyle: { color: p.s2, width: 2 }, itemStyle: { color: p.s2 } }] })} />
          )}
          <Note>Training load is computed only from your heart-rate samples; days without heart-rate data count as zero. It is exploratory and not a guide for training decisions.</Note>
        </Card>
        <Card explain="ac.weekly" title="Weekly distribution" subtitle="Average steps and total exercise minutes per week">
          <Chart height={220} title="Weekly steps" option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: q.data.weekly.map((w: any) => w.week.slice(5, 10)) }, yAxis: { type: "value", name: "steps/day" },
            series: [{ type: "bar", data: q.data.weekly.map((w: any) => w.steps), barMaxWidth: 22, itemStyle: { color: p.s1, borderRadius: [4, 4, 0, 0] } }] })} />
          <Chart height={140} title="Weekly exercise" option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: q.data.weekly.map((w: any) => w.week.slice(5, 10)) }, yAxis: { type: "value", name: "min" },
            series: [{ type: "bar", data: q.data.weekly.map((w: any) => w.exercise_min), barMaxWidth: 22, itemStyle: { color: p.s4, borderRadius: [4, 4, 0, 0] } }] })} />
        </Card>
        <Card explain="ac.vs_sleep" title="Activity vs sleep" subtitle="Steps vs the following night's sleep">
          {d.filter((x, i) => x.steps != null && d[i + 1]?.sleep_tst_min != null).length < 5 ? <Empty title="Not enough days" /> : (
            <Chart height={240} title="Activity vs sleep" option={(p) => ({ legend: { show: false }, tooltip: { trigger: "item", formatter: (x: any) => `${x.value[2]}: ${Math.round(x.value[0])} steps → ${(x.value[1] / 60).toFixed(1)} h` },
              xAxis: { type: "value", name: "steps" }, yAxis: { type: "value", name: "next night (min)", scale: true },
              series: [{ type: "scatter", symbolSize: 9, itemStyle: { color: p.s1, borderColor: p.surface, borderWidth: 2 },
                data: d.map((x, i) => [x.steps, d[i + 1]?.sleep_tst_min, x.date]).filter((x) => x[0] != null && x[1] != null) }] })} />
          )}
        </Card>
        <Card explain="ac.history" title="Exercise history" subtitle={`${q.data.sessions.length} sessions`}>
          {q.data.sessions.length === 0 ? <Empty title="No exercise sessions" /> : (
            <div className="max-h-72 overflow-auto"><table className="w-full text-xs"><thead className="sticky top-0 bg-s1 text-muted"><tr>
              {["Date", "Type", "Duration", "Distance", "Active kcal", "Source"].map((h) => <th key={h} className="py-1 text-left font-normal">{h}</th>)}</tr></thead>
              <tbody>{[...q.data.sessions].reverse().map((s: any) => (
                <tr key={s.record_id} className="border-t border-line"><td className="py-1">{fmtDate(s.date, { weekday: "short", day: "numeric", month: "short" })}</td>
                  <td>{s.type.replaceAll("_", " ")}</td><td className="num">{Math.round(s.minutes)} min</td><td className="num">{fmtFeature("distance", s.distance_m, "m")}</td>
                  <td className="num">{s.active_kcal ? Math.round(s.active_kcal) : "—"}</td><td className="text-fg2">{s.provider}</td></tr>))}</tbody></table></div>
          )}
          <Note>GPS routes are never read (not needed, and sensitive).</Note>
        </Card>
      </Grid>
    </div>
  );
}
