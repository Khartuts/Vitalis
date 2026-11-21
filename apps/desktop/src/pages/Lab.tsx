import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Play, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { Chart } from "../components/Chart";
import { Badge, Button, Card, Empty, ErrorBox, Field, Grid, inputCls, Note, PageHeader, Select, Skeleton, Stat, Switch, TabPanel, Tabs } from "../components/ui";
import { engine } from "../lib/bridge";
import { fmtDate, fmtFeature } from "../lib/format";
import { useEngine, useGlobalRange } from "../lib/hooks";
import { addDays } from "../lib/store";

const QUESTIONS = [
  { q: "Does drinking coffee after 3 PM affect my sleep?", habit: "j:caffeine_last_time", condition: { op: "after", value: "15:00" }, outcome: "sleep_tst_min" },
  { q: "Does late caffeine change my deep sleep?", habit: "j:caffeine_last_time", condition: { op: "after", value: "15:00" }, outcome: "sleep_deep_min" },
  { q: "Do I sleep better after walking?", habit: "j:walk", condition: { op: "==", value: 1 }, outcome: "sleep_efficiency" },
  { q: "Are my stressful days associated with worse recovery?", habit: "j:stress", condition: { op: ">=", value: 7 }, outcome: "recovery_index" },
  { q: "Does my sleep improve when I avoid late snacks?", habit: "j:late_snack", condition: { op: "==", value: 0 }, outcome: "sleep_efficiency" },
  { q: "Is my resting heart rate higher after alcohol?", habit: "j:alcohol_units", condition: { op: ">=", value: 2 }, outcome: "rhr" },
];
const VERDICT: Record<string, { label: string; tone: "neutral" | "accent" | "warning" }> = {
  insufficient_data: { label: "Insufficient data", tone: "warning" }, inconclusive: { label: "Inconclusive", tone: "neutral" },
  suggestive_small_sample: { label: "Suggestive (small sample)", tone: "warning" }, association_detected: { label: "Association detected", tone: "accent" },
};

export default function Lab() {
  const [tab, setTab] = useState("ask");
  return (
    <div data-testid="lab-page">
      <PageHeader title="Correlation & Experiment Lab" subtitle="Test questions against your own recorded data. Every result reports sample size, missing days, a confidence interval
        and an effect size. Correlation is not causation — use an experiment to test a cause." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "ask", label: "Ask a question" }, { id: "explore", label: "Two-variable explorer" }, { id: "discover", label: "Discover (FDR-controlled)" }, { id: "experiments", label: "N-of-1 experiments" }]}>
        <TabPanel value="ask"><Ask /></TabPanel>
        <TabPanel value="explore"><Explore /></TabPanel>
        <TabPanel value="discover"><Discover /></TabPanel>
        <TabPanel value="experiments"><Experiments /></TabPanel>
      </Tabs>
    </div>
  );
}

function useFeatureOptions() {
  const cat = useEngine<any[]>("features.catalog");
  return useMemo(() => (cat.data ?? []).filter((c) => c.days > 0).map((c) => ({ value: c.key, label: `${c.label}${c.key.startsWith("j:") ? " (journal)" : ""}` })), [cat.data]);
}

