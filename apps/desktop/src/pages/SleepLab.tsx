import { useQueryClient } from "@tanstack/react-query";
import type { ExplainId } from "../explain/content";
import { useMemo, useState } from "react";
import { Chart, dailyLines, type Palette } from "../components/Chart";
import { Badge, Button, Card, Empty, ErrorBox, Field, Grid, inputCls, Note, PageHeader, Prov, Skeleton, Stat, TabPanel, Tabs } from "../components/ui";
import { engine } from "../lib/bridge";
import { fmtClock, fmtDate, fmtDuration, fmtFeature, isNum } from "../lib/format";
import { useEngine, useGlobalRange } from "../lib/hooks";

const STAGES = [["deep", "Deep"], ["light", "Light"], ["rem", "REM"], ["awake", "Awake"]] as const;

function rolling(v: (number | null)[], n: number) {
  return v.map((_, i) => {
    const w = v.slice(Math.max(0, i - n + 1), i + 1).filter((x): x is number => x != null);
    return w.length >= Math.ceil(n / 2) ? w.reduce((a, b) => a + b, 0) / w.length : null;
  });
}

const VITALS_EXPLAIN: Record<string, ExplainId> = { hr_sleep_mean: "sl.vitals.hr", spo2_night_mean: "sl.vitals.spo2",
  resp_rate_night: "sl.vitals.resp", skin_temp_delta_night: "sl.vitals.skin" };

