import type { JSX } from "react";
// Renders a report built by the engine (report.build). Used on screen and in the hidden A4 print window.
import { fmtFeature } from "../lib/format";
import { Chart, dailyLines } from "./Chart";

const SECTION_TITLE: Record<string, string> = { sleep: "Sleep analysis", cardio: "Cardiovascular trends", activity: "Activity", stress: "Stress trends" };

export function ReportView({ r, print = false }: { r: any; print?: boolean }) {
  const color = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
  const H = ({ n, t }: { n: number; t: string }) => <h2 className="mb-2 mt-6 border-b border-line pb-1 text-base font-semibold" style={{ breakAfter: "avoid" }}>{n}. {t}</h2>;
  const rec = r.recovery.filter((x: any) => x.value != null);
  return (
    <article className={print ? "mx-auto max-w-[180mm] text-[11px] leading-relaxed" : "text-[13px]"} data-testid="report-view">
      <header className="mb-4">
        <div className="text-[11px] tracking-[0.2em] text-muted">VITALIS</div>
        <h1 className="text-xl font-semibold">{r.title}</h1>
        <div className="text-xs text-fg2">{r.period.start} → {r.period.end} ({r.period.days} days) · compared with {r.comparison_period.start} → {r.comparison_period.end} · generated {r.generated_at.slice(0, 16).replace("T", " ")} UTC · app {r.app_version}</div>
        {r.demo && <div className="mt-2 rounded bg-warning/90 px-2 py-1 text-center text-[11px] font-semibold text-black">SYNTHETIC DEMO DATA — NOT REAL HEALTH DATA</div>}
      </header>
      <H n={1} t="Executive summary" />
      <ul className="list-disc pl-5">
        {Object.entries(r.sections).flatMap(([, rows]: any) => rows.slice(0, 2)).map((row: any) => (
          <li key={row.feature}>{row.label}: {fmtFeature(row.feature, row.current.mean, row.unit)} on average ({row.current.n} days)
            {row.previous.n ? `, vs ${fmtFeature(row.feature, row.previous.mean, row.unit)} in the previous period` : ""}.</li>
        ))}
        {rec.length > 0 && <li>Morning recovery estimate (relative index, 50 = usual): mean {Math.round(rec.reduce((a: number, b: any) => a + b.value, 0) / rec.length)} over {rec.length} days.</li>}
      </ul>
      <H n={2} t="Data coverage and limitations" />
      <table className="w-full"><tbody>{Object.entries(r.coverage.per_metric).map(([k, v]: any) => (
        <tr key={k} className="border-t border-line"><td className="py-0.5 text-fg2">{k}</td><td className="num text-right">{v.days_present}/{v.days_total} days ({Math.round(v.fraction * 100)}%)</td></tr>))}</tbody></table>
      <p className="mt-1 text-fg2">Overall completeness score {Math.round(r.coverage.completeness_score * 100)}%. Missing days are left empty and never imputed.</p>
      <Section n={3} sec="sleep" r={r} print={print} color={color("--series-2")} H={H} />
      <H n={4} t="Recovery trends" />
      {rec.length === 0 ? <p className="text-fg2">No recovery estimates in this period (insufficient inputs or baseline).</p> : (
        <Chart height={print ? 140 : 170} title="Recovery" option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: r.recovery.map((x: any) => x.date) },
          yAxis: { type: "value", min: 0, max: 100 }, series: [{ type: "bar", data: r.recovery.map((x: any) => x.value), barMaxWidth: 10, itemStyle: { color: p.s1, borderRadius: [3, 3, 0, 0] } }] })} />
      )}
      <Section n={5} sec="stress" r={r} print={print} color={color("--series-3")} H={H} />
      <Section n={6} sec="cardio" r={r} print={print} color={color("--series-1")} H={H} />
      <Section n={7} sec="activity" r={r} print={print} color={color("--series-4")} H={H} />
      <H n={8} t="Habits" />
      {r.habits.length === 0 ? <p className="text-fg2">No journal habits.</p> : (
        <table className="w-full"><tbody>{r.habits.filter((h: any) => h.days_logged > 0).map((h: any) => (
          <tr key={h.key} className="border-t border-line"><td className="py-0.5">{h.label}</td><td className="num text-right">{h.days_logged} days logged</td>
            <td className="num text-right">{h.mean == null ? "—" : `mean ${h.mean.toFixed(1)}`}</td></tr>))}</tbody></table>
      )}
      <H n={9} t="Statistical correlations (exploratory)" />
      {!r.correlations ? <p className="text-fg2">Not enough habit data.</p> : (
        <>
          <p className="text-fg2">{r.correlations.warning}</p>
          <table className="mt-1 w-full"><tbody>{r.correlations.results.filter((x: any) => x.r != null).slice(0, 10).map((x: any) => (
            <tr key={x.exposure + x.outcome} className="border-t border-line"><td className="py-0.5">{x.exposure_label} → {x.outcome_label} (lag {x.lag_days})</td>
              <td className="num text-right">ρ {x.r.toFixed(2)} [{x.ci[0].toFixed(2)}, {x.ci[1].toFixed(2)}]</td><td className="num text-right">n {x.n}</td>
              <td className="num text-right">q {x.q.toFixed(3)}{x.survives_fdr ? " ✓" : ""}</td></tr>))}</tbody></table>
        </>
      )}
      <H n={10} t="Important changes" />
      {r.important_changes.length === 0 ? <p className="text-fg2">No notable changes were detected by the insight rules.</p> : (
        <ul className="list-disc pl-5">{r.important_changes.map((i: any) => <li key={i.id}><b>{i.title}.</b> {i.observation} <span className="text-muted">({i.confidence} confidence)</span></li>)}</ul>
      )}
      <H n={11} t="Practical recommendations" />
      {r.recommendations.length === 0 ? <p className="text-fg2">Keep logging consistently; no specific actions were triggered.</p> : (
        <ul className="list-disc pl-5">{r.recommendations.map((x: any) => <li key={x.title}>{x.action}</li>)}</ul>
      )}
      <H n={12} t="Methods and algorithms" />
      <ul className="list-disc pl-5">{r.methods.notes.map((n: string) => <li key={n}>{n}</li>)}</ul>
      <p className="mt-1 text-fg2">Versions: {Object.entries(r.methods.algorithms).map(([k, v]) => `${k} ${v}`).join(" · ")}</p>
      <H n={13} t="Data-source provenance" />
      <table className="w-full"><thead className="text-muted"><tr><th className="text-left font-normal">Source</th><th className="text-left font-normal">Metric</th><th className="text-right font-normal">Records</th><th className="text-right font-normal">Flagged</th></tr></thead>
        <tbody>{r.sources.map((s: any, k: number) => (
          <tr key={k} className="border-t border-line"><td className="py-0.5">{s.provider}{s.app_package ? ` · ${s.app_package}` : ""}{s.device_model ? ` · ${s.device_model}` : ""}</td>
            <td>{s.metric}</td><td className="num text-right">{s.n.toLocaleString()}</td><td className="num text-right">{(s.flagged_fraction * 100).toFixed(1)}%</td></tr>))}</tbody></table>
      <p className="mt-6 border-t border-line pt-2 text-[10px] text-muted">{r.disclaimer}</p>
    </article>
  );
}

