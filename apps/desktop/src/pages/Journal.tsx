import { useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Lock, Plus, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Badge, Button, Card, Empty, ErrorBox, Field, Grid, inputCls, Modal, PageHeader, Skeleton, Switch, TabPanel, Tabs } from "../components/ui";
import { engine } from "../lib/bridge";
import { fmtDate } from "../lib/format";
import { useEngine, useGlobalRange } from "../lib/hooks";
import { addDays } from "../lib/store";

const CATS = ["sleep", "caffeine", "hydration", "nutrition", "activity", "lifestyle", "experience", "custom"];

export default function Journal() {
  const range = useGlobalRange();
  const [tab, setTab] = useState("checkin");
  const [date, setDate] = useState<string | null>(null);
  const d = date ?? range.end;
  return (
    <div data-testid="journal-page">
      <PageHeader title="Habit Journal" subtitle="A quick daily check-in (under two minutes). Your entries power the Correlation Lab. Private habits are never sent to cloud AI." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "checkin", label: "Daily check-in" }, { id: "history", label: "History" }, { id: "habits", label: "Manage habits" }]}>
        <TabPanel value="checkin"><CheckIn date={d} setDate={setDate} /></TabPanel>
        <TabPanel value="history"><History end={range.end} onEdit={(x) => { setDate(x); setTab("checkin"); }} /></TabPanel>
        <TabPanel value="habits"><Habits /></TabPanel>
      </Tabs>
    </div>
  );
}