function Ask() {
  const range = useGlobalRange();
  const [i, setI] = useState(0);
  const [habit, setHabit] = useState(QUESTIONS[0].habit);
  const [outcome, setOutcome] = useState(QUESTIONS[0].outcome);
  const [op, setOp] = useState(QUESTIONS[0].condition.op);
  const [val, setVal] = useState(String(QUESTIONS[0].condition.value));
  const [adjust, setAdjust] = useState(false);
  const opts = useFeatureOptions();
  const start = addDays(range.end, -Math.max(89, range.days - 1));
  const r = useEngine<any>("stats.habit", { habit, outcome, start, end: range.end, condition: { op, value: /^\d+(\.\d+)?$/.test(val) ? Number(val) : val },
    adjust: adjust ? ["sleep_tst_min", "steps"].filter((x) => x !== outcome) : [] }, { enabled: !!range.status });
  const pick = (k: number) => { const q = QUESTIONS[k]; setI(k); setHabit(q.habit); setOutcome(q.outcome); setOp(q.condition.op); setVal(String(q.condition.value)); };
  const g = r.data?.group_comparison;
  return (
    <Grid cols={2}>
      <Card explain="lb.question" title="Question">
        <div className="mb-3 flex flex-wrap gap-1.5">{QUESTIONS.map((q, k) => (
          <button key={q.q} onClick={() => pick(k)} data-testid={`question-${k}`} className={`rounded-full border px-2.5 py-1 text-[11px] ${k === i ? "border-accent text-fg" : "border-line text-fg2 hover:text-fg"}`}>{q.q}</button>))}</div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Habit / exposure"><select className={inputCls} value={habit} onChange={(e) => setHabit(e.target.value)}>{opts.filter((o) => o.value.startsWith("j:")).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></Field>
          <Field label="Outcome"><select className={inputCls} value={outcome} onChange={(e) => setOutcome(e.target.value)}>{opts.filter((o) => !o.value.startsWith("j:")).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></Field>
          <Field label="Condition" hint="Times use HH:MM (after/before)"><div className="flex gap-1">
            <select className={inputCls + " w-24"} value={op} onChange={(e) => setOp(e.target.value)}>{[">", ">=", "<", "<=", "==", "!=", "after", "before"].map((o) => <option key={o}>{o}</option>)}</select>
            <input className={inputCls} value={val} onChange={(e) => setVal(e.target.value)} /></div></Field>
          <div className="self-end"><Switch checked={adjust} onChange={setAdjust} label="Adjust for weekend, sleep & steps" /></div>
        </div>
        <Note>Period: {start} → {range.end} (at least 90 days). Overnight outcomes are compared with the habit logged the day before the night (lag {r.data?.lag_days ?? "…"} day).</Note>
      </Card>
      <Card explain="lb.answer" title="Answer from your data" testId="habit-result">
        {r.error ? <ErrorBox error={r.error} /> : !r.data ? <Skeleton h={220} /> : !g || g.verdict === "insufficient_data" ? (
          <Empty title="Insufficient data">{g?.explanation ?? "Log this habit for more days."} ({r.data.days_with_habit_logged} days logged, {r.data.days_with_outcome} with outcome)</Empty>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-3"><Badge tone={VERDICT[g.verdict].tone}>{VERDICT[g.verdict].label}</Badge>
              <span className="text-xs text-fg2">n = {g.n_a} vs {g.n_b} days · Hedges g = {g.hedges_g.toFixed(2)} [{g.hedges_g_ci.map((x: number) => x.toFixed(2)).join(", ")}]</span></div>
            <div className="grid grid-cols-3 gap-3">
              <Stat label="When condition met" value={fmtFeature(outcome, g.mean_a)} sub={`median ${fmtFeature(outcome, g.median_a)}`} />
              <Stat label="Otherwise" value={fmtFeature(outcome, g.mean_b)} sub={`median ${fmtFeature(outcome, g.median_b)}`} />
              <Stat label="Difference" value={`${g.diff_mean >= 0 ? "+" : "−"}${fmtFeature(outcome, Math.abs(g.diff_mean))}`} sub={`95% CI ${g.diff_mean_ci.map((x: number) => x.toFixed(1)).join(" to ")}`} />
            </div>
            <Chart height={170} title="Distributions" option={(p) => ({ legend: { show: false }, grid: { left: 120, right: 20, top: 10, bottom: 24, containLabel: false },
              xAxis: { type: "value", scale: true }, yAxis: { type: "category", data: ["Otherwise", "Condition met"] },
              tooltip: { trigger: "item", formatter: (x: any) => `${x.name}: 5th ${x.value[1].toFixed(0)} · 25th ${x.value[2].toFixed(0)} · median ${x.value[3].toFixed(0)} · 75th ${x.value[4].toFixed(0)} · 95th ${x.value[5].toFixed(0)}` },
              series: [{ type: "boxplot", data: [g.distribution_b, g.distribution_a], itemStyle: { color: p.surface3, borderColor: p.s2, borderWidth: 2 } }] })} />
            <p className="mt-2 text-xs text-fg2">{g.explanation}</p>
            {r.data.adjusted && (
              <p className="mt-2 text-xs text-fg2"><b>Adjusted</b> (OLS, Newey–West SE, covariates {r.data.adjusted.covariates?.join(", ") || "—"}):
                {r.data.adjusted.coef != null ? ` ${r.data.adjusted.coef.toFixed(1)} [${r.data.adjusted.ci.map((x: number) => x.toFixed(1)).join(", ")}], n=${r.data.adjusted.n} — ${r.data.adjusted.verdict.replaceAll("_", " ")}` : ` ${r.data.adjusted.explanation}`}</p>
            )}
            <Note>{r.data.caution}</Note>
          </>
        )}
      </Card>
    </Grid>
  );
}

function Explore() {
  const range = useGlobalRange();
  const opts = useFeatureOptions();
  const [x, setX] = useState("sleep_tst_min");
  const [y, setY] = useState("rhr");
  const [lag, setLag] = useState("auto");
  const [method, setMethod] = useState("spearman");
  const start = addDays(range.end, -Math.max(89, range.days - 1));
  const r = useEngine<any>("stats.correlate", { x, y, start, end: range.end, method, ...(lag === "auto" ? {} : { lag: Number(lag) }) }, { enabled: !!range.status });
  return (
    <div className="space-y-4">
      <Card explain="lb.variables" title="Variables">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Field label="X (exposure)"><select className={inputCls} value={x} onChange={(e) => setX(e.target.value)} data-testid="corr-x">{opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></Field>
          <Field label="Y (outcome)"><select className={inputCls} value={y} onChange={(e) => setY(e.target.value)} data-testid="corr-y">{opts.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></Field>
          <Field label="Lag (days after X)"><Select label="Lag" value={lag} onChange={setLag} options={[{ value: "auto", label: "Automatic" }, ...[0, 1, 2, 3].map((n) => ({ value: String(n), label: `${n} day${n === 1 ? "" : "s"}` }))]} /></Field>
          <Field label="Method" hint="Pearson only when roughly normal"><Select label="Method" value={method} onChange={setMethod} options={[{ value: "spearman", label: "Spearman (rank)" }, { value: "pearson", label: "Pearson (linear)" }]} /></Field>
        </div>
      </Card>
      {r.error ? <ErrorBox error={r.error} /> : !r.data ? <Skeleton h={300} /> : (
        <Grid cols={2}>
          <Card explain="lb.scatter" title="Scatter" subtitle={`${r.data.n} days with both values (${r.data.n_missing} missing) · lag ${r.data.lag_days}`} testId="corr-result">
            {r.data.r == null ? <Empty title="Insufficient data">{r.data.explanation}</Empty> : (
              <>
                <div className="mb-2 flex flex-wrap items-center gap-3"><Stat label={method === "spearman" ? "Spearman ρ" : "Pearson r"} value={r.data.r.toFixed(2)} sub={`95% CI ${r.data.ci[0].toFixed(2)} to ${r.data.ci[1].toFixed(2)} · effective n ≈ ${r.data.n_effective}`} />
                  <Badge tone={VERDICT[r.data.verdict].tone}>{VERDICT[r.data.verdict].label}</Badge><Badge>{r.data.strength}</Badge></div>
                <Chart height={240} title="Scatter" option={(p) => ({ legend: { show: false }, tooltip: { trigger: "item", formatter: (v: any) => `${v.value[2]}: ${v.value[0].toFixed(1)} → ${v.value[1].toFixed(1)}` },
                  xAxis: { type: "value", name: r.data.x_info.label, nameLocation: "middle", nameGap: 26, scale: true }, yAxis: { type: "value", name: r.data.y_info.label, scale: true },
                  series: [{ type: "scatter", symbolSize: 8, data: r.data.points.map((pt: any) => [pt.x, pt.y, pt.date]), itemStyle: { color: p.s1, borderColor: p.surface, borderWidth: 2 } }] })} />
                <p className="mt-2 text-xs text-fg2">{r.data.explanation}</p>
                {r.data.assumption_note && <Note>{r.data.assumption_note}</Note>}
              </>
            )}
          </Card>
          <Card explain="lb.lagged" title="Lagged associations" subtitle="Correlation when Y is measured 0–3 days after X (bars: r, whiskers: 95% CI)">
            <Chart height={200} title="Lagged correlations" option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: r.data.lagged.map((l: any) => `lag ${l.lag_days}`) },
              yAxis: { type: "value", min: -1, max: 1 }, tooltip: { formatter: (v: any) => { const l = r.data.lagged[v[0].dataIndex]; return l.r == null ? "insufficient data" : `r ${l.r.toFixed(2)} [${l.ci[0].toFixed(2)}, ${l.ci[1].toFixed(2)}] n=${l.n}`; } },
              series: [{ type: "bar", barMaxWidth: 28, data: r.data.lagged.map((l: any) => l.r), itemStyle: { color: p.s2, borderRadius: 4 } },
                { type: "custom", renderItem: (_: any, api: any) => { const l = r.data.lagged[api.value(0)]; if (!l?.ci) return null;
                  const a = api.coord([api.value(0), l.ci[0]]), b = api.coord([api.value(0), l.ci[1]]);
                  return { type: "group", children: [{ type: "line", shape: { x1: a[0], y1: a[1], x2: b[0], y2: b[1] }, style: { stroke: p.text2, lineWidth: 1.5 } },
                    { type: "line", shape: { x1: a[0] - 5, y1: a[1], x2: a[0] + 5, y2: a[1] }, style: { stroke: p.text2 } }, { type: "line", shape: { x1: b[0] - 5, y1: b[1], x2: b[0] + 5, y2: b[1] }, style: { stroke: p.text2 } }] }; },
                  data: r.data.lagged.map((_: any, k: number) => [k]) }] })} />
            <Chart height={180} title="Rolling correlation" option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: r.data.rolling.map((v: any) => v.date), axisLabel: { formatter: (v: string) => v.slice(5) } },
              yAxis: { type: "value", min: -1, max: 1, name: "30-day r" }, series: [{ type: "line", showSymbol: false, data: r.data.rolling.map((v: any) => v.r), lineStyle: { color: p.s4, width: 2 } }] })} />
            <Note>The rolling correlation shows whether the relationship is stable over time. Each window has few days, so expect noise.</Note>
          </Card>
        </Grid>
      )}
    </div>
  );
}