export default function SleepLab() {
  const range = useGlobalRange();
  const start = range.days < 14 ? new Date(Date.parse(range.end) - 29 * 86400000).toISOString().slice(0, 10) : range.start;
  const q = useEngine<any>("sleep.nights", { start, end: range.end }, { enabled: !!range.status });
  const cons = useEngine<any>("sleep.consistency", { date: range.end, days: Math.max(14, Math.min(365, range.days)) }, { enabled: !!range.status });
  const rec = useEngine<any[]>("recovery.range", { start, end: range.end }, { enabled: !!range.status });
  const [tab, setTab] = useState("nights");
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Skeleton h={500} />;
  const nights: any[] = q.data.nights;
  const dates = nights.map((n) => n.date);
  const col = (k: string) => nights.map((n) => n[k] as number | null);
  const recorded = nights.filter((n) => n.sleep_tst_min != null);
  const avg = (k: string) => { const v = recorded.map((n) => n[k]).filter(isNum); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
  const need = q.data.need_min ?? 450;
  const ctx = (metrics: string[], title: string) => ({ metrics, start, end: range.end, title });
  return (
    <div data-testid="sleep-lab">
      <PageHeader title="Sleep Lab" subtitle={<>{recorded.length} of {nights.length} nights recorded ({start} → {range.end}). Sleep is dated by the morning you woke up.
        Wrist-derived stages are estimates, not clinical polysomnography.</>} />
      <div className="mb-5 grid grid-cols-2 gap-4 rounded-xl border border-line bg-s1 p-4 md:grid-cols-6">
        <Stat label="Avg asleep" value={fmtDuration(avg("sleep_tst_min"))} />
        <Stat label="Avg in bed" value={fmtDuration(avg("sleep_tib_min"))} />
        <Stat label="Efficiency" value={fmtFeature("sleep_efficiency", avg("sleep_efficiency"), "%")} />
        <Stat label="Typical bedtime" value={fmtClock(cons.data?.sleep_bedtime_min?.median)} sub={isNum(cons.data?.sleep_bedtime_min?.sd) ? `± ${Math.round(cons.data.sleep_bedtime_min.sd)} min` : undefined} />
        <Stat label="Typical wake" value={fmtClock(cons.data?.sleep_wake_min?.median)} sub={isNum(cons.data?.sleep_wake_min?.sd) ? `± ${Math.round(cons.data.sleep_wake_min.sd)} min` : undefined} />
        <Stat label="Social jet lag" value={isNum(cons.data?.social_jetlag_min) ? `${Math.round(cons.data.social_jetlag_min)} min` : "—"} sub="weekend vs weekday midpoint" />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "nights", label: "Nights" }, { id: "stages", label: "Stages & timeline" },
        { id: "consistency", label: "Consistency" }, { id: "distribution", label: "Distribution" }, { id: "recovery", label: "Sleep & recovery" },
        { id: "vitals", label: "Overnight vitals" }, { id: "planner", label: "Sleep Planner" }]}>
        <TabPanel value="nights">
          <Grid cols={2}>
            <Card explain="sl.tst" title="Total sleep time" subtitle={`Bars: nightly · line: 7-night average · dashed: your sleep need (${fmtDuration(need)})`}>
              <Chart height={260} title="Total sleep time" zoom context={ctx(["sleep_tst_min"], "Total sleep time")}
                table={{ columns: ["Night", "Asleep (min)"], rows: nights.map((n) => [n.date, n.sleep_tst_min == null ? null : Math.round(n.sleep_tst_min)]) }}
                option={(p) => ({
                  legend: { data: ["Nightly", "7-night average"] },
                  xAxis: { type: "category", data: dates, axisLabel: { formatter: (v: string) => v.slice(5) } },
                  yAxis: { type: "value", axisLabel: { formatter: (v: number) => `${Math.round(v / 60)} h` }, min: 0 },
                  tooltip: { valueFormatter: (v: any) => fmtDuration(v) },
                  series: [{ name: "Nightly", type: "bar", data: col("sleep_tst_min"), barMaxWidth: 14, itemStyle: { color: p.s2, borderRadius: [4, 4, 0, 0] },
                    markLine: { silent: true, symbol: "none", lineStyle: { color: p.muted, type: "dashed" }, data: [{ yAxis: need, name: "need" }], label: { color: p.muted, formatter: "need" } } },
                    { name: "7-night average", type: "line", data: rolling(col("sleep_tst_min"), 7), showSymbol: false, lineStyle: { color: p.s1, width: 2 }, itemStyle: { color: p.s1 } }],
                })} />
            </Card>
            <Card explain="sl.efficiency" title="Sleep efficiency" subtitle="Asleep ÷ time in bed (needs stages)">
              <Chart height={260} title="Sleep efficiency" zoom context={ctx(["sleep_efficiency"], "Sleep efficiency")}
                option={dailyLines(dates, [{ name: "Efficiency", data: col("sleep_efficiency"), color: getVar("--series-1") }],
                  { formatter: (v) => `${v.toFixed(0)}%` })} />
            </Card>
            {col("sleep_score_imported").some((v) => v != null) && (
              <Card explain="sl.score" title="Sleep score history" subtitle="Samsung Health's proprietary score, imported verbatim" actions={<Prov kind="imported" />}>
                <Chart height={220} title="Sleep score" option={dailyLines(dates, [{ name: "Sleep score", data: col("sleep_score_imported"), color: getVar("--series-4") }])} />
              </Card>
            )}
            <Card explain="sl.debt" title="Sleep debt trend" subtitle="Assumption-based estimate" actions={<Prov kind="estimated" />}>
              <Chart height={220} title="Sleep debt" context={ctx(["sleep_debt_min"], "Sleep debt")}
                option={dailyLines(dates, [{ name: "Sleep debt", data: col("sleep_debt_min"), color: getVar("--series-3"), area: true }], { formatter: (v) => fmtDuration(v), min: 0 })} />
              <Note>Assumptions: sleep need {fmtDuration(q.data.debt_assumptions.need_min)} (change in Settings); each night adds (need − sleep);
                debt decays by 15 % per night; nights without data add nothing. This is an illustration of accumulated shortfall, not a physiological measurement.</Note>
            </Card>
          </Grid>
          <Card explain="sl.nights" title="Nights" className="mt-4">
            <div className="max-h-[420px] overflow-auto">
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-s1 text-muted"><tr>{["Night", "Bed", "Wake", "Asleep", "Efficiency", "Latency", "WASO", "Awakenings", "Deep", "REM", "Nap"].map((h) =>
                  <th key={h} className="py-1 text-left font-normal">{h}</th>)}</tr></thead>
                <tbody>{[...nights].reverse().map((n) => (
                  <tr key={n.date} className="border-t border-line">
                    <td className="py-1 text-fg2">{fmtDate(n.date, { weekday: "short", day: "numeric", month: "short" })}</td>
                    {n.sleep_tst_min == null ? <td colSpan={10} className="text-muted">no main sleep recorded</td> : <>
                      <td className="num">{fmtClock(n.sleep_bedtime_min)}</td><td className="num">{fmtClock(n.sleep_wake_min)}</td>
                      <td className="num">{fmtDuration(n.sleep_tst_min)}</td><td className="num">{fmtFeature("e", n.sleep_efficiency, "%")}</td>
                      <td className="num">{n.sleep_latency_min == null ? "n/a" : `${Math.round(n.sleep_latency_min)} min`}</td>
                      <td className="num">{n.sleep_waso_min == null ? "—" : `${Math.round(n.sleep_waso_min)} min`}</td>
                      <td className="num">{n.sleep_awakenings ?? "—"}</td><td className="num">{fmtFeature("d", n.sleep_deep_min, "min")}</td>
                      <td className="num">{fmtFeature("r", n.sleep_rem_min, "min")}</td><td className="num">{n.nap_min ? `${Math.round(n.nap_min)} min` : "—"}</td></>}
                  </tr>))}</tbody>
              </table>
            </div>
            <Note>Latency is shown only when the source session starts with an in-bed awake period; otherwise it cannot be measured and is marked n/a.</Note>
          </Card>
        </TabPanel>
        <TabPanel value="stages"><StagesTab data={q.data} /></TabPanel>
        <TabPanel value="consistency"><ConsistencyTab nights={nights} cons={cons.data} /></TabPanel>
        <TabPanel value="distribution"><DistributionTab nights={nights} need={need} /></TabPanel>
        <TabPanel value="recovery">
          <SleepRecovery nights={nights} rec={rec.data ?? []} />
        </TabPanel>
        <TabPanel value="vitals">
          <Grid cols={2}>
            {[["hr_sleep_mean", "Mean heart rate during sleep", "--series-2"], ["spo2_night_mean", "Overnight SpO2 (mean)", "--series-1"],
              ["resp_rate_night", "Overnight respiratory rate", "--series-4"], ["skin_temp_delta_night", "Skin temperature deviation (source baseline)", "--series-3"]].map(([k, t, c]) => (
              <Card key={k} title={t} subtitle={q.data.info[k]?.unit} explain={VITALS_EXPLAIN[k]}>
                {col(k).some((v) => v != null) ? <Chart height={200} title={t} context={ctx([k], t)} option={dailyLines(dates, [{ name: t, data: col(k), color: getVar(c) }])} />
                  : <Empty title="Not available from your sources">See Data Sources for what your phone shares.</Empty>}
              </Card>
            ))}
          </Grid>
        </TabPanel>
        <TabPanel value="planner"><Planner /></TabPanel>
      </Tabs>
    </div>
  );
}

