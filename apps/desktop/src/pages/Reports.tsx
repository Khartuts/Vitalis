import { Download, FileJson, FileSpreadsheet, FileText } from "lucide-react";
import { useState } from "react";
import { ReportView } from "../components/ReportView";
import { Badge, Button, Card, ErrorBox, Grid, PageHeader, Select, Skeleton, Spinner, TabPanel, Tabs } from "../components/ui";
import { invoke } from "../lib/bridge";
import { fmtFeature } from "../lib/format";
import { useEngine, useGlobalRange } from "../lib/hooks";
import { addDays } from "../lib/store";

export default function Reports() {
  const range = useGlobalRange();
  const [preset, setPreset] = useState("30");
  const [tab, setTab] = useState("report");
  const end = range.end;
  const start = preset === "range" ? range.start : addDays(end, -(Number(preset) - 1));
  const rep = useEngine<any>("report.build", { start, end }, { enabled: !!range.status && tab === "report" });
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);
  const run = async (label: string, f: () => Promise<any>) => {
    setBusy(label); setErr(null); setDone(null);
    try { const r = await f(); if (r?.path) setDone(`Saved ${r.path}`); else if (r?.files) setDone(`Exported ${Object.keys(r.files).length} files`); }
    catch (e) { setErr(e); } finally { setBusy(null); }
  };
  return (
    <div data-testid="reports-page">
      <PageHeader title="Reports" subtitle="Reports contain the actual analytical figures for the selected period, with methods and data provenance. A4-ready PDF."
        actions={<>
          <Select label="Report period" value={preset} onChange={setPreset} options={[{ value: "7", label: "Last 7 days" }, { value: "30", label: "Last 30 days" },
            { value: "90", label: "Last 90 days" }, { value: "365", label: "Last 365 days" }, { value: "range", label: "Global range (custom)" }]} testId="report-preset" />
          <Button variant="primary" onClick={() => run("PDF", () => invoke("report:pdf", { start, end }))} testId="export-pdf"><FileText size={13} />PDF</Button>
          <Button onClick={() => run("CSV", () => invoke("export:csv", { start, end }))}><FileSpreadsheet size={13} />CSV</Button>
          <Button onClick={() => run("JSON", () => invoke("report:json", { start, end }))}><FileJson size={13} />JSON</Button>
          <Button onClick={() => run("All data", () => invoke("export:all"))}><Download size={13} />All data</Button>
        </>} />
      {busy && <Spinner label={`Exporting ${busy}…`} />}{done && <div className="mb-2 text-xs text-good" role="status">{done}</div>}{!!err && <ErrorBox error={err} />}
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "report", label: "Health report" }, { id: "briefs", label: "Briefs" }]}>
        <TabPanel value="report">
          <Card>{rep.error ? <ErrorBox error={rep.error} /> : !rep.data ? <Skeleton h={600} /> : <ReportView r={rep.data} />}</Card>
        </TabPanel>
        <TabPanel value="briefs"><Briefs date={end} /></TabPanel>
      </Tabs>
    </div>
  );
}

function Briefs({ date }: { date: string }) {
  const [kind, setKind] = useState("morning");
  const b = useEngine<any>("briefs.get", { date, kind });
  return (
    <Card title={{ morning: "Daily Morning Brief", weekly: "Weekly Health Review", monthly: "Monthly Deep Analysis", quarterly: "Quarterly Trend Report" }[kind]}
      actions={<Select label="Brief type" value={kind} onChange={setKind} options={[{ value: "morning", label: "Morning" }, { value: "weekly", label: "Weekly" },
        { value: "monthly", label: "Monthly" }, { value: "quarterly", label: "Quarterly" }]} />}>
      {!b.data ? <Skeleton h={300} /> : (
        <Grid cols={2}>
          <div>
            <div className="mb-2 text-xs text-fg2">Period {b.data.period[0]} → {b.data.period[1]}{b.data.demo && <> · <Badge tone="warning">synthetic</Badge></>}</div>
            <table className="w-full text-xs"><thead className="text-muted"><tr><th className="text-left font-normal">Metric</th><th className="text-right font-normal">{kind === "morning" ? "Today" : "This period"}</th>
              <th className="text-right font-normal">{kind === "morning" ? "Usual" : "Previous"}</th></tr></thead>
              <tbody>{b.data.metrics.map((m: any) => (
                <tr key={m.feature} className="border-t border-line"><td className="py-1 text-fg2">{m.label}</td><td className="num text-right">{fmtFeature(m.feature, m.current, m.unit)}</td>
                  <td className="num text-right text-fg2">{fmtFeature(m.feature, kind === "morning" ? m.baseline?.median : m.previous, m.unit)}</td></tr>))}</tbody></table>
            {kind === "morning" && b.data.recovery?.value != null && <p className="mt-3 text-xs">Recovery estimate: <b>{Math.round(b.data.recovery.value)}</b> ({b.data.recovery.category.replaceAll("_", " ")}).</p>}
            {kind !== "morning" && b.data.recovery?.mean_index != null && <p className="mt-3 text-xs">Mean recovery index: <b>{Math.round(b.data.recovery.mean_index)}</b> over {b.data.recovery.days} days.</p>}
          </div>
          <div>
            <div className="mb-2 text-xs font-semibold">Insights in this period</div>
            {b.data.insights.length === 0 ? <p className="text-xs text-fg2">Nothing notable.</p> : (
              <ul className="space-y-2 text-xs">{b.data.insights.map((i: any) => <li key={i.id}><b>{i.title}</b><div className="text-fg2">{i.observation}</div>{i.action && <div>→ {i.action}</div>}</li>)}</ul>
            )}
          </div>
        </Grid>
      )}
    </Card>
  );
}