function Discover() {
  const range = useGlobalRange();
  const start = addDays(range.end, -Math.max(119, range.days - 1));
  const r = useEngine<any>("stats.scan", { start, end: range.end }, { enabled: !!range.status });
  return (
    <Card explain="lb.screen" title="Habit screen" subtitle={`Every active journal habit vs sleep, resting HR, HRV and arousal · ${start} → ${range.end}`}>
      {r.error ? <ErrorBox error={r.error} /> : !r.data ? <Skeleton h={300} /> : (
        <>
          <div className="mb-3 rounded-lg border border-warning/40 bg-warning/10 p-2 text-xs text-fg">{r.data.warning}</div>
          <div className="max-h-[520px] overflow-auto"><table className="w-full text-xs" data-testid="scan-table">
            <thead className="sticky top-0 bg-s1 text-muted"><tr>{["Habit", "Outcome", "Lag", "ρ", "95% CI", "n", "q (FDR)", ""].map((h) => <th key={h} className="py-1 text-left font-normal">{h}</th>)}</tr></thead>
            <tbody>{r.data.results.filter((x: any) => x.r != null).map((x: any) => (
              <tr key={x.exposure + x.outcome} className={`border-t border-line ${x.survives_fdr ? "" : "text-fg2"}`}>
                <td className="py-1">{x.exposure_label}</td><td>{x.outcome_label}</td><td className="num">{x.lag_days}</td><td className="num">{x.r.toFixed(2)}</td>
                <td className="num">{x.ci[0].toFixed(2)} – {x.ci[1].toFixed(2)}</td><td className="num">{x.n}</td><td className="num">{x.q.toFixed(3)}</td>
                <td>{x.survives_fdr ? <Badge tone="accent">survives FDR</Badge> : null}</td></tr>))}</tbody></table></div>
        </>
      )}
    </Card>
  );
}

