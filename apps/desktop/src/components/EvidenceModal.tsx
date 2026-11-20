import { fmtFeature } from "../lib/format";
import { useEngine, useSeries } from "../lib/hooks";
import { useUi } from "../lib/store";
import { Chart, dailyLines } from "./Chart";
import { Empty, Modal, Prov, Skeleton, Stat } from "./ui";

/** Opens the exact metric/date range an AI answer (or the command palette) refers to. */
export function EvidenceModal() {
  const ev = useUi((s) => s.evidence);
  const close = useUi((s) => s.showEvidence);
  const isRecovery = ev?.metric === "recovery";
  const s = useSeries(ev && !isRecovery ? [ev.metric] : ["rhr"], ev?.start ?? "", ev?.end ?? "");
  const rec = useEngine<any[]>("recovery.range", ev ? { start: ev.start, end: ev.end } : undefined, { enabled: !!ev && isRecovery });
  if (!ev) return null;
  const dates = isRecovery ? (rec.data ?? []).map((r) => r.local_date) : s.data?.dates ?? [];
  const values = isRecovery ? (rec.data ?? []).map((r) => r.value) : s.data?.series[ev.metric] ?? [];
  const info = s.data?.info?.[ev.metric];
  const present = values.filter((v) => v != null) as number[];
  return (
    <Modal open onOpenChange={(o) => !o && close(null)} wide title={ev.label ?? info?.label ?? ev.metric}
      description={`Source data for ${ev.start} → ${ev.end}. Gaps are days without data; nothing is interpolated.`}>
      {s.isLoading || rec.isLoading ? <Skeleton h={260} /> : present.length === 0 ? (
        <Empty title="No data in this range">This metric has no stored values for the selected dates.</Empty>
      ) : (
        <>
          <div className="mb-4 flex gap-8">
            <Stat label="Days with data" value={`${present.length} / ${values.length}`} />
            <Stat label="Mean" value={fmtFeature(ev.metric, present.reduce((a, b) => a + b, 0) / present.length, info?.unit)} />
            <Stat label="Range" value={`${fmtFeature(ev.metric, Math.min(...present), info?.unit)} – ${fmtFeature(ev.metric, Math.max(...present), info?.unit)}`} />
            <div className="self-end"><Prov kind={info?.provenance ?? "calculated"} /></div>
          </div>
          <Chart height={280} title={ev.label ?? ev.metric} zoom
            option={dailyLines(dates, [{ name: info?.label ?? ev.metric, data: values, color: getComputedStyle(document.documentElement).getPropertyValue("--series-1").trim(), area: true }])}
            table={{ columns: ["Date", info?.label ?? ev.metric], rows: dates.map((d, i) => [d, values[i] == null ? null : Math.round(values[i]! * 10) / 10]) }}
            context={{ metrics: [ev.metric], start: ev.start, end: ev.end, title: ev.label }} />
        </>
      )}
    </Modal>
  );
}