export function getVar(name: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function StagesTab({ data }: { data: any }) {
  const eps: any[] = data.episodes.filter((e: any) => e.duration_min >= 180);
  const [sel, setSel] = useState(eps.length - 1);
  const ep = eps[sel];
  const nights: any[] = data.nights.filter((n: any) => n.sleep_deep_min != null || n.sleep_light_min != null);
  const color = (p: Palette, s: string) => ({ deep: p.deep, light: p.light, rem: p.rem, awake: p.awake, asleep: p.light, awake_in_bed: p.awake, out_of_bed: p.muted } as any)[s] ?? p.muted;
  const lanes = ["awake", "rem", "light", "deep"];
  return (
    <Grid cols={2}>
      <Card explain="sl.hypnogram" title="Hypnogram" subtitle={ep ? `${fmtDate(ep.wake_date, { weekday: "long", day: "numeric", month: "long" })} · ${fmtDuration(ep.duration_min)}` : "No night selected"}
        actions={eps.length > 0 && <div className="flex gap-1"><Button size="sm" disabled={sel <= 0} onClick={() => setSel(sel - 1)}>‹</Button>
          <Button size="sm" disabled={sel >= eps.length - 1} onClick={() => setSel(sel + 1)}>›</Button></div>}>
        {!ep ? <Empty title="No recent nights" /> : !ep.has_stages ? <Empty title="This night has no stage data">The source supplied only a session start and end.</Empty> : (
          <Chart height={240} title="Hypnogram" testId="hypnogram"
            option={(p) => ({
              tooltip: { trigger: "item", formatter: (x: any) => `${x.data.name}: ${x.data.value[3]}–${x.data.value[4]}` },
              grid: { left: 60, right: 16, top: 10, bottom: 30 }, legend: { show: false },
              xAxis: { type: "time", axisLabel: { formatter: (v: number) => new Date(v).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) } },
              yAxis: { type: "category", data: lanes.map((l) => l.toUpperCase()), splitLine: { show: false } },
              series: [{ type: "custom", renderItem: (params: any, api: any) => {
                const lane = api.value(0);
                const s = api.coord([api.value(1), lane]);
                const e = api.coord([api.value(2), lane]);
                const h = api.size([0, 1])[1] * 0.62;
                return { type: "rect", shape: { x: s[0], y: s[1] - h / 2, width: Math.max(1, e[0] - s[0] - 1), height: h, r: 2 }, style: api.style() };
              }, encode: { x: [1, 2], y: 0 }, data: ep.stages.filter((s: any) => lanes.includes(s.stage) || s.stage === "asleep" || s.stage === "awake_in_bed").map((s: any) => {
                const st = s.stage === "asleep" ? "light" : s.stage === "awake_in_bed" ? "awake" : s.stage;
                const a = Date.parse(s.start), b = Date.parse(s.end);
                return { name: st, value: [lanes.indexOf(st), a, b, new Date(a).toTimeString().slice(0, 5), new Date(b).toTimeString().slice(0, 5)], itemStyle: { color: color(p, st) } };
              }) }],
            })} />
        )}
        <div className="mt-2 flex gap-3 text-[11px] text-fg2">{STAGES.map(([k, l]) => <span key={k} className="flex items-center gap-1"><i className="inline-block h-2 w-3 rounded-sm" style={{ background: getVar(`--stage-${k}`) }} />{l}</span>)}</div>
      </Card>
      <Card explain="sl.stage_durations" title="Stage durations per night" subtitle="Minutes in each stage (legend + table view available)">
        {!nights.length ? <Empty title="No stage data" /> : (
          <Chart height={260} title="Stage durations" zoom
            table={{ columns: ["Night", "Deep", "Light", "REM", "Awake"], rows: nights.map((n) => [n.date, ...["sleep_deep_min", "sleep_light_min", "sleep_rem_min", "sleep_awake_min"].map((k) => n[k] == null ? null : Math.round(n[k]))]) }}
            option={(p) => ({
              legend: { data: STAGES.map(([, l]) => l) }, tooltip: { valueFormatter: (v: any) => `${Math.round(v)} min` },
              xAxis: { type: "category", data: nights.map((n) => n.date), axisLabel: { formatter: (v: string) => v.slice(5) } },
              yAxis: { type: "value", axisLabel: { formatter: (v: number) => `${Math.round(v / 60)} h` } },
              series: STAGES.map(([k, l]) => ({ name: l, type: "bar", stack: "s", barMaxWidth: 16, data: nights.map((n) => n[`sleep_${k}_min`]),
                itemStyle: { color: (p as any)[k], borderColor: p.surface, borderWidth: 1 } })),
            })} />
        )}
      </Card>
      <Card explain="sl.stage_share" title="Stage share" subtitle="Average percentage of time asleep">
        <div className="grid grid-cols-3 gap-3">
          {(["deep", "rem", "light"] as const).map((k) => {
            const v = nights.map((n) => n[`sleep_${k}_pct`]).filter(isNum);
            return <Stat key={k} label={k === "rem" ? "REM" : k[0].toUpperCase() + k.slice(1)} value={v.length ? `${(v.reduce((a, b) => a + b, 0) / v.length).toFixed(0)}%` : "—"} />;
          })}
        </div>
        <Note>Consumer wrist devices classify stages from movement and heart-rate patterns; agreement with lab polysomnography is moderate, especially for deep and REM sleep.</Note>
      </Card>
    </Grid>
  );
}