function Experiments() {
  const qc = useQueryClient();
  const range = useGlobalRange();
  const list = useEngine<any[]>("experiments.list");
  const opts = useFeatureOptions();
  const [form, setForm] = useState({ title: "No caffeine after 14:00", hypothesis: "Avoiding afternoon caffeine increases my deep sleep.", habit_key: "caffeine_after_lunch",
    op: "==", value: "0", outcome: "sleep_deep_min", design: "randomized_blocks", start_date: addDays(range.end, 1), baseline_days: 7, intervention_days: 28, block_days: 2 });
  const [sel, setSel] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const analysis = useEngine<any>("experiments.analyze", { id: sel }, { enabled: !!sel });
  const create = async () => {
    setErr(null);
    try {
      const e = await engine("experiments.create", { title: form.title, hypothesis: form.hypothesis, habit_key: form.habit_key,
        condition: { op: form.op, value: /^\d+(\.\d+)?$/.test(form.value) ? Number(form.value) : form.value }, outcome: form.outcome, lag_days: 1,
        design: form.design, start_date: form.start_date, baseline_days: Number(form.baseline_days), intervention_days: Number(form.intervention_days), block_days: Number(form.block_days) });
      qc.invalidateQueries({ queryKey: ["experiments.list"] });
      setSel(e.id);
    } catch (e) { setErr(e); }
  };
  return (
    <Grid cols={2}>
      <Card explain="lb.design" title="Design an experiment" subtitle="Voluntary, safe changes only (sleep timing, caffeine timing, walks, screens…)">
        <div className="space-y-3">
          <Field label="Title"><input className={inputCls} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} maxLength={120} /></Field>
          <Field label="Hypothesis"><input className={inputCls} value={form.hypothesis} onChange={(e) => setForm({ ...form, hypothesis: e.target.value })} maxLength={500} /></Field>
          <div className="grid grid-cols-3 gap-2">
            <Field label="Habit"><select className={inputCls} value={form.habit_key} onChange={(e) => setForm({ ...form, habit_key: e.target.value })}>
              {opts.filter((o) => o.value.startsWith("j:")).map((o) => <option key={o.value} value={o.value.slice(2)}>{o.label}</option>)}</select></Field>
            <Field label="Intervention = habit"><div className="flex gap-1"><select className={inputCls + " w-16"} value={form.op} onChange={(e) => setForm({ ...form, op: e.target.value })}>
              {["==", "!=", ">", ">=", "<", "<=", "after", "before"].map((o) => <option key={o}>{o}</option>)}</select>
              <input className={inputCls} value={form.value} onChange={(e) => setForm({ ...form, value: e.target.value })} /></div></Field>
            <Field label="Outcome"><select className={inputCls} value={form.outcome} onChange={(e) => setForm({ ...form, outcome: e.target.value })}>
              {opts.filter((o) => !o.value.startsWith("j:")).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></Field>
          </div>
          <div className="grid grid-cols-4 gap-2">
            <Field label="Design"><select className={inputCls} value={form.design} onChange={(e) => setForm({ ...form, design: e.target.value })}>
              <option value="randomized_blocks">Randomized blocks</option><option value="ab">Before / after (A-B)</option></select></Field>
            <Field label="Start"><input type="date" className={inputCls} value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} /></Field>
            <Field label="Baseline days"><input type="number" className={inputCls} value={form.baseline_days} min={0} max={60} onChange={(e) => setForm({ ...form, baseline_days: Number(e.target.value) })} /></Field>
            <Field label="Test days"><input type="number" className={inputCls} value={form.intervention_days} min={8} max={120} onChange={(e) => setForm({ ...form, intervention_days: Number(e.target.value) })} /></Field>
          </div>
          {!!err && <ErrorBox error={err} />}
          <Button variant="primary" onClick={create} testId="experiment-create"><FlaskConical size={13} />Create schedule</Button>
          <Note>Randomized blocks assign 2-day blocks to "do it" or "don't" in balanced random order, which protects against time trends. Follow the schedule and log the habit daily.</Note>
        </div>
      </Card>
      <Card explain="lb.experiments" title="Your experiments">
        {!list.data?.length ? <Empty title="No experiments yet" /> : (
          <ul className="space-y-2">{list.data.map((e) => (
            <li key={e.id} className="flex items-center gap-2 text-[13px]"><button className={`flex-1 text-left ${sel === e.id ? "text-accent-ink" : "text-fg"}`} onClick={() => setSel(e.id)}>{e.title}</button>
              <Badge>{e.design === "ab" ? "A-B" : "randomized"}</Badge><Badge>{e.status}</Badge>
              <Button size="sm" variant="ghost" title="Analyze" onClick={() => setSel(e.id)}><Play size={12} /></Button>
              <Button size="sm" variant="ghost" title="Delete" onClick={async () => { await engine("experiments.delete", { id: e.id }); if (sel === e.id) setSel(null); qc.invalidateQueries({ queryKey: ["experiments.list"] }); }}><Trash2 size={12} /></Button></li>))}</ul>
        )}
        {sel && (analysis.isLoading ? <Skeleton h={200} /> : analysis.data && <ExperimentResult a={analysis.data} />)}
      </Card>
    </Grid>
  );
}

