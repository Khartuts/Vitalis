import { HelpCircle, MessageSquare } from "lucide-react";
import { useState } from "react";
import { Button, Modal } from "../components/ui";
import { fmtFeature } from "../lib/format";
import { useEngine, useGlobalRange } from "../lib/hooks";
import { IS_ANDROID } from "../lib/platform";
import { useUi } from "../lib/store";
import { EXPLAIN, type ExplainId } from "./content";

const STATUS_TEXT: Record<string, string> = {
  insufficient: "insufficient (not enough days yet)",
  provisional: "provisional",
  stable: "stable",
};
const SCORE_LABEL: Record<string, string> = {
  recovery: "Morning recovery estimate", load: "Training load", energy_summary: "Daily energy estimate", arousal: "Arousal summary",
};

/** "Explain" button for a card header: built-in explanation + live "Your data" + "Ask the analyst about this". */
export function ExplainButton({ id }: { id: ExplainId }) {
  const [open, setOpen] = useState(false);
  const e = EXPLAIN[id];
  return (
    <>
      <Button size="sm" variant="ghost" title={`Explain: ${e.title}`} onClick={() => setOpen(true)} testId={`explain-${id}`}>
        <HelpCircle size={13} />Explain
      </Button>
      {open && <ExplainPanel id={id} onClose={() => setOpen(false)} />}
    </>
  );
}

function ExplainPanel({ id, onClose }: { id: ExplainId; onClose: () => void }) {
  const e = EXPLAIN[id];
  const range = useGlobalRange();
  const features = "features" in e ? (e.features as string[]) : [];
  const scores = "scores" in e ? (e.scores as string[]) : [];
  const wantsData = features.length > 0 || scores.length > 0;
  const data = useEngine<any>("explain.data", { date: range.end, features, scores }, { enabled: wantsData && !!range.end });
  const ask = () => {
    // The AI request accepts up to 4 chart metrics; entries list the most relevant first.
    useUi.getState().explain({ metrics: features.slice(0, 4), start: range.start, end: range.end, title: e.title }, e.ask);
    onClose();
  };
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={e.title} description={e.what} wide>
      <div className="space-y-4 text-[13px] leading-relaxed" data-testid="explain-panel">
        <Section title="How it's calculated" items={e.how} />
        <Section title="How to read it" items={e.read} />
        {wantsData && (
          <section data-testid="explain-your-data">
            <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">Your data (up to {range.end})</h4>
            {data.error ? <p className="text-critical">Could not load: {String((data.error as Error).message ?? data.error)}</p>
              : !data.data ? <p className="text-muted">Loading…</p> : (
                <ul className="space-y-1 text-fg2">
                  {data.data.features.map((f: any) => (
                    <li key={f.key}><b className="text-fg">{f.label}</b>: {f.days === 0 ? "no data yet." : <>
                      {f.days} day{f.days === 1 ? "" : "s"} with data ({f.recent_days} in the last {data.data.recent_window_days} days), {f.first} → {f.last}.
                      {" "}Baseline: {STATUS_TEXT[f.baseline.status] ?? f.baseline.status}, {f.baseline.n} valid day{f.baseline.n === 1 ? "" : "s"}
                      {f.baseline.median != null && <> · usual {fmtFeature(f.key, f.baseline.median, f.unit)}</>}
                      {f.baseline.status === "insufficient" && <> — needs {data.data.baseline_settings.min_provisional} (stable from {data.data.baseline_settings.min_stable})</>}.
                    </>}</li>
                  ))}
                  {data.data.scores.map((s: any) => (
                    <li key={s.kind}><b className="text-fg">{SCORE_LABEL[s.kind] ?? s.kind}</b>: {s.days === 0 ? "not estimated on any day yet." : <>
                      estimated on {s.days} day{s.days === 1 ? "" : "s"} ({s.recent_days} in the last {data.data.recent_window_days} days)
                      {s.on_date ? (s.on_date.value == null ? "; not available on this day." : `; this day: ${Math.round(s.on_date.value)}${s.on_date.category ? ` (${String(s.on_date.category).replaceAll("_", " ")})` : ""}.`) : "."}
                    </>}</li>
                  ))}
                </ul>
              )}
          </section>
        )}
        <Section title="Limitations" items={e.limits} />
        {"algorithm" in e && <p className="text-xs text-muted">Method: {String(e.algorithm)} — details in ALGORITHMS.md.</p>}
        {!IS_ANDROID && <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-3">
          <p className="max-w-[60ch] text-xs text-muted">"Ask the analyst" opens the AI analyst with a question about this. Nothing is sent until you
            have seen and approved the exact request.</p>
          <Button variant="primary" onClick={ask} testId="explain-ask"><MessageSquare size={13} />Ask the analyst about this</Button>
        </div>}
      </div>
    </Modal>
  );
}

function Section({ title, items }: { title: string; items: string[] }) {
  return (
    <section>
      <h4 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">{title}</h4>
      <ul className="list-disc space-y-1 pl-5 text-fg2">{items.map((t, i) => <li key={i}>{t}</li>)}</ul>
    </section>
  );
}
