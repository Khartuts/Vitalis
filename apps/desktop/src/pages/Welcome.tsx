import { useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Lock, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Button, ErrorBox, Field, inputCls, Spinner } from "../components/ui";
import { invoke } from "../lib/bridge";

export function Welcome({ data }: { data?: { profiles: any[]; last: string | null } }) {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [birthYear, setBirthYear] = useState("");
  const [tz, setTz] = useState("Europe/Zagreb");
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<unknown>(null);

  const create = async (demo: boolean) => {
    setErr(null);
    setBusy(demo ? "Generating 120 days of clearly-labelled synthetic data…" : "Creating encrypted profile…");
    try {
      await invoke("profiles:create", { name: demo ? "Demo (synthetic)" : name.trim(), demo, timezone: tz,
        birthYear: birthYear ? Number(birthYear) : null });
      await qc.invalidateQueries();
    } catch (e) { setErr(e); } finally { setBusy(null); }
  };
  const open = async (id: string) => {
    setBusy("Opening profile…");
    try { await invoke("profiles:open", { id }); await qc.invalidateQueries(); } catch (e) { setErr(e); } finally { setBusy(null); }
  };

  return (
    <div className="flex h-full items-center justify-center overflow-auto p-8">
      <div className="w-full max-w-3xl">
        <div className="mb-8">
          <div className="text-3xl font-semibold tracking-[0.2em]">VITALIS</div>
          <p className="mt-1 text-fg2">Personal AI Health Intelligence — local-first, encrypted, yours.</p>
        </div>
        {!!data?.profiles?.length && (
          <section className="mb-6 rounded-xl border border-line bg-s1 p-5">
            <h2 className="mb-3 text-sm font-semibold">Open a profile</h2>
            <div className="flex flex-wrap gap-2">
              {data.profiles.map((p) => (
                <Button key={p.id} onClick={() => open(p.id)} variant={p.id === data.last ? "primary" : "secondary"} testId={`open-profile-${p.demo ? "demo" : "real"}`}>
                  {p.demo && <FlaskConical size={14} />}{p.name}
                </Button>
              ))}
            </div>
          </section>
        )}
        <div className="grid gap-4 md:grid-cols-2">
          <section className="rounded-xl border border-line bg-s1 p-5">
            <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold"><Lock size={15} />Create your private health profile</h2>
            <p className="mb-4 text-xs text-fg2">Stored only on this PC in an encrypted database (SQLCipher). The key is protected by Windows.</p>
            <div className="space-y-3">
              <Field label="Profile name"><input className={inputCls} value={name} onChange={(e) => setName(e.target.value)} data-testid="profile-name" maxLength={60} /></Field>
              <Field label="Birth year (optional)" hint="Only used to estimate maximum heart rate for heart-rate zones.">
                <input className={inputCls} inputMode="numeric" value={birthYear} onChange={(e) => setBirthYear(e.target.value.replace(/\D/g, "").slice(0, 4))} /></Field>
              <Field label="Time zone"><input className={inputCls} value={tz} onChange={(e) => setTz(e.target.value)} /></Field>
              <Button variant="primary" disabled={!name.trim() || !!busy} onClick={() => create(false)} testId="create-profile">Create profile</Button>
            </div>
          </section>
          <section className="rounded-xl border border-warning/40 bg-s1 p-5">
            <h2 className="mb-1 flex items-center gap-2 text-sm font-semibold"><FlaskConical size={15} />Explore with demo data</h2>
            <p className="mb-4 text-xs text-fg2">A separate profile filled with <b>synthetic</b> data (120 days, with built-in habit effects and
              missing-data examples). It is labelled as synthetic everywhere and never mixed with real data.</p>
            <Button onClick={() => create(true)} disabled={!!busy} testId="create-demo">Create demo profile</Button>
          </section>
        </div>
        <div className="mt-6 flex items-start gap-2 text-xs text-fg2"><ShieldCheck size={14} className="mt-0.5 text-good" />
          No account, no telemetry, no cloud by default. AI providers and Google Health are optional and ask for consent before any data leaves this PC.</div>
        {busy && <div className="mt-4"><Spinner label={busy} /></div>}
        {!!err && <div className="mt-4"><ErrorBox error={err} /></div>}
      </div>
    </div>
  );
}
