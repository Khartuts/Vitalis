import { useQueryClient } from "@tanstack/react-query";
import { Thermometer } from "lucide-react";
import { useState } from "react";
import { navigate } from "../App";
import { Chart } from "../components/Chart";
import { Badge, Button, Card, Empty, ErrorBox, Grid, inputCls, Note, PageHeader, Prov, Skeleton, Stat } from "../components/ui";
import { engine } from "../lib/bridge";
import { fmtDate } from "../lib/format";
import { useEngine, useGlobalRange } from "../lib/hooks";

const fmtC = (v: number | null | undefined, digits = 1) => (v == null ? "—" : `${v.toFixed(digits)} °C`);
const fmtDev = (v: number | null | undefined) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(1)} °C`);

export default function Temperature() {
  const range = useGlobalRange();
  const start = range.days < 30 ? new Date(Date.parse(range.end) - 59 * 86400000).toISOString().slice(0, 10) : range.start;
  const sum = useEngine<any>("thermal.summary", { start, end: range.end }, { enabled: !!range.status });
  const sug = useEngine<any>("thermal.suggestion", { date: range.end }, { enabled: !!range.status });
  if (sum.error) return <ErrorBox error={sum.error} />;
  if (!sum.data) return <Skeleton h={500} />;
  const nights = sum.data.nights as any[];
  const dates = nights.map((n) => n.date);
  const corr = sum.data.correlations;
  return (
    <div data-testid="temperature-page">
      <PageHeader title="Body & Bedroom Temperature"
        subtitle="Overnight skin temperature from your watch compared with your own usual, and a suggested bedroom temperature for tonight. Wrist skin temperature is not a fever measurement." />
      <Grid cols={2}>
        <SuggestionCard sug={sug} today={range.end} />

        <Card explain="tm.skin" title="Overnight skin temperature" subtitle={`Each night versus your usual (personal baseline: ${sum.data.baseline.status}, ${sum.data.baseline.n} nights)`} actions={<Prov kind="measured" />}>
          {!sum.data.has_skin ? <Empty title="No overnight skin temperature yet">Galaxy Watch measures it during sleep; it arrives through Health Connect after the watch syncs.</Empty> : (
            <>
              <Chart height={230} title="Skin temperature versus your usual" zoom
                context={{ metrics: ["skin_temp_delta_night"], start, end: range.end, title: "Overnight skin temperature" }}
                table={{ columns: ["Night (wake date)", "vs your usual (°C)", "vs watch baseline (°C)"], rows: nights.map((n) => [n.date, n.personal_dev == null ? null : Math.round(n.personal_dev * 100) / 100, n.skin_delta]) }}
                option={(p) => ({ legend: { show: false }, xAxis: { type: "category", data: dates, axisLabel: { formatter: (v: string) => v.slice(5) } },
                  yAxis: { type: "value", name: "°C vs usual" },
                  series: [{ type: "bar", barMaxWidth: 12, data: nights.map((n) => ({ value: n.personal_dev == null ? null : Math.round(n.personal_dev * 100) / 100,
                    itemStyle: { color: n.personal_dev != null && n.personal_dev >= 0.5 ? p.s5 : n.personal_dev != null && n.personal_dev <= -0.5 ? p.s2 : p.s1, borderRadius: [3, 3, 0, 0] } })),
                    markLine: { silent: true, symbol: "none", data: [{ yAxis: 0.5 }], lineStyle: { color: p.muted, type: "dashed" }, label: { formatter: "+0.5 °C", color: p.muted } } }] })} />
              {sum.data.has_absolute && (
                <Chart height={140} title="Overnight skin temperature (absolute)" option={(p) => ({ legend: { show: false },
                  xAxis: { type: "category", data: dates, axisLabel: { formatter: (v: string) => v.slice(5) } }, yAxis: { type: "value", name: "°C", scale: true },
                  series: [{ type: "line", data: nights.map((n) => n.skin_abs), connectNulls: false, symbolSize: 5, lineStyle: { color: p.s4, width: 2 }, itemStyle: { color: p.s4 } }] })} />
              )}
            </>
          )}
        </Card>

        <Card explain="tm.elevated" title="Nights warmer than usual" subtitle="At least +0.5 °C above your personal baseline">
          {!sum.data.elevated_nights.length ? <Empty title="None in this period">Nights at least 0.5 °C above your usual appear here.</Empty> : (
            <ul className="space-y-1 text-[13px]">{nights.filter((n) => sum.data.elevated_nights.includes(n.date)).map((n) => (
              <li key={n.date} className="flex items-center gap-2"><span className="w-28 text-fg2">{fmtDate(n.date)}</span>
                <Badge tone="warning">{fmtDev(n.personal_dev)}</Badge>
                {n.sleep_awakenings != null && <span className="text-xs text-muted">{n.sleep_awakenings} awakenings</span>}</li>))}</ul>
          )}
          <Note>A warmer night can accompany illness, alcohol, a warm bedroom or the menstrual cycle. VITALIS does not diagnose; use a thermometer and seek medical advice if you feel unwell.</Note>
        </Card>

        <Card explain="tm.bedroom" title="Bedroom temperature and your sleep" subtitle={`${sum.data.nights_with_bedroom_temp} nights with a bedroom temperature in this period`}>
          {sum.data.nights_with_bedroom_temp < 3 ? (
            <Empty title="Log your bedroom temperature">Record it in the evening journal (Habit Journal → Environment) or in the card on the left. With about 12 nights at different temperatures VITALIS can find what suits you.</Empty>
          ) : (
            <>
              <CorrStat label="Bedroom °C vs sleep efficiency" c={corr.bedroom_vs_efficiency} />
              <Chart height={220} title="Bedroom temperature vs sleep efficiency"
                table={{ columns: ["Night", "Bedroom °C", "Sleep efficiency %"], rows: nights.filter((n) => n.bedroom_temp != null).map((n) => [n.date, n.bedroom_temp, n.sleep_efficiency]) }}
                option={(p) => ({ legend: { show: false }, tooltip: { trigger: "item", formatter: (x: any) => `${x.value[2]}: ${x.value[0]} °C, ${x.value[1]?.toFixed?.(1)} %` },
                  xAxis: { type: "value", name: "°C", scale: true }, yAxis: { type: "value", name: "efficiency %", scale: true },
                  series: [{ type: "scatter", symbolSize: 9, itemStyle: { color: p.s2, borderColor: p.surface, borderWidth: 2 },
                    data: nights.filter((n) => n.bedroom_temp != null && n.sleep_efficiency != null).map((n) => [n.bedroom_temp, n.sleep_efficiency, n.date]) }] })} />
              {sug.data?.personal_model?.bands && (
                <table className="mt-2 w-full text-xs"><thead className="text-muted"><tr><th className="text-left font-normal">Bedroom</th><th className="text-right font-normal">Nights</th><th className="text-right font-normal">Sleep efficiency</th></tr></thead>
                  <tbody>{sug.data.personal_model.bands.map((b: any) => (
                    <tr key={b.from} className="border-t border-line"><td className="py-1">{b.from.toFixed(1)}–{b.to.toFixed(1)} °C</td><td className="num text-right">{b.n}</td><td className="num text-right">{b.mean_efficiency.toFixed(1)} %</td></tr>))}</tbody></table>
              )}
            </>
          )}
        </Card>

        <Card explain="tm.skin_sleep" title="Skin temperature and your sleep" subtitle="Same-night associations (Spearman, 95 % CI)">
          <CorrStat label="Skin temperature vs sleep efficiency" c={corr.skin_vs_efficiency} />
          <CorrStat label="Skin temperature vs awakenings" c={corr.skin_vs_awakenings} />
          <CorrStat label="Bedroom °C vs skin temperature" c={corr.bedroom_vs_skin} />
          <Note>Associations in your own data, not proof of cause — routines change several things at once.</Note>
        </Card>

        <Card explain="tm.body" title="Body temperature readings" subtitle="Thermometer readings shared through Health Connect or other sources" actions={<Prov kind="measured" />}>
          {!sum.data.body_temperature.length ? <Empty title="No readings">Readings from a thermometer app that writes to Health Connect appear here.</Empty> : (
            <ul className="max-h-60 space-y-1 overflow-auto text-[13px]">{[...sum.data.body_temperature].reverse().slice(0, 50).map((r: any) => (
              <li key={r.time} className="flex justify-between border-b border-line py-1"><span className="text-fg2">{new Date(r.time).toLocaleString()}</span><span className="num">{fmtC(r.celsius)}</span></li>))}</ul>
          )}
        </Card>

        <Card explain="tm.smarthome" title="Smart home (SmartThings)" subtitle="Suggestions only — VITALIS does not control devices">
          <p className="text-[13px] text-fg2">Set the suggested temperature yourself on your thermostat, air conditioner or radiator, or with a SmartThings routine before bedtime.</p>
          <Note>Direct control is not built in: SmartThings allows lasting app access only through its paid API Access plan, and personal access tokens expire after 24 hours. A room sensor could later supply the bedroom temperature automatically.</Note>
        </Card>
      </Grid>
    </div>
  );
}

function CorrStat({ label, c }: { label: string; c: any }) {
  return (
    <div className="mb-2 flex items-center gap-3 text-[13px]">
      <span className="flex-1 text-fg2">{label}</span>
      {c?.r == null ? <span className="text-xs text-muted">not enough nights ({c?.n ?? 0})</span> : (
        <><span className="num">ρ {c.r.toFixed(2)}</span><span className="num text-xs text-muted">CI {c.ci[0].toFixed(2)} to {c.ci[1].toFixed(2)} · n {c.n}</span>
          <Badge tone={c.verdict === "association_detected" ? "accent" : "neutral"}>{String(c.verdict).replaceAll("_", " ")}</Badge></>
      )}
    </div>
  );
}

function SuggestionCard({ sug, today }: { sug: any; today: string }) {
  const qc = useQueryClient();
  const [room, setRoom] = useState("");
  const [saved, setSaved] = useState(false);
  const prefs = useEngine<any>("prefs.get");
  const s = sug.data;
  const save = async () => {
    const v = Number(room.replace(",", "."));
    if (!Number.isFinite(v) || v < 10 || v > 32) return;
    await engine("journal.day_set", { date: today, values: { bedroom_temp_c: v } });
    setSaved(true);
    qc.invalidateQueries();
  };
  const setLimit = async (k: "thermal_min_c" | "thermal_max_c", v: string) => {
    const n = Number(v);
    if (!Number.isFinite(n)) return;
    await engine("prefs.set", { values: { [k]: n } });
    qc.invalidateQueries();
  };
  return (
    <Card explain="tm.suggestion" title="Tonight's bedroom temperature" subtitle={s ? `For the night ending ${fmtDate(s.wake_date)}` : undefined} testId="thermal-suggestion" actions={<Prov kind="estimated" />}>
      {sug.error ? <ErrorBox error={sug.error} /> : !s ? <Skeleton h={200} /> : (
        <>
          <div className="flex items-end gap-4">
            <Thermometer size={28} className="mb-1 text-accent-ink" />
            <Stat label="Suggested" value={`${s.suggested_c.toFixed(1)} °C`} testId="thermal-value"
              sub={s.basis === "personal" ? `your best range ${s.interval[0].toFixed(1)}–${s.interval[1].toFixed(1)} °C` : `general guidance ${s.interval[0].toFixed(0)}–${s.interval[1].toFixed(0)} °C`} />
            <Badge tone={s.basis === "personal" ? "good" : "neutral"}>{s.basis === "personal" ? "personal" : "general guidance"}</Badge>
          </div>
          <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-fg2">{s.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
          <Note>{s.how_to_apply}</Note>
          <div className="mt-3 flex flex-wrap items-end gap-2 border-t border-line pt-3">
            <label className="text-xs text-fg2">Bedroom temperature tonight (°C)
              <span className="mt-1 block w-28"><input className={inputCls} inputMode="decimal" value={room} onChange={(e) => { setRoom(e.target.value); setSaved(false); }} placeholder="e.g. 19" data-testid="bedroom-temp-input" /></span></label>
            <Button size="sm" onClick={save} testId="bedroom-temp-save">Save to journal</Button>
            {saved && <span className="text-xs text-good">Saved ✓</span>}
            <Button size="sm" variant="ghost" onClick={() => navigate("journal")}>Open journal</Button>
          </div>
          {prefs.data && (
            <div className="mt-2 flex items-center gap-2 text-xs text-fg2">
              <span>Suggest only between</span>
              <span className="w-16"><input aria-label="Lowest suggested °C" className={inputCls} inputMode="decimal" defaultValue={prefs.data.thermal_min_c} onBlur={(e) => setLimit("thermal_min_c", e.target.value)} /></span>
              <span>and</span>
              <span className="w-16"><input aria-label="Highest suggested °C" className={inputCls} inputMode="decimal" defaultValue={prefs.data.thermal_max_c} onBlur={(e) => setLimit("thermal_max_c", e.target.value)} /></span>
              <span>°C</span>
            </div>
          )}
        </>
      )}
    </Card>
  );
}
