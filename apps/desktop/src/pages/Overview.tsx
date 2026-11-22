import type { JSX } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, EyeOff, LayoutGrid, Lightbulb, Sparkles } from "lucide-react";
import { useMemo, useState } from "react";
import { navigate } from "../App";
import { Chart, dailyLines, type Palette } from "../components/Chart";
import { Badge, Button, Card, Empty, ErrorBox, PageHeader, Prov, Select, Skeleton, Stat } from "../components/ui";
import { engine } from "../lib/bridge";
import { CATEGORY_LABEL, fmtDelta, fmtDuration, fmtFeature, isNum, zLabel } from "../lib/format";
import type { ExplainId } from "../explain/content";
import { useEngine, useGlobalRange } from "../lib/hooks";

const CARDS = ["recovery", "sleep", "rhr", "hrv", "skin", "stress", "energy", "activity", "baseline", "change", "insights", "quality", "periods"] as const;
type CardId = (typeof CARDS)[number];

export default function Overview() {
  const range = useGlobalRange();
  const qc = useQueryClient();
  const ov = useEngine<any>("overview.get", { date: range.end }, { enabled: !!range.status });
  const prefs = useEngine<any>("prefs.get");
  const [editing, setEditing] = useState(false);
  const [period, setPeriod] = useState("7");
  const layout: { order: CardId[]; hidden: CardId[] } = useMemo(() => {
    const l = prefs.data?.dashboard_layout;
    const order = (l?.order ?? []).filter((c: CardId) => CARDS.includes(c));
    return { order: [...order, ...CARDS.filter((c) => !order.includes(c))], hidden: l?.hidden ?? [] };
  }, [prefs.data]);
  const saveLayout = async (l: typeof layout) => {
    await engine("prefs.set", { values: { dashboard_layout: l } });
    qc.invalidateQueries({ queryKey: ["prefs.get"] });
  };
  const move = (id: CardId, d: -1 | 1) => {
    const o = [...layout.order];
    const i = o.indexOf(id);
    const j = i + d;
    if (j < 0 || j >= o.length) return;
    [o[i], o[j]] = [o[j], o[i]];
    saveLayout({ ...layout, order: o });
  };

  if (ov.error) return <ErrorBox error={ov.error} />;
  if (ov.isLoading || !ov.data) return <div className="space-y-4"><Skeleton h={40} /><Skeleton h={240} /><Skeleton h={240} /></div>;
  const d = ov.data;
  if (!range.status?.data_range) {
    return (
      <>
        <PageHeader title="Overview" />
        <Empty title="No health data yet">Pair the Android companion (Settings → Phone sync), import a Samsung Health export, or connect Google Health. Everything stays encrypted on this PC.</Empty>
      </>
    );
  }
  const comp = Object.fromEntries(d.comparisons.map((c: any) => [c.feature, c]));
  const render: Record<CardId, () => JSX.Element> = {
    recovery: () => <RecoveryCard d={d} />,
    sleep: () => <SleepCard d={d} />,
    rhr: () => <TrendCard title="Resting heart rate" feature="rhr" d={d} comp={comp.rhr} color="s2" to="cardio" />,
    hrv: () => <TrendCard title="Overnight HRV (source RMSSD)" feature="hrv_night_rmssd" d={d} comp={comp.hrv_night_rmssd} color="s1" to="cardio"
      emptyNote="Samsung Health does not share HRV through its public interfaces; HRV appears only if a source (e.g. Google Health) provides it." />,
    skin: () => <TrendCard title="Overnight skin temperature (vs watch baseline)" feature="skin_temp_delta_night" d={d} comp={comp.skin_temp_delta_night} color="s5" to="temperature"
      emptyNote="Galaxy Watch measures skin temperature during sleep; it arrives through Health Connect after the watch syncs." />,
    stress: () => <TrendCard title="Elevated HR at low movement" feature="arousal_min" d={d} comp={comp.arousal_min} color="s3" to="stress" bar
      emptyNote="Needs heart rate and steps." />,
    energy: () => <EnergyCard d={d} />,
    activity: () => <ActivityCard d={d} comp={comp} />,
    baseline: () => <BaselineCard d={d} />,
    change: () => <ChangeCard d={d} />,
    insights: () => <InsightsCard d={d} />,
    quality: () => <QualityCard d={d} />,
    periods: () => <PeriodsCard d={d} period={period} setPeriod={setPeriod} />,
  };
  return (
    <div data-testid="overview">
      <PageHeader title="Overview" subtitle={<>Today's summary for <b className="text-fg">{d.date}</b>, compared with your own baseline. Click any card for details.</>}
        actions={<Button size="sm" variant={editing ? "primary" : "secondary"} onClick={() => setEditing(!editing)}><LayoutGrid size={13} />{editing ? "Done" : "Customize"}</Button>} />
      {editing && layout.hidden.length > 0 && (
        <div className="mb-3 flex flex-wrap gap-2 text-xs text-fg2">Hidden cards:
          {layout.hidden.map((h) => <Button key={h} size="sm" onClick={() => saveLayout({ ...layout, hidden: layout.hidden.filter((x) => x !== h) })}>Show {h}</Button>)}
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 2xl:grid-cols-3">
        {layout.order.filter((c) => !layout.hidden.includes(c)).map((c) => (
          <div key={c} className={c === "baseline" || c === "periods" ? "lg:col-span-2 2xl:col-span-1" : ""}>
            {editing && (
              <div className="mb-1 flex justify-end gap-1">
                <Button size="sm" variant="ghost" onClick={() => move(c, -1)} title="Move up"><ArrowUp size={12} /></Button>
                <Button size="sm" variant="ghost" onClick={() => move(c, 1)} title="Move down"><ArrowDown size={12} /></Button>
                <Button size="sm" variant="ghost" onClick={() => saveLayout({ ...layout, hidden: [...layout.hidden, c] })} title="Hide"><EyeOff size={12} /></Button>
              </div>
            )}
            {render[c]()}
          </div>
        ))}
      </div>
    </div>
  );
}

function RecoveryCard({ d }: { d: any }) {
  const r = d.recovery;
  const used = r?.detail?.inputs_used ?? [];
  return (
    <Card explain="ov.recovery" title="Morning recovery estimate" subtitle="Relative index · 50 = your usual" onClick={() => navigate("recovery")} testId="card-recovery"
      actions={<Prov kind="estimated" />}>
      {!r || r.value == null ? (
        <Empty title="Insufficient data">{r?.detail?.reason ?? "Needs overnight HRV or resting HR with a baseline, plus sleep duration."}</Empty>
      ) : (
        <div className="flex items-end justify-between gap-4">
          <Stat label={CATEGORY_LABEL[r.category]} value={Math.round(r.value)} sub={`z = ${r.z >= 0 ? "+" : ""}${r.z.toFixed(2)} · ${Math.round(r.completeness * 100)}% of inputs · baseline ${r.baseline_status}`} testId="recovery-value" />
          <div className="w-1/2 space-y-1">
            {used.map((u: any) => (
              <div key={u.component} className="flex items-center gap-2 text-[11px]">
                <span className="w-16 shrink-0 text-fg2">{u.component}</span>
                <div className="relative h-1.5 flex-1 rounded bg-s3">
                  <div className="absolute top-0 h-1.5 rounded" style={{ left: u.contribution < 0 ? `${50 + Math.max(-50, u.contribution * 60)}%` : "50%",
                    width: `${Math.min(50, Math.abs(u.contribution) * 60)}%`, background: u.contribution < 0 ? "var(--series-3)" : "var(--series-1)" }} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      <MiniTrend values={(d.recovery_trend ?? []).map((x: any) => x.value)} dates={(d.recovery_trend ?? []).map((x: any) => x.local_date ?? x.date)} color="s1" />
    </Card>
  );
}

function SleepCard({ d }: { d: any }) {
  const s = d.sleep_today ?? {};
  const total = (s.sleep_deep_min ?? 0) + (s.sleep_rem_min ?? 0);
  return (
    <Card explain="ov.sleep" title="Last night's sleep" onClick={() => navigate("sleep")} testId="card-sleep" actions={<Prov kind="measured" />}>
      {!isNum(s.sleep_tst_min) ? <Empty title="No main sleep recorded for this night" /> : (
        <div className="grid grid-cols-3 gap-3">
          <Stat label="Asleep" value={fmtDuration(s.sleep_tst_min)} testId="sleep-tst" />
          <Stat label="Efficiency" value={fmtFeature("sleep_efficiency", s.sleep_efficiency, "%")} />
          <Stat label="Deep + REM" value={total ? fmtDuration(total) : "—"} />
          {isNum(s.sleep_score_imported) && <Stat label="Samsung sleep score" value={Math.round(s.sleep_score_imported)} sub={<Prov kind="imported" />} />}
          <Stat label="Sleep debt (est.)" value={fmtDuration(s.sleep_debt_min)} sub="vs your sleep need" />
          <Stat label="Regularity (SRI)" value={fmtFeature("sleep_sri_14d", s.sleep_sri_14d)} sub="14-day index" />
        </div>
      )}
    </Card>
  );
}

function MiniTrend({ values, dates, color, bar }: { values: (number | null)[]; dates: string[]; color: keyof Palette; bar?: boolean }) {
  if (!values.some((v) => v != null)) return null;
  return (
    <div className="mt-3">
      <Chart bare height={70} title="30-day trend" option={(p) => ({
        grid: { left: 0, right: 0, top: 4, bottom: 0, containLabel: false }, legend: { show: false },
        xAxis: { type: "category", data: dates, show: false }, yAxis: { type: "value", show: false, scale: true },
        tooltip: { trigger: "axis", valueFormatter: (v: any) => (v == null ? "—" : String(Math.round(v))) },
        series: [{ type: bar ? "bar" : "line", data: values, showSymbol: false, connectNulls: false, barMaxWidth: 6,
          itemStyle: { color: p[color] as string, borderRadius: [2, 2, 0, 0] }, lineStyle: { color: p[color] as string, width: 2 } }],
      })} />
    </div>
  );
}

const TREND_EXPLAIN: Record<string, ExplainId> = { rhr: "ov.rhr", hrv_night_rmssd: "ov.hrv", arousal_min: "ov.arousal", skin_temp_delta_night: "ov.skin" };

function TrendCard({ title, feature, d, comp, color, to, bar, emptyNote }: { title: string; feature: string; d: any; comp: any;
  color: keyof Palette; to: string; bar?: boolean; emptyNote?: string }) {
  const trend = d.trends?.[feature] ?? [];
  const has = trend.some((t: any) => t.value != null);
  return (
    <Card title={title} onClick={() => navigate(to)} testId={`card-${feature}`} explain={TREND_EXPLAIN[feature]}>
      {!has ? <Empty title="Not available">{emptyNote}</Empty> : (
        <>
          <div className="flex items-end justify-between">
            <Stat label="Latest" value={fmtFeature(feature, comp?.value, comp?.unit)}
              sub={comp?.baseline?.median != null ? `Usual ${fmtFeature(feature, comp.baseline.median, comp.unit)} (${comp.baseline.status})` : "baseline building"} />
            <Badge tone={comp?.z == null ? "neutral" : Math.abs(comp.z) >= 1.5 ? "warning" : "neutral"}>{zLabel(comp?.z)}</Badge>
          </div>
          <MiniTrend values={trend.map((t: any) => t.value)} dates={trend.map((t: any) => t.date)} color={color} bar={bar} />
        </>
      )}
    </Card>
  );
}

function EnergyCard({ d }: { d: any }) {
  const e = d.energy;
  return (
    <Card explain="ov.energy" title="Daily energy estimate" subtitle="Exploratory model · not validated" onClick={() => navigate("recovery")} testId="card-energy" actions={<Prov kind="estimated" />}>
      {e.status === "insufficient_data" ? <Empty title="Insufficient data">Missing: {(e.missing_inputs ?? []).join(", ")}</Empty> : (
        <>
          <div className="flex gap-6"><Stat label="At wake" value={Math.round(e.start_value)} /><Stat label="Now / end of day" value={Math.round(e.end_value)} /><Stat label="Lowest" value={Math.round(e.min_value)} /></div>
          <Chart bare height={90} title="Energy" option={(p) => ({
            grid: { left: 0, right: 0, top: 6, bottom: 0, containLabel: false }, legend: { show: false },
            xAxis: { type: "category", data: e.points.map((x: any) => x.t.slice(11, 16)), show: false }, yAxis: { type: "value", min: 0, max: 100, show: false },
            series: [{ type: "line", data: e.points.map((x: any) => x.value), showSymbol: false, lineStyle: { color: p.s2, width: 2 },
              areaStyle: { color: p.s2 + "30" } }] })} />
        </>
      )}
    </Card>
  );
}

function ActivityCard({ d, comp }: { d: any; comp: any }) {
  const a = d.activity_today ?? {};
  const st = d.trends?.steps ?? [];
  return (
    <Card explain="ov.activity" title="Activity" onClick={() => navigate("activity")} testId="card-activity">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Steps" value={fmtFeature("steps", a.steps, "count")} sub={comp.steps?.baseline?.median ? `usual ${Math.round(comp.steps.baseline.median).toLocaleString()}` : undefined} />
        <Stat label="Exercise" value={isNum(a.exercise_min) ? `${Math.round(a.exercise_min)} min` : "—"} />
        <Stat label="Cardio load" value={fmtFeature("trimp", a.trimp)} sub="TRIMP" />
      </div>
      <MiniTrend values={st.map((t: any) => t.value)} dates={st.map((t: any) => t.date)} color="s1" bar />
    </Card>
  );
}

function BaselineCard({ d }: { d: any }) {
  return (
    <Card explain="ov.baseline" title="Today vs your baseline" subtitle="Robust median of the previous 60 days (provisional < 28 valid days)" testId="card-baseline">
      <table className="w-full text-xs">
        <thead className="text-muted"><tr><th className="py-1 text-left font-normal">Metric</th><th className="text-right font-normal">Today</th>
          <th className="text-right font-normal">Usual</th><th className="text-right font-normal">vs usual</th></tr></thead>
        <tbody>
          {d.comparisons.map((c: any) => (
            <tr key={c.feature} className="border-t border-line">
              <td className="py-1.5 text-fg2">{c.label}</td>
              <td className="num text-right text-fg">{fmtFeature(c.feature, c.value, c.unit)}</td>
              <td className="num text-right text-fg2">{fmtFeature(c.feature, c.baseline?.median, c.unit)}</td>
              <td className="text-right">{c.value == null ? <span className="text-muted">missing</span> : <span className="text-fg2">{zLabel(c.z)}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Card>
  );
}

function ChangeCard({ d }: { d: any }) {
  const m = d.most_important_change;
  return (
    <Card explain="ov.change" title="Most important change from baseline" testId="card-change">
      {!m ? <p className="text-xs text-fg2">No metric is more than one robust SD away from your usual today.</p> : (
        <div>
          <div className="text-lg font-semibold">{m.label}</div>
          <p className="mt-1 text-xs text-fg2">{fmtFeature(m.feature, m.value, m.unit)} today vs usual {fmtFeature(m.feature, m.baseline.median, m.unit)} — {zLabel(m.z)} (z {m.z.toFixed(1)}, {m.baseline.n} days of baseline).</p>
          <Button size="sm" onClick={() => navigate(m.group === "sleep" ? "sleep" : m.group === "activity" ? "activity" : m.group === "stress" ? "stress" : "cardio")}>See details</Button>
        </div>
      )}
    </Card>
  );
}

function InsightsCard({ d }: { d: any }) {
  return (
    <Card explain="ov.insights" title="Insights" subtitle="Deterministic rules with conservative thresholds" testId="card-insights" actions={<Sparkles size={14} className="text-muted" />}>
      {!d.insights?.length ? <p className="text-xs text-fg2">No new insights — nothing has changed enough to report.</p> : (
        <ul className="space-y-3">
          {d.insights.slice(0, 3).map((i: any) => (
            <li key={i.id} className="text-xs">
              <div className="flex items-center gap-1.5 font-medium text-fg"><Lightbulb size={13} className="text-accent-ink" />{i.title}
                <Badge tone={i.severity === "attention" ? "warning" : "neutral"}>{i.confidence} confidence</Badge></div>
              <p className="mt-0.5 text-fg2">{i.observation}</p>
              {i.action && <p className="mt-0.5 text-fg">→ {i.action}</p>}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function QualityCard({ d }: { d: any }) {
  const q = d.data_quality;
  const pct = Math.round((q.score ?? 0) * 100);
  return (
    <Card explain="ov.quality" title="Data quality" onClick={() => navigate("settings")} testId="card-quality">
      <div className="grid grid-cols-3 gap-3">
        <Stat label="Completeness (7 d)" value={`${pct}%`} tone={pct < 60 ? "muted" : undefined} />
        <Stat label="Nights with sleep" value={`${q.sleep_nights_7d}/7`} />
        <Stat label="HR wear today" value={q.hr_coverage_today == null ? "—" : `${Math.round(q.hr_coverage_today)}%`} />
      </div>
    </Card>
  );
}

function PeriodsCard({ d, period, setPeriod }: { d: any; period: string; setPeriod: (p: string) => void }) {
  const p = d.periods[period];
  const label = { "1": "Today vs yesterday", "7": "Last 7 days vs previous 7", "30": "Last 30 days vs previous 30", "90": "Last 90 days vs previous 90" }[period];
  return (
    <Card explain="ov.periods" title="Comparisons" subtitle={label} testId="card-periods"
      actions={<Select label="Comparison period" value={period} onChange={setPeriod} options={[{ value: "1", label: "Today" }, { value: "7", label: "7 days" },
        { value: "30", label: "30 days" }, { value: "90", label: "90 days" }]} />}>
      <table className="w-full text-xs">
        <tbody>
          {Object.entries(p).map(([k, v]: any) => {
            const c = d.comparisons.find((x: any) => x.feature === k);
            return (
              <tr key={k} className="border-t border-line">
                <td className="py-1.5 text-fg2">{c?.label ?? k}</td>
                <td className="num text-right">{fmtFeature(k, v.current, c?.unit)}</td>
                <td className="num text-right text-fg2">{fmtFeature(k, v.previous, c?.unit)}</td>
                <td className="num w-24 text-right text-fg2">{fmtDelta(v.current, v.previous, k, c?.unit)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <p className="mt-2 text-[11px] text-muted">For a custom range with confidence intervals use Health Trends.</p>
    </Card>
  );
}

export { dailyLines };