function Section({ n, sec, r, print, color, H }: { n: number; sec: string; r: any; print: boolean; color: string; H: (p: { n: number; t: string }) => JSX.Element }) {
  const rows = r.sections[sec] ?? [];
  return (
    <section style={{ breakInside: "avoid" }}>
      <H n={n} t={SECTION_TITLE[sec]} />
      {rows.length === 0 ? <p className="text-fg2">No data for this section.</p> : (
        <>
          <table className="w-full"><thead className="text-muted"><tr><th className="text-left font-normal">Metric</th><th className="text-right font-normal">Mean</th>
            <th className="text-right font-normal">Range</th><th className="text-right font-normal">Previous</th><th className="text-right font-normal">Days</th></tr></thead>
            <tbody>{rows.map((row: any) => (
              <tr key={row.feature} className="border-t border-line"><td className="py-0.5">{row.label}</td><td className="num text-right">{fmtFeature(row.feature, row.current.mean, row.unit)}</td>
                <td className="num text-right text-fg2">{fmtFeature(row.feature, row.current.min, row.unit)} – {fmtFeature(row.feature, row.current.max, row.unit)}</td>
                <td className="num text-right text-fg2">{fmtFeature(row.feature, row.previous.mean, row.unit)}</td><td className="num text-right">{row.current.n}</td></tr>))}</tbody></table>
          <Chart height={print ? 150 : 180} title={SECTION_TITLE[sec]} option={dailyLines(rows[0].series.map((x: any) => x.date),
            [{ name: rows[0].label, data: rows[0].series.map((x: any) => x.value), color }])} />
        </>
      )}
    </section>
  );
}
