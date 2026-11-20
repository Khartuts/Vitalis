import { Chart, dailyLines } from "../components/Chart";
import { Badge, Card, Empty, ErrorBox, Grid, Note, PageHeader, Prov, Skeleton } from "../components/ui";
import { fmtDate } from "../lib/format";
import { useEngine, useGlobalRange } from "../lib/hooks";
import { getVar } from "./SleepLab";

export default function Cardio() {
  const range = useGlobalRange();
  const start = range.days < 14 ? new Date(Date.parse(range.end) - 29 * 86400000).toISOString().slice(0, 10) : range.start;
  const q = useEngine<any>("cardio.summary", { start, end: range.end }, { enabled: !!range.status });
  const load = useEngine<any>("activity.summary", { start, end: range.end }, { enabled: !!range.status });
  if (q.error) return <ErrorBox error={q.error} />;
  if (!q.data) return <Skeleton h={500} />;
  const d = q.data.daily as any[];
  const dates = d.map((x) => x.date);
  const col = (k: string) => d.map((x) => x[k] as number | null);
  const has = (k: string) => col(k).some((v) => v != null);
  const ctx = (m: string[], t: string) => ({ metrics: m, start, end: range.end, title: t });
  const lineCard = (k: string, title: string, sub: string, color: string, prov: string, empty?: string) => (
    <Card key={k} title={title} subtitle={sub} actions={<Prov kind={prov} />}>
      {has(k) ? <Chart height={220} title={title} zoom context={ctx([k], title)} table={{ columns: ["Date", title], rows: d.map((x) => [x.date, x[k] == null ? null : Math.round(x[k] * 10) / 10]) }}
        option={dailyLines(dates, [{ name: title, data: col(k), color: getVar(color) }])} />
        : <Empty title="Not available from your sources">{empty}</Empty>}
    </Card>
  );
  return (
    <div data-testid="cardio-page">
      <PageHeader title="Cardiovascular Health" subtitle="Trends from your own measurements. Not a diagnostic tool — VITALIS does not detect or rule out arrhythmia, hypertension, sleep apnea or heart disease." />
      <Grid cols={2}>
        {lineCard("rhr", "Resting heart rate", "Calculated overnight (lowest 30-min mean during sleep); source value used only as fallback", "--series-2", "calculated")}
        {lineCard("hrv_night_rmssd", "Overnight HRV (RMSSD)", "Imported from sources that measure beat-to-beat intervals — never derived from averaged HR", "--series-1", "imported",
          "Samsung's public interfaces do not provide HRV. HRV appears here only if Health Connect or Google Health supplies an RMSSD record.")}
        <Card title="Day vs night heart rate" subtitle="Mean awake heart rate and mean heart rate during sleep">
          {has("hr_day_mean") ? <Chart height={220} title="Day vs night heart rate" option={dailyLines(dates, [
            { name: "Awake mean", data: col("hr_day_mean"), color: getVar("--series-3") }, { name: "Asleep mean", data: col("hr_sleep_mean"), color: getVar("--series-2") }])} /> : <Empty title="No heart-rate data" />}
        </Card>
        <Card title="Heart-rate distribution" subtitle={q.data.hr_histogram ? `${q.data.hr_histogram.n.toLocaleString()} samples · ${q.data.hr_histogram.period.join(" → ")}` : ""}>
          {!q.data.hr_histogram ? <Empty title="No heart-rate samples" /> : (
            <Chart height={220} title="Heart-rate distribution" option={(p) => ({ legend: { show: false }, tooltip: { trigger: "axis" },
              xAxis: { type: "category", data: q.data.hr_histogram.edges.slice(0, -1).map((e: number) => `${e}`), name: "bpm" }, yAxis: { type: "value", name: "samples" },
              series: [{ type: "bar", data: q.data.hr_histogram.counts, barCategoryGap: "6%", itemStyle: { color: p.s2, borderRadius: [3, 3, 0, 0] } }] })} />
          )}
        </Card>
        {lineCard("spo2_night_mean", "Overnight SpO2", "Mean of overnight samples (Galaxy Watch: enable 'SpO2 during sleep' in Samsung Health)", "--series-1", "measured")}
        {lineCard("resp_rate_night", "Overnight respiratory rate", "Breaths per minute", "--series-4", "measured", "Not exposed by the Samsung SDK; appears if Health Connect or Google Health provides it.")}
        <Card title="Workout heart-rate zones" subtitle="Minutes per zone (% of max HR) — see Activity for details">
          {load.data?.daily?.some((x: any) => x.hr_z2_min != null) ? (
            <Chart height={220} title="Heart-rate zones" option={(p) => ({ legend: { data: ["Z1", "Z2", "Z3", "Z4", "Z5"] }, tooltip: { valueFormatter: (v: any) => `${Math.round(v)} min` },
              xAxis: { type: "category", data: load.data.daily.map((x: any) => x.date), axisLabel: { formatter: (v: string) => v.slice(5) } }, yAxis: { type: "value", name: "min" },
              series: [1, 2, 3, 4, 5].map((z) => ({ name: `Z${z}`, type: "bar", stack: "z", barMaxWidth: 12, data: load.data.daily.map((x: any) => x[`hr_z${z}_min`]),
                itemStyle: { color: ["#cde2fb", "#86b6ef", "#3987e5", "#1c5cab", "#0d366b"][z - 1], borderColor: p.surface, borderWidth: 1 } })) })} />
          ) : <Empty title="Zones unavailable">Set your birth year or maximum heart rate in Settings to compute zones.</Empty>}
        </Card>
        <Card title="Blood pressure" subtitle="Imported cuff/watch readings or manual entries">
          {q.data.blood_pressure.length === 0 ? <Empty title="No blood-pressure readings">Galaxy Watch blood pressure requires cuff calibration in Samsung Health Monitor and may not be shared with other apps.</Empty> : (
            <Chart height={220} title="Blood pressure" option={(p) => {
              const sys = q.data.blood_pressure.filter((b: any) => b.metric === "bp_systolic");
              const dia = q.data.blood_pressure.filter((b: any) => b.metric === "bp_diastolic");
              return { legend: { data: ["Systolic", "Diastolic"] }, xAxis: { type: "time" }, yAxis: { type: "value", name: "mmHg", scale: true },
                series: [{ name: "Systolic", type: "scatter", data: sys.map((b: any) => [b.start_utc, b.value_num]), itemStyle: { color: p.s5 } },
                  { name: "Diastolic", type: "scatter", data: dia.map((b: any) => [b.start_utc, b.value_num]), itemStyle: { color: p.s2 } }] };
            }} />
          )}
        </Card>
        <Card title="Resting HR vs sleep · HRV vs sleep" subtitle="Correlations across nights (Spearman, 95% CI)">
          {Object.entries(q.data.relationships).map(([k, r]: any) => (
            <div key={k} className="mb-2 flex items-center justify-between border-t border-line pt-2 text-xs">
              <span className="text-fg2">{k === "sleep_tst_min~rhr" ? "Sleep duration ↔ resting HR" : "Sleep duration ↔ HRV"}</span>
              {r.r == null ? <span className="text-muted">insufficient data (n={r.n})</span> :
                <span className="num">ρ {r.r.toFixed(2)} [{r.ci[0].toFixed(2)}, {r.ci[1].toFixed(2)}] n={r.n} <Badge>{r.verdict.replaceAll("_", " ")}</Badge></span>}
            </div>
          ))}
        </Card>
        <Card title="Authorized heart records" subtitle="Irregular-rhythm notifications and sleep-apnea feature records, shown verbatim">
          {q.data.events.length === 0 ? <p className="text-xs text-fg2">No such records were shared by your sources in this period.</p> : (
            <ul className="space-y-1 text-xs">{q.data.events.map((e: any, i: number) => <li key={i}>{fmtDate(e.start_utc)} · {e.metric.replaceAll("_", " ")} · {e.value_text ?? ""} ({e.provider})</li>)}</ul>
          )}
          <Note>VITALIS does not interpret these records. If you received such a notification or have symptoms (palpitations, chest pain, fainting, breathlessness), contact a healthcare professional.</Note>
        </Card>
      </Grid>
      <Note>{q.data.notes.join(" ")}</Note>
    </div>
  );
}