function ExperimentResult({ a }: { a: any }) {
  const sched = Object.entries<string>(a.experiment.schedule);
  const color: Record<string, string> = { baseline: "var(--surface-3)", intervention: "var(--series-1)", control: "var(--series-3)" };
  return (
    <div className="mt-4 border-t border-line pt-3" data-testid="experiment-result">
      <div className="mb-2 text-xs text-fg2">{a.experiment.hypothesis}</div>
      <div className="mb-3 flex flex-wrap gap-0.5" aria-label="Schedule">{sched.map(([d, arm]) => (
        <span key={d} title={`${d}: ${arm}`} className="h-4 w-4 rounded-sm" style={{ background: color[arm] }} />))}</div>
      <div className="mb-2 flex gap-3 text-[11px] text-fg2"><span><i className="inline-block h-2 w-3 rounded-sm" style={{ background: color.intervention }} /> do it</span>
        <span><i className="inline-block h-2 w-3 rounded-sm" style={{ background: color.control }} /> don't (control)</span><span><i className="inline-block h-2 w-3 rounded-sm" style={{ background: color.baseline }} /> baseline</span></div>
      {a.status !== "analyzed" ? <p className="text-xs text-fg2">{a.conclusion ?? "Not started yet."}</p> : (
        <>
          <div className="grid grid-cols-3 gap-3">
            <Stat label="Intervention days" value={fmtFeature(a.experiment.outcome, a.mean_intervention)} sub={`n = ${a.n_intervention}`} />
            <Stat label="Comparison days" value={fmtFeature(a.experiment.outcome, a.mean_comparison)} sub={`n = ${a.n_comparison}`} />
            <Stat label="Adherence" value={a.adherence_rate == null ? "—" : `${Math.round(a.adherence_rate * 100)}%`} />
          </div>
          <p className="mt-2 text-xs text-fg"><b>{a.conclusion}</b> Permutation p = {a.p_permutation.toFixed(3)}, g = {a.hedges_g.toFixed(2)}.</p>
          <Note>{a.comparison}. {a.limitations.join(" ")}</Note>
        </>
      )}
      <Note>Started {fmtDate(a.experiment.start_date)}.</Note>
    </div>
  );
}
