import { useQueryClient } from "@tanstack/react-query";
import { Plus, Save, X } from "lucide-react";
import { useMemo, useState } from "react";
import { Chart, dailyLines } from "../components/Chart";
import { Badge, Button, Card, Empty, Field, Grid, inputCls, Note, PageHeader, Prov, Select, Skeleton, Stat } from "../components/ui";
import { engine } from "../lib/bridge";
import { fmtFeature } from "../lib/format";
import { useEngine, useGlobalRange, useSeries } from "../lib/hooks";
import { addDays } from "../lib/store";
import { getVar } from "./SleepLab";

const COLORS = ["--series-1", "--series-2", "--series-3", "--series-4"];

export default function Trends() {
  const qc = useQueryClient();
  const range = useGlobalRange();
  const catalog = useEngine<any[]>("features.catalog");
  const prefs = useEngine<any>("prefs.get");
  const [features, setFeatures] = useState<string[]>(["rhr", "sleep_tst_min"]);
  const [roll, setRoll] = useState("7");
  const [viewName, setViewName] = useState("");
  const start = range.days < 30 ? addDays(range.end, -89) : range.start;
  const s = useSeries(features, start, range.end);
  const available = (catalog.data ?? []).filter((c) => c.days > 0);
  const n = Math.max(7, range.days < 30 ? 30 : Math.round(range.days / 2));
  const [cmpFeature, setCmpFeature] = useState("sleep_tst_min");
  const cmp = useEngine<any>("compare.periods", { feature: cmpFeature, a_start: addDays(range.end, -(n - 1)), a_end: range.end,
    b_start: addDays(range.end, -(2 * n - 1)), b_end: addDays(range.end, -n) }, { enabled: !!range.status });
  const saved: { name: string; features: string[] }[] = prefs.data?.saved_views ?? [];
  const saveView = async () => {
    if (!viewName.trim()) return;
    await engine("prefs.set", { values: { saved_views: [...saved.filter((v) => v.name !== viewName.trim()), { name: viewName.trim(), features }] } });
    setViewName("");
    qc.invalidateQueries({ queryKey: ["prefs.get"] });
  };
  const smoothed = useMemo(() => {
    if (!s.data) return null;
    const k = Number(roll);
    return Object.fromEntries(features.map((f) => [f, (s.data!.series[f] ?? []).map((_, i, arr) => {
      if (k <= 1) return arr[i];
      const w = arr.slice(Math.max(0, i - k + 1), i + 1).filter((x): x is number => x != null);
      return w.length >= Math.ceil(k / 2) ? w.reduce((a, b) => a + b, 0) / w.length : null;
    })]));
  }, [s.data, roll, features]);
  return (
    <div data-testid="trends-page">
      <PageHeader title="Health Trends" subtitle="Explore any stored metric over time. Metrics with different units are drawn as separate, aligned charts (never two y-axes)." />
      <Card title="Metric explorer" actions={<div className="flex items-center gap-2">
        <Select label="Smoothing" value={roll} onChange={setRoll} options={[{ value: "1", label: "Daily values" }, { value: "7", label: "7-day average" }, { value: "28", label: "28-day average" }]} />
      </div>}>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          {features.map((f, i) => (
            <Badge key={f} tone="neutral"><i className="inline-block h-2 w-2 rounded-full" style={{ background: getVar(COLORS[i]) }} />{s.data?.info[f]?.label ?? f}
              <button aria-label={`Remove ${f}`} onClick={() => setFeatures(features.filter((x) => x !== f))}><X size={11} /></button></Badge>
          ))}
          {features.length < 4 && (
            <select aria-label="Add metric" className={inputCls + " w-64"} value="" onChange={(e) => e.target.value && setFeatures([...features, e.target.value])} data-testid="add-metric">
              <option value="">+ Add metric…</option>
              {["sleep", "cardio", "activity", "stress", "recovery", "body", "journal", "quality"].map((g) => (
                <optgroup key={g} label={g}>{available.filter((c) => c.group === g && !features.includes(c.key)).map((c) => <option key={c.key} value={c.key}>{c.label} ({c.days} d)</option>)}</optgroup>))}
            </select>
          )}
          <div className="flex-1" />
          <input className={inputCls + " w-40"} placeholder="Save view as…" value={viewName} onChange={(e) => setViewName(e.target.value)} maxLength={40} />
          <Button size="sm" onClick={saveView} disabled={!viewName.trim()}><Save size={12} />Save view</Button>
        </div>
        {saved.length > 0 && <div className="mb-3 flex flex-wrap gap-1.5 text-xs text-fg2">Saved views:
          {saved.map((v) => <Button key={v.name} size="sm" variant="ghost" onClick={() => setFeatures(v.features)}><Plus size={11} />{v.name}</Button>)}</div>}
        {!s.data || !smoothed ? <Skeleton h={300} /> : features.length === 0 ? <Empty title="Pick a metric to start" /> : (
          <div className="space-y-6">
            {features.map((f, i) => {
              const info = s.data!.info[f];
              const raw = s.data!.series[f] ?? [];
              const vals = raw.filter((v): v is number => v != null);
              return (
                <div key={f}>
                  <div className="mb-1 flex items-center gap-3 text-xs"><b className="text-fg">{info?.label ?? f}</b><span className="text-muted">{info?.unit}</span><Prov kind={info?.provenance} />
                    <span className="text-fg2">{vals.length} days · mean {fmtFeature(f, vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : null, info?.unit)}</span></div>
                  {vals.length === 0 ? <Empty title="No data in this range" /> : (
                    <Chart height={200} title={info?.label ?? f} zoom context={{ metrics: [f], start, end: range.end, title: info?.label }}
                      table={{ columns: ["Date", info?.label ?? f], rows: s.data!.dates.map((d, j) => [d, raw[j] == null ? null : Math.round(raw[j]! * 10) / 10]) }}
                      option={dailyLines(s.data!.dates, roll === "1" ? [{ name: info?.label ?? f, data: raw, color: getVar(COLORS[i]) }] :
                        [{ name: `${roll}-day average`, data: smoothed[f], color: getVar(COLORS[i]) }, { name: "Daily", data: raw, color: getVar("--text-muted"), dashed: true }])} />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Card>
      <div className="mt-4">
        <Grid cols={2}>
          <Card title="Period comparison with uncertainty" subtitle={`Last ${n} days vs the ${n} days before (bootstrap 95% CI of the difference in means)`}
            actions={<Select label="Metric to compare" value={cmpFeature} onChange={setCmpFeature} options={available.filter((c) => !c.key.startsWith("j:")).map((c) => ({ value: c.key, label: c.label }))} />}>
            {!cmp.data ? <Skeleton h={120} /> : (
              <div className="grid grid-cols-3 gap-3" data-testid="period-compare">
                <Stat label="Recent" value={fmtFeature(cmpFeature, cmp.data.period_a.mean, cmp.data.unit)} sub={`n = ${cmp.data.period_a.n}`} />
                <Stat label="Before" value={fmtFeature(cmpFeature, cmp.data.period_b.mean, cmp.data.unit)} sub={`n = ${cmp.data.period_b.n}`} />
                <Stat label="Difference" value={cmp.data.difference_mean == null ? "—" : fmtFeature(cmpFeature, cmp.data.difference_mean, cmp.data.unit)}
                  sub={cmp.data.difference_ci95 ? `95% CI ${cmp.data.difference_ci95.map((x: number) => x.toFixed(1)).join(" to ")}` : cmp.data.verdict} />
                <div className="col-span-3"><Badge tone={cmp.data.verdict === "difference_detected" ? "accent" : "neutral"}>{cmp.data.verdict.replaceAll("_", " ")}</Badge>
                  {cmp.data.hedges_g != null && <span className="ml-2 text-xs text-fg2">effect size g = {cmp.data.hedges_g.toFixed(2)}</span>}</div>
              </div>
            )}
            <Note>A difference "detected" here means the interval excludes zero for these days; it does not tell you why it changed.</Note>
          </Card>
          <Card title="Long-term view" subtitle="Monthly means over the last year (open the 1 year range for daily detail)">
            <MonthlyView end={range.end} feature={cmpFeature} />
          </Card>
        </Grid>
      </div>
    </div>
  );
}

function MonthlyView({ end, feature }: { end: string; feature: string }) {
  const s = useSeries([feature], addDays(end, -364), end);
  const months = useMemo(() => {
    if (!s.data) return [];
    const m = new Map<string, number[]>();
    s.data.dates.forEach((d, i) => { const v = s.data!.series[feature][i]; if (v != null) m.set(d.slice(0, 7), [...(m.get(d.slice(0, 7)) ?? []), v]); });
    return [...m.entries()].map(([k, v]) => ({ month: k, mean: v.reduce((a, b) => a + b, 0) / v.length, n: v.length }));
  }, [s.data, feature]);
  if (!months.length) return <Empty title="No data in the last year" />;
  return <Chart height={200} title="Monthly means" table={{ columns: ["Month", "Mean", "Days"], rows: months.map((m) => [m.month, Math.round(m.mean * 10) / 10, m.n]) }}
    option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: months.map((m) => m.month) }, yAxis: { type: "value", scale: true },
      tooltip: { formatter: (x: any) => `${x[0].name}: ${fmtFeature(feature, months[x[0].dataIndex].mean)} (${months[x[0].dataIndex].n} days)` },
      series: [{ type: "bar", data: months.map((m) => m.mean), barMaxWidth: 26, itemStyle: { color: p.s2, borderRadius: [4, 4, 0, 0] } }] })} />;
}
