import { useState } from "react";
import { Chart, dailyLines } from "../components/Chart";
import { Badge, Card, Empty, ErrorBox, Grid, Note, PageHeader, Prov, Skeleton, Stat } from "../components/ui";
import { CATEGORY_LABEL, fmtDate, fmtFeature, isNum } from "../lib/format";
import { useEngine, useGlobalRange, useSeries } from "../lib/hooks";
import { getVar } from "./SleepLab";

const COMPONENT_LABEL: Record<string, string> = { hrv: "Overnight HRV", rhr: "Resting heart rate", sleep: "Sleep vs need",
  regularity: "Bedtime regularity", load: "Previous-day cardio load" };

export default function Recovery() {
  const range = useGlobalRange();
  const start = range.days < 14 ? new Date(Date.parse(range.end) - 29 * 86400000).toISOString().slice(0, 10) : range.start;
  const rec = useEngine<any[]>("recovery.range", { start, end: range.end }, { enabled: !!range.status });
  const [day, setDay] = useState<string | null>(null);
  const sel = day ?? range.end;
  const energy = useEngine<any>("energy.day", { date: sel }, { enabled: !!range.status });
  const feats = useSeries(["sleep_tst_min", "trimp", "steps", "energy_score_imported"], start, range.end);
  if (rec.error) return <ErrorBox error={rec.error} />;
  if (!rec.data) return <Skeleton h={500} />;
  const rows = rec.data;
  const cur = rows.find((r) => r.local_date === sel);
  const dates = rows.map((r) => r.local_date);
  const valid = rows.filter((r) => r.value != null);
  const ctx = { metrics: ["recovery"], start, end: range.end, title: "Morning recovery estimate" };
  return (
    <div data-testid="recovery-page">
      <PageHeader title="Recovery" subtitle={<>The <b>Morning Recovery Estimate</b> (algorithm recovery.v1) compares last night's physiology with <i>your own</i> recent
        baseline. It is a relative index — 50 is your usual, about ±20 per robust standard deviation — not a medical percentage. Click a day to see why it changed.</>} />
      <Grid cols={2}>
        <Card title="Recovery trend" subtitle={`${valid.length} of ${rows.length} days estimated; the rest lacked inputs or baseline`} actions={<Prov kind="estimated" />}>
          <Chart height={260} title="Recovery trend" zoom context={ctx} onClick={(p: any) => p?.name && setDay(p.name)}
            table={{ columns: ["Date", "Index", "Category"], rows: rows.map((r) => [r.local_date, r.value == null ? null : Math.round(r.value), r.category]) }}
            option={(p) => ({
              legend: { show: false }, xAxis: { type: "category", data: dates, axisLabel: { formatter: (v: string) => v.slice(5) } },
              yAxis: { type: "value", min: 0, max: 100 }, tooltip: { valueFormatter: (v: any) => (v == null ? "insufficient data" : String(Math.round(v))) },
              series: [{ type: "bar", barMaxWidth: 14, data: rows.map((r) => ({ value: r.value, itemStyle: { color: r.local_date === sel ? p.s3 : p.s1, borderRadius: [4, 4, 0, 0] } })),
                markLine: { silent: true, symbol: "none", data: [{ yAxis: 50 }], lineStyle: { color: p.muted, type: "dashed" }, label: { formatter: "your usual", color: p.muted } } }],
            })} />
        </Card>
        <Card title={`Why: ${fmtDate(sel, { weekday: "long", day: "numeric", month: "long" })}`} subtitle="Contribution of each input (weight × personal z-score, capped)" testId="recovery-drivers">
          {!cur ? <Empty title="No estimate for this day" /> : cur.value == null ? (
            <div>
              <Badge tone="warning">{CATEGORY_LABEL.insufficient_data}</Badge>
              <p className="mt-2 text-xs text-fg2">{cur.detail.reason}</p>
              <MissingList missing={cur.detail.inputs_missing} />
            </div>
          ) : (
            <>
              <div className="mb-3 flex gap-6">
                <Stat label={CATEGORY_LABEL[cur.category]} value={Math.round(cur.value)} />
                <Stat label="Difference from baseline" value={`${cur.z >= 0 ? "+" : ""}${cur.z.toFixed(2)} SD`} />
                <Stat label="Data completeness" value={`${Math.round(cur.completeness * 100)}%`} sub={`baseline ${cur.baseline_status}`} />
              </div>
              <Chart height={190} title="Driver contributions" option={(p) => ({
                legend: { show: false }, grid: { left: 140, right: 30, top: 6, bottom: 20, containLabel: false },
                xAxis: { type: "value", axisLabel: { formatter: (v: number) => v.toFixed(1) } },
                yAxis: { type: "category", data: cur.detail.inputs_used.map((u: any) => COMPONENT_LABEL[u.component] ?? u.component), axisLabel: { color: p.text2 } },
                tooltip: { trigger: "item", formatter: (x: any) => { const u = cur.detail.inputs_used[x.dataIndex];
                  return `${COMPONENT_LABEL[u.component]}: value ${fmtFeature(u.feature, u.value)}${u.baseline_median != null ? `, usual ${fmtFeature(u.feature, u.baseline_median)}` : ""}<br/>contribution ${u.contribution.toFixed(2)}`; } },
                series: [{ type: "bar", barMaxWidth: 16, data: cur.detail.inputs_used.map((u: any) => ({ value: u.contribution,
                  itemStyle: { color: u.contribution >= 0 ? p.s1 : p.s3, borderRadius: u.contribution >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4] } })) }],
              })} />
              <table className="mt-2 w-full text-[11px]"><tbody>{cur.detail.inputs_used.map((u: any) => (
                <tr key={u.component} className="border-t border-line"><td className="py-1 text-fg2">{COMPONENT_LABEL[u.component]}</td>
                  <td className="num text-right">{fmtFeature(u.feature, u.value)}</td>
                  <td className="num text-right text-fg2">{u.baseline_median != null ? `usual ${fmtFeature(u.feature, u.baseline_median)}` : u.reference != null ? `need ${fmtFeature(u.feature, u.reference)}` : ""}</td>
                  <td className="num text-right text-fg2">{isNum(u.z) ? `z ${u.z.toFixed(1)}` : ""}</td></tr>))}</tbody></table>
              <MissingList missing={cur.detail.inputs_missing} />
              <Note>Algorithm {cur.algo_version}. {cur.detail.limitations.join(" ")}</Note>
            </>
          )}
        </Card>
        <Card title={`Daily energy estimate · ${fmtDate(sel)}`} subtitle="Exploratory intraday model (energy.v1) — hand-set parameters, not validated" actions={<Prov kind="estimated" />} testId="energy-card">
          {!energy.data ? <Skeleton h={200} /> : energy.data.status === "insufficient_data" ? (
            <Empty title="Insufficient data">Missing: {energy.data.missing_inputs.join(", ")}</Empty>
          ) : (
            <>
              <div className="mb-2 flex gap-6 text-xs">
                <Stat label="At wake" value={Math.round(energy.data.start_value)} /><Stat label="End" value={Math.round(energy.data.end_value)} />
                <Stat label="Drained by activity" value={Math.round(energy.data.drains.activity)} /><Stat label="Drained by elevated HR at rest" value={Math.round(energy.data.drains.arousal)} />
              </div>
              <Chart height={220} title="Energy timeline" option={(p) => {
                const pts = energy.data.points;
                return { legend: { show: false }, xAxis: { type: "category", data: pts.map((x: any) => new Date(x.t).toTimeString().slice(0, 5)) },
                  yAxis: { type: "value", min: 0, max: 100 }, tooltip: { formatter: (x: any) => { const pt = pts[x[0].dataIndex]; return `${x[0].name}: ${pt.value ?? "—"} (${pt.state.replaceAll("_", " ")})`; } },
                  series: [{ type: "line", data: pts.map((x: any) => x.value), showSymbol: false, lineStyle: { color: p.s2, width: 2 }, areaStyle: { color: p.s2 + "26" } }] };
              }} />
              <Note>Inputs used: {energy.data.inputs_used.join(", ")}.{energy.data.missing_inputs.length ? ` Missing: ${energy.data.missing_inputs.join(", ")}.` : ""} Max HR {Math.round(energy.data.hr_max ?? 0)} ({energy.data.hr_max_source}).
                {" "}{energy.data.limitations.join(" ")}</Note>
            </>
          )}
        </Card>
        <Card title="Rest vs activity" subtitle="Sleep duration and cardio load, day by day (separate axes would mislead — shown as two aligned charts)">
          {feats.data ? (
            <>
              <Chart height={120} title="Sleep" option={dailyLines(feats.data.dates, [{ name: "Asleep (h)", data: feats.data.series.sleep_tst_min.map((v) => v == null ? null : v / 60), color: getVar("--series-2") }])} />
              <Chart height={120} title="Cardio load" option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: feats.data!.dates, axisLabel: { formatter: (v: string) => v.slice(5) } },
                yAxis: { type: "value" }, series: [{ type: "bar", name: "TRIMP", data: feats.data!.series.trimp, barMaxWidth: 10, itemStyle: { color: p.s3, borderRadius: [3, 3, 0, 0] } }] })} />
            </>
          ) : <Skeleton h={240} />}
          {feats.data?.series.energy_score_imported?.some((v) => v != null) && <Note>Samsung Energy Score is shown separately in Health Trends; VITALIS never uses it as an input.</Note>}
        </Card>
      </Grid>
    </div>
  );
}

function MissingList({ missing }: { missing: { component: string; reason: string }[] }) {
  if (!missing?.length) return null;
  return (
    <div className="mt-2 text-[11px] text-fg2"><b className="text-fg">Missing inputs:</b>
      <ul className="ml-4 list-disc">{missing.map((m) => <li key={m.component}>{COMPONENT_LABEL[m.component] ?? m.component}: {m.reason}</li>)}</ul></div>
  );
}