function CheckIn({ date, setDate }: { date: string; setDate: (d: string) => void }) {
  const qc = useQueryClient();
  const habits = useEngine<any[]>("journal.habits");
  const day = useEngine<any>("journal.day_get", { date });
  const [vals, setVals] = useState<Record<string, any>>({});
  const [note, setNote] = useState("");
  const [state, setState] = useState<"idle" | "saving" | "saved">("idle");
  const [err, setErr] = useState<unknown>(null);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  useEffect(() => {
    if (!day.data || loadedFor === date) return; // load once per date; refetches after saving keep the form as is
    const v: Record<string, any> = {};
    for (const [k, e] of Object.entries<any>(day.data.entries ?? {})) v[k] = e.value_text ?? (e.value_num == null ? null : e.value_num);
    setVals(v);
    setNote(day.data.note ?? "");
    setState("idle");
    setLoadedFor(date);
  }, [day.data, date, loadedFor]);
  const active = (habits.data ?? []).filter((h) => h.active);
  const byCat = useMemo(() => CATS.map((c) => [c, active.filter((h) => h.category === c)] as const).filter(([, l]) => l.length), [active]);
  const set = (k: string, v: any) => { setVals({ ...vals, [k]: v }); setState("idle"); };
  const save = async () => {
    setState("saving"); setErr(null);
    try {
      const values: Record<string, any> = {};
      for (const h of active) {
        const v = vals[h.key];
        values[h.key] = h.value_type === "boolean" ? (v == null ? null : !!v) : v === "" ? null : v ?? null;
      }
      await engine("journal.day_set", { date, values, note });
      setState("saved");
      qc.invalidateQueries({ queryKey: ["journal.day_get"] });
    } catch (e) { setErr(e); setState("idle"); }
  };
  if (!habits.data || !day.data) return <Skeleton h={400} />;
  return (
    <Card explain="jr.checkin" title={<span className="flex items-center gap-2">
      <Button size="sm" variant="ghost" onClick={() => setDate(addDays(date, -1))} title="Previous day"><ChevronLeft size={14} /></Button>
      {fmtDate(date, { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
      <Button size="sm" variant="ghost" onClick={() => setDate(addDays(date, 1))} title="Next day"><ChevronRight size={14} /></Button></span>}
      actions={<><input type="date" className={inputCls + " w-40"} value={date} onChange={(e) => e.target.value && setDate(e.target.value)} aria-label="Journal date" />
        <Button variant="primary" onClick={save} disabled={state === "saving"} testId="journal-save">{state === "saving" ? "Saving…" : state === "saved" ? "Saved ✓" : "Save day"}</Button></>}>
      <div className="grid grid-cols-1 gap-x-8 gap-y-5 lg:grid-cols-2 2xl:grid-cols-3">
        {byCat.map(([cat, list]) => (
          <fieldset key={cat}>
            <legend className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">{cat}</legend>
            <div className="space-y-2">
              {list.map((h) => <HabitInput key={h.key} h={h} value={vals[h.key]} onChange={(v) => set(h.key, v)} />)}
            </div>
          </fieldset>
        ))}
      </div>
      <div className="mt-5"><Field label="Notes for this day"><textarea className={inputCls} rows={2} value={note} onChange={(e) => { setNote(e.target.value); setState("idle"); }} maxLength={5000} /></Field></div>
      {!!err && <div className="mt-3"><ErrorBox error={err} /></div>}
    </Card>
  );
}

function HabitInput({ h, value, onChange }: { h: any; value: any; onChange: (v: any) => void }) {
  const label = <span className="flex items-center gap-1 text-[13px] text-fg">{h.label}{h.private && <Lock size={11} className="text-muted" aria-label="private" />}</span>;
  if (h.value_type === "boolean") {
    return (
      <div className="flex items-center justify-between gap-2">{label}
        <div role="radiogroup" aria-label={h.label} className="flex overflow-hidden rounded-md border border-line text-xs">
          {[["Yes", true], ["No", false], ["—", null]].map(([l, v]) => (
            <button key={String(l)} role="radio" aria-checked={value === v || (v === null && value == null)} data-testid={`habit-${h.key}-${l}`}
              onClick={() => onChange(v)} className={`px-2 py-0.5 ${(value === v || (v === null && value == null)) ? "bg-s3 text-fg" : "text-fg2"}`}>{l as string}</button>))}
        </div>
      </div>
    );
  }
  if (h.value_type === "scale") {
    return (
      <div>{label}
        <div className="mt-1 flex gap-0.5" role="radiogroup" aria-label={h.label}>
          {Array.from({ length: 10 }, (_, i) => i + 1).map((n) => (
            <button key={n} role="radio" aria-checked={value === n} onClick={() => onChange(value === n ? null : n)}
              className={`h-6 flex-1 rounded text-[11px] ${value === n ? "bg-accent text-white" : "bg-s2 text-fg2 hover:bg-s3"}`}>{n}</button>))}
        </div>
      </div>
    );
  }
  if (h.value_type === "number") {
    return (
      <div className="flex items-center justify-between gap-2">{label}
        <div className="flex items-center gap-1">
          <input type="number" aria-label={h.label} className={inputCls + " w-20 text-right"} value={value ?? ""} min={h.options?.min} max={h.options?.max} step={h.options?.step ?? 1}
            onChange={(e) => onChange(e.target.value === "" ? null : Number(e.target.value))} data-testid={`habit-${h.key}`} />
          <span className="w-12 text-[11px] text-muted">{h.unit}</span>
        </div>
      </div>
    );
  }
  if (h.value_type === "time") {
    return <div className="flex items-center justify-between gap-2">{label}<input type="time" aria-label={h.label} className={inputCls + " w-28"} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)} /></div>;
  }
  if (h.value_type === "category") {
    return (
      <div className="flex items-center justify-between gap-2">{label}
        <select aria-label={h.label} className={inputCls + " w-32"} value={value ?? ""} onChange={(e) => onChange(e.target.value || null)}>
          <option value="">—</option>{(h.options?.choices ?? []).map((c: string) => <option key={c}>{c}</option>)}</select>
      </div>
    );
  }
  return <Field label={h.label}><input className={inputCls} value={value ?? ""} onChange={(e) => onChange(e.target.value)} maxLength={2000} /></Field>;
}

function History({ end, onEdit }: { end: string; onEdit: (d: string) => void }) {
  const start = addDays(end, -27);
  const entries = useEngine<any[]>("journal.range", { start, end });
  const habits = useEngine<any[]>("journal.habits");
  if (!entries.data || !habits.data) return <Skeleton h={300} />;
  const days = Array.from({ length: 28 }, (_, i) => addDays(start, i));
  const by = new Map(entries.data.map((e) => [`${e.local_date}|${e.habit_key}`, e]));
  const shown = habits.data.filter((h) => h.active && entries.data!.some((e) => e.habit_key === h.key));
  if (!shown.length) return <Empty title="No entries in the last 4 weeks">Use the daily check-in to start logging.</Empty>;
  return (
    <Card explain="jr.weeks" title="Last 4 weeks" subtitle="Click a day to review or edit it">
      <div className="overflow-auto">
        <table className="text-[11px]">
          <thead><tr><th className="sticky left-0 bg-s1 pr-3 text-left font-normal text-muted">Habit</th>
            {days.map((d) => <th key={d} className="px-0.5 font-normal text-muted"><button onClick={() => onEdit(d)} className="hover:text-fg">{d.slice(8)}</button></th>)}</tr></thead>
          <tbody>{shown.map((h) => (
            <tr key={h.key} className="border-t border-line"><td className="sticky left-0 whitespace-nowrap bg-s1 py-1 pr-3 text-fg2">{h.label}</td>
              {days.map((d) => {
                const e = by.get(`${d}|${h.key}`);
                const v = e ? (e.value_text ?? (h.value_type === "boolean" ? (e.value_num ? "✓" : "✗") : e.value_num)) : "";
                return <td key={d} className="num px-0.5 text-center"><button onClick={() => onEdit(d)} className={`h-6 w-9 rounded ${e ? "bg-s3 text-fg" : "bg-s2 text-muted"}`}>{String(v).slice(0, 5)}</button></td>;
              })}</tr>))}</tbody>
        </table>
      </div>
    </Card>
  );
}

function Habits() {
  const qc = useQueryClient();
  const habits = useEngine<any[]>("journal.habits");
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({ key: "", label: "", category: "custom", value_type: "boolean", unit: "", private: false, choices: "" });
  const [err, setErr] = useState<unknown>(null);
  const upsert = async (h: any) => { await engine("journal.habit_upsert", h); qc.invalidateQueries({ queryKey: ["journal.habits"] }); };
  const create = async () => {
    setErr(null);
    try {
      await upsert({ key: form.key, label: form.label, category: form.category, value_type: form.value_type, unit: form.unit || null, private: form.private,
        options: form.value_type === "category" ? { choices: form.choices.split(",").map((s) => s.trim()).filter(Boolean) } : form.value_type === "scale" ? { min: 1, max: 10 } : null });
      setOpen(false);
    } catch (e) { setErr(e); }
  };
  if (!habits.data) return <Skeleton h={300} />;
  return (
    <>
      <div className="mb-3 flex justify-end"><Button variant="primary" onClick={() => setOpen(true)} testId="new-habit"><Plus size={13} />New custom habit</Button></div>
      <Grid cols={2}>
        {CATS.map((c) => {
          const list = habits.data!.filter((h) => h.category === c);
          if (!list.length) return null;
          return (
            <Card explain="jr.category" key={c} title={c[0].toUpperCase() + c.slice(1)}>
              <ul className="space-y-1.5">{list.map((h) => (
                <li key={h.key} className="flex items-center gap-2 text-[13px]">
                  <span className={`flex-1 ${h.active ? "text-fg" : "text-muted line-through"}`}>{h.label}</span>
                  <Badge>{h.value_type}</Badge>
                  <Switch checked={h.private} onChange={(v) => upsert({ ...h, private: v, options: h.options })} label="Private" />
                  <Switch checked={h.active} onChange={(v) => upsert({ ...h, active: v, options: h.options })} label="Active" />
                  {!h.builtin && <Button size="sm" variant="ghost" title="Delete habit" onClick={async () => { await engine("journal.habit_delete", { key: h.key }); qc.invalidateQueries({ queryKey: ["journal.habits"] }); }}><Trash2 size={12} /></Button>}
                </li>))}</ul>
            </Card>
          );
        })}
      </Grid>
      <Modal open={open} onOpenChange={setOpen} title="New custom habit">
        <div className="space-y-3">
          <Field label="Name"><input className={inputCls} value={form.label} onChange={(e) => setForm({ ...form, label: e.target.value,
            key: form.key || "" })} maxLength={80} data-testid="habit-label" /></Field>
          <Field label="Key (lowercase, used in analyses)" hint="e.g. evening_walk"><input className={inputCls} value={form.key}
            onChange={(e) => setForm({ ...form, key: e.target.value.toLowerCase().replace(/[^a-z0-9_]/g, "_").slice(0, 40) })} data-testid="habit-key" /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Answer type"><select className={inputCls} value={form.value_type} onChange={(e) => setForm({ ...form, value_type: e.target.value })}>
              {["boolean", "number", "time", "scale", "category", "text"].map((t) => <option key={t}>{t}</option>)}</select></Field>
            <Field label="Category"><select className={inputCls} value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
              {CATS.map((t) => <option key={t}>{t}</option>)}</select></Field>
          </div>
          {form.value_type === "number" && <Field label="Unit"><input className={inputCls} value={form.unit} onChange={(e) => setForm({ ...form, unit: e.target.value })} maxLength={20} /></Field>}
          {form.value_type === "category" && <Field label="Choices (comma-separated)"><input className={inputCls} value={form.choices} onChange={(e) => setForm({ ...form, choices: e.target.value })} /></Field>}
          <Switch checked={form.private} onChange={(v) => setForm({ ...form, private: v })} label="Private (excluded from cloud AI)" />
          {!!err && <ErrorBox error={err} />}
          <div className="flex justify-end"><Button variant="primary" disabled={!form.key || !form.label} onClick={create} testId="habit-create">Create</Button></div>
        </div>
      </Modal>
    </>
  );
}