function ConsistencyTab({ nights, cons }: { nights: any[]; cons: any }) {
  const med = cons?.sleep_bedtime_min?.median;
  const heat = useMemo(() => {
    const cells: [number, number, number][] = [];
    const weeks: string[] = [];
    nights.forEach((n) => {
      if (n.sleep_bedtime_min == null || med == null) return;
      const d = new Date(`${n.date}T12:00:00`);
      const monday = new Date(d); monday.setDate(d.getDate() - ((d.getDay() + 6) % 7));
      const wk = monday.toISOString().slice(0, 10);
      if (!weeks.includes(wk)) weeks.push(wk);
      cells.push([weeks.indexOf(wk), (d.getDay() + 6) % 7, Math.round(Math.abs(n.sleep_bedtime_min - med))]);
    });
    return { cells, weeks };
  }, [nights, med]);
  return (
    <Grid cols={2}>
      <Card explain="sl.bed_wake" title="Bedtime & wake time" subtitle="Clock time per night (one axis: minutes from midnight)">
        <Chart height={260} title="Bedtime and wake time" zoom option={(p) => ({
          legend: { data: ["Bedtime", "Wake time"] },
          xAxis: { type: "category", data: nights.map((n) => n.date), axisLabel: { formatter: (v: string) => v.slice(5) } },
          yAxis: { type: "value", axisLabel: { formatter: (v: number) => fmtClock(v) }, scale: true },
          tooltip: { valueFormatter: (v: any) => fmtClock(v) },
          series: [{ name: "Bedtime", type: "scatter", symbolSize: 8, data: nights.map((n) => n.sleep_bedtime_min), itemStyle: { color: p.s2 } },
            { name: "Wake time", type: "scatter", symbolSize: 8, data: nights.map((n) => n.sleep_wake_min), itemStyle: { color: p.s3 } }] })} />
      </Card>
      <Card explain="sl.heatmap" title="Bedtime consistency heatmap" subtitle={`Minutes away from your median bedtime (${fmtClock(med)}) · darker = less consistent`}>
        {!heat.cells.length ? <Empty title="Not enough nights" /> : (
          <Chart height={260} title="Bedtime consistency" testId="bedtime-heatmap" option={(p) => ({
            tooltip: { trigger: "item", formatter: (x: any) => `Week of ${heat.weeks[x.value[0]]}, ${["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][x.value[1]]}: ${x.value[2]} min off` },
            grid: { left: 40, right: 16, top: 10, bottom: 50 }, legend: { show: false },
            xAxis: { type: "category", data: heat.weeks.map((w) => w.slice(5)), splitArea: { show: false } },
            yAxis: { type: "category", data: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"], splitLine: { show: false } },
            visualMap: { min: 0, max: 120, orient: "horizontal", left: "center", bottom: 0, itemHeight: 120, textStyle: { color: p.muted },
              inRange: { color: ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"] }, text: ["120+ min", "0"] },
            series: [{ type: "heatmap", data: heat.cells, itemStyle: { borderColor: p.surface, borderWidth: 2, borderRadius: 3 } }] })} />
        )}
      </Card>
      <Card explain="sl.weekday" title="Weekday vs weekend" subtitle={cons?.note}>
        <table className="w-full text-xs"><thead className="text-muted"><tr><th className="text-left font-normal">Median</th><th className="text-right font-normal">Weekdays</th><th className="text-right font-normal">Weekend</th></tr></thead>
          <tbody>{[["sleep_bedtime_min", "Bedtime"], ["sleep_wake_min", "Wake"], ["sleep_midpoint_min", "Midpoint"], ["sleep_tst_min", "Asleep"]].map(([k, l]) => (
            <tr key={k} className="border-t border-line"><td className="py-1.5 text-fg2">{l}</td>
              <td className="num text-right">{fmtFeature(k, cons?.weekday?.[k], "min")}</td><td className="num text-right">{fmtFeature(k, cons?.weekend?.[k], "min")}</td></tr>))}</tbody></table>
      </Card>
      <Card explain="sl.sri" title="Sleep Regularity Index" subtitle="Probability of being in the same state (asleep/awake) 24 h apart, scaled −100…100 (14-night window)">
        <Chart height={200} title="Sleep regularity" option={dailyLines(nights.map((n) => n.date), [{ name: "SRI", data: nights.map((n) => n.sleep_sri_14d), color: getVar("--series-1") }])} />
      </Card>
    </Grid>
  );
}

function DistributionTab({ nights, need }: { nights: any[]; need: number }) {
  const v = nights.map((n) => n.sleep_tst_min).filter(isNum);
  const bins = Array.from({ length: 14 }, (_, i) => 240 + i * 30);
  const counts = bins.map((b) => v.filter((x) => x >= b && x < b + 30).length);
  return (
    <Grid cols={2}>
      <Card explain="sl.distribution" title="Sleep duration distribution" subtitle={`${v.length} nights · 30-minute bins`}>
        <Chart height={260} title="Sleep duration distribution" table={{ columns: ["Bin", "Nights"], rows: bins.map((b, i) => [`${fmtDuration(b)}–${fmtDuration(b + 30)}`, counts[i]]) }}
          option={(p) => ({ tooltip: { trigger: "axis" }, legend: { show: false },
            xAxis: { type: "category", data: bins.map((b) => `${(b / 60).toFixed(1)}h`) }, yAxis: { type: "value", minInterval: 1 },
            series: [{ type: "bar", data: counts.map((c, i) => ({ value: c, itemStyle: { color: bins[i] + 15 < need ? p.s3 : p.s2, borderRadius: [4, 4, 0, 0] } })), barCategoryGap: "8%" }] })} />
        <Note>Amber bars are below your configured sleep need ({fmtDuration(need)}).</Note>
      </Card>
      <Card explain="sl.rolling" title="Rolling averages" subtitle="7- and 30-night rolling mean of total sleep">
        <Chart height={260} title="Rolling sleep averages" option={dailyLines(nights.map((n) => n.date), [
          { name: "7-night", data: rolling(nights.map((n) => n.sleep_tst_min), 7), color: getVar("--series-2") },
          { name: "30-night", data: rolling(nights.map((n) => n.sleep_tst_min), 30), color: getVar("--series-3") }], { formatter: (x) => fmtDuration(x) })} />
      </Card>
    </Grid>
  );
}

function SleepRecovery({ nights, rec }: { nights: any[]; rec: any[] }) {
  const byDate = new Map(rec.map((r) => [r.local_date, r.value]));
  const pts = nights.filter((n) => n.sleep_tst_min != null && byDate.get(n.date) != null).map((n) => [n.sleep_tst_min / 60, byDate.get(n.date), n.date]);
  const pts2 = nights.filter((n) => n.sleep_tst_min != null && n.rhr != null).map((n) => [n.sleep_tst_min / 60, n.rhr, n.date]);
  return (
    <Grid cols={2}>
      <Card explain="sl.vs_recovery" title="Sleep duration vs morning recovery" subtitle={`${pts.length} mornings · recovery uses sleep as one input, so some relationship is expected by construction`}>
        {pts.length < 5 ? <Empty title="Not enough mornings with both values" /> : (
          <Chart height={260} title="Sleep vs recovery" option={(p) => ({ tooltip: { trigger: "item", formatter: (x: any) => `${x.value[2]}: ${x.value[0].toFixed(1)} h → ${Math.round(x.value[1])}` },
            legend: { show: false }, xAxis: { type: "value", name: "hours asleep", scale: true }, yAxis: { type: "value", name: "recovery index", min: 0, max: 100 },
            series: [{ type: "scatter", data: pts, symbolSize: 9, itemStyle: { color: p.s1, borderColor: p.surface, borderWidth: 2 } }] })} />
        )}
      </Card>
      <Card explain="sl.vs_rhr" title="Sleep duration vs overnight resting HR">
        {pts2.length < 5 ? <Empty title="Not enough nights" /> : (
          <Chart height={260} title="Sleep vs resting HR" option={(p) => ({ tooltip: { trigger: "item", formatter: (x: any) => `${x.value[2]}: ${x.value[0].toFixed(1)} h, ${Math.round(x.value[1])} bpm` },
            legend: { show: false }, xAxis: { type: "value", name: "hours asleep", scale: true }, yAxis: { type: "value", name: "bpm", scale: true },
            series: [{ type: "scatter", data: pts2, symbolSize: 9, itemStyle: { color: p.s2, borderColor: p.surface, borderWidth: 2 } }] })} />
        )}
        <Note>Use the Correlation Lab for statistics (correlation with confidence interval and effective sample size).</Note>
      </Card>
    </Grid>
  );
}

function Planner() {
  const qc = useQueryClient();
  const [wake, setWake] = useState("06:45");
  const [need, setNeed] = useState("");
  const [routine, setRoutine] = useState("");
  const plan = useEngine<any>("sleep.planner", { wake_time: wake, ...(need ? { need_min: Number(need) * 60 } : {}) }, { enabled: /^\d\d:\d\d$/.test(wake) });
  const adh = useEngine<any>("sleep.adherence");
  const save = async () => {
    await engine("sleep.plan_save", { wake_time: wake, ...(need ? { need_min: Number(need) * 60 } : {}), routine: routine || null });
    qc.invalidateQueries({ queryKey: ["sleep.adherence"] });
  };
  return (
    <Grid cols={2}>
      <Card explain="sl.planner" title="Plan your bedtime" subtitle="Based on your recent sleep efficiency and your sleep need">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Desired wake time"><input type="time" className={inputCls} value={wake} onChange={(e) => setWake(e.target.value)} data-testid="planner-wake" /></Field>
          <Field label="Sleep need (hours)" hint="Blank = your setting"><input className={inputCls} inputMode="decimal" value={need} onChange={(e) => setNeed(e.target.value.replace(/[^\d.]/g, ""))} /></Field>
        </div>
        {plan.data && (
          <div className="mt-4 rounded-lg bg-s2 p-3" data-testid="planner-result">
            <div className="text-xs text-fg2">Suggested bedtime window</div>
            <div className="num text-2xl font-semibold">{plan.data.window[0]} – {plan.data.window[1]}</div>
            <div className="mt-1 text-xs text-fg2">Time in bed {fmtDuration(plan.data.time_in_bed_min)} · efficiency {Math.round(plan.data.assumed_efficiency * 100)}% ({plan.data.efficiency_source})
              {plan.data.typical_bedtime && <> · your typical bedtime {plan.data.typical_bedtime}</>}</div>
            {plan.data.gradual_shift?.length > 0 && <div className="mt-2 text-xs"><b>Shift gradually:</b> {plan.data.gradual_shift.map((s: any) => `${fmtDate(s.day)} ${s.bedtime}`).join(" → ")}</div>}
          </div>
        )}
        <Field label="Routine to try (optional)"><input className={inputCls} value={routine} onChange={(e) => setRoutine(e.target.value)} placeholder="e.g. no screens after 22:00, reading" maxLength={500} /></Field>
        <div className="mt-3"><Button variant="primary" onClick={save} disabled={!plan.data}>Start this plan tomorrow</Button></div>
        {plan.data && <Note>{plan.data.assumptions.join(" ")}</Note>}
      </Card>
      <Card explain="sl.adherence" title="Plan adherence" subtitle="Bedtime within the window ±15 min and wake within ±30 min of target">
        {!adh.data?.plan ? <Empty title="No active plan" /> : (
          <>
            <div className="mb-3 flex gap-6"><Stat label="Adherence" value={adh.data.adherence_rate == null ? "—" : `${Math.round(adh.data.adherence_rate * 100)}%`} sub={`${adh.data.nights_rated} nights rated`} />
              <Stat label="Plan" value={`${adh.data.plan.bedtime_start}–${adh.data.plan.bedtime_end}`} sub={`wake ${adh.data.plan.wake_time}`} /></div>
            {adh.data.plan.routine && <p className="mb-2 text-xs text-fg2">Routine: {adh.data.plan.routine}</p>}
            <ul className="max-h-60 space-y-1 overflow-auto text-xs">{[...adh.data.nights].reverse().map((n: any) => (
              <li key={n.date} className="flex justify-between border-t border-line pt-1"><span>{fmtDate(n.date, { weekday: "short", day: "numeric", month: "short" })}</span>
                {n.status === "no_data" ? <span className="text-muted">no data</span> : <span>{n.bedtime} → {n.wake} <Badge tone={n.status === "adherent" ? "good" : "neutral"}>{n.status === "adherent" ? "on plan" : "off plan"}</Badge></span>}</li>))}</ul>
          </>
        )}
      </Card>
    </Grid>
  );
}
