import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, CheckCircle2, ExternalLink, KeyRound, QrCode, ShieldCheck, Smartphone, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import { useAiStatus } from "../components/AiPanel";
import { Badge, Button, Card, Empty, ErrorBox, Field, Grid, inputCls, Modal, Note, PageHeader, Skeleton, Spinner, Stat, Switch, TabPanel, Tabs } from "../components/ui";
import { engine, invoke, openExternal } from "../lib/bridge";
import { fmtDate } from "../lib/format";
import { useEngine, useGlobalRange, useStatus } from "../lib/hooks";
import { addDays, useUi } from "../lib/store";

export default function Settings() {
  const [tab, setTab] = useState("profile");
  return (
    <div data-testid="settings-page">
      <PageHeader title="Data Sources & Settings" subtitle="Everything is stored locally and encrypted. Connections are optional and can be revoked at any time." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "profile", label: "Profile" }, { id: "phone", label: "Phone sync" }, { id: "import", label: "Imports" },
        { id: "google", label: "Google Health" }, { id: "ai", label: "AI providers" }, { id: "sources", label: "Sources & quality" }, { id: "privacy", label: "Privacy & data" }]}>
        <TabPanel value="profile"><ProfileTab /></TabPanel>
        <TabPanel value="phone"><PhoneTab /></TabPanel>
        <TabPanel value="import"><ImportTab /></TabPanel>
        <TabPanel value="google"><GoogleTab /></TabPanel>
        <TabPanel value="ai"><AiTab /></TabPanel>
        <TabPanel value="sources"><SourcesTab /></TabPanel>
        <TabPanel value="privacy"><PrivacyTab /></TabPanel>
      </Tabs>
    </div>
  );
}

function useRun() {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<unknown>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const run = async (f: () => Promise<any>, ok?: string) => {
    setBusy(true); setErr(null); setMsg(null);
    try { const r = await f(); if (ok) setMsg(ok); return r; } catch (e) { setErr(e); } finally { setBusy(false); }
  };
  const view = <>{busy && <Spinner label="Working…" />}{msg && <div className="text-xs text-good" role="status">{msg}</div>}{!!err && <ErrorBox error={err} />}</>;
  return { run, busy, view };
}

function ProfileTab() {
  const qc = useQueryClient();
  const prefs = useEngine<any>("prefs.get");
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: () => invoke<any>("profiles:list") });
  const { locale, setLocale, theme, setTheme } = useUi();
  const [edit, setEdit] = useState<Record<string, any>>({});
  const { run, view } = useRun();
  if (!prefs.data) return <Skeleton h={300} />;
  const v = (k: string) => (k in edit ? edit[k] : prefs.data[k] ?? "");
  const save = () => run(async () => {
    const values: Record<string, any> = {};
    for (const [k, x] of Object.entries(edit)) values[k] = x === "" ? null : ["sleep_need_min", "birth_year", "hr_max", "baseline_window_days", "baseline_min_provisional", "baseline_min_stable"].includes(k) ? Number(x) : x;
    await engine("prefs.set", { values });
    setEdit({});
    qc.invalidateQueries();
  }, "Saved — analytics are recomputed in the background.");
  return (
    <Grid cols={2}>
      <Card title="Personal settings">
        <div className="grid grid-cols-2 gap-3">
          <Field label="Profile name"><input className={inputCls} value={v("profile_name")} onChange={(e) => setEdit({ ...edit, profile_name: e.target.value })} /></Field>
          <Field label="Display time zone" hint="Canonical timestamps stay in UTC"><input className={inputCls} value={v("display_timezone")} onChange={(e) => setEdit({ ...edit, display_timezone: e.target.value })} /></Field>
          <Field label="Sleep need (minutes)" hint="Used for sleep debt, recovery and the planner"><input className={inputCls} inputMode="numeric" value={v("sleep_need_min")} onChange={(e) => setEdit({ ...edit, sleep_need_min: e.target.value })} data-testid="pref-sleep-need" /></Field>
          <Field label="Birth year" hint="Max HR ≈ 208 − 0.7 × age (Tanaka)"><input className={inputCls} inputMode="numeric" value={v("birth_year")} onChange={(e) => setEdit({ ...edit, birth_year: e.target.value })} /></Field>
          <Field label="Max heart rate (overrides estimate)"><input className={inputCls} inputMode="numeric" value={v("hr_max")} onChange={(e) => setEdit({ ...edit, hr_max: e.target.value })} /></Field>
          <Field label="Language"><select className={inputCls} value={locale} onChange={(e) => setLocale(e.target.value as any)}><option value="en">English</option><option value="hr">Hrvatski (partial)</option></select></Field>
          <Field label="Theme"><select className={inputCls} value={theme} onChange={(e) => setTheme(e.target.value as any)}><option value="dark">Dark</option><option value="light">Light</option><option value="system">System</option></select></Field>
        </div>
        <div className="mt-4 grid grid-cols-3 gap-3">
          <Field label="Baseline window (days)"><input className={inputCls} value={v("baseline_window_days")} onChange={(e) => setEdit({ ...edit, baseline_window_days: e.target.value })} /></Field>
          <Field label="Provisional after (valid days)"><input className={inputCls} value={v("baseline_min_provisional")} onChange={(e) => setEdit({ ...edit, baseline_min_provisional: e.target.value })} /></Field>
          <Field label="Stable after (valid days)"><input className={inputCls} value={v("baseline_min_stable")} onChange={(e) => setEdit({ ...edit, baseline_min_stable: e.target.value })} /></Field>
        </div>
        <div className="mt-4 flex items-center gap-3"><Button variant="primary" onClick={save} disabled={!Object.keys(edit).length} testId="prefs-save">Save</Button>{view}</div>
      </Card>
      <Card title="Profiles" subtitle="Each profile is a separate encrypted database">
        <ul className="space-y-2">{(profiles.data?.profiles ?? []).map((p: any) => (
          <li key={p.id} className="flex items-center gap-2 text-[13px]"><span className="flex-1">{p.name}</span>{p.demo && <Badge tone="warning">synthetic</Badge>}
            {profiles.data.active?.id === p.id ? <Badge tone="good">open</Badge> : <Button size="sm" onClick={() => run(async () => { await invoke("profiles:open", { id: p.id }); qc.invalidateQueries(); })}>Open</Button>}</li>))}</ul>
        <NewProfile />
      </Card>
    </Grid>
  );
}

function NewProfile() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const { run, view } = useRun();
  return (
    <div className="mt-4 border-t border-line pt-3">
      <div className="flex gap-2"><input className={inputCls} placeholder="New profile name" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
        <Button disabled={!name.trim()} onClick={() => run(async () => { await invoke("profiles:create", { name: name.trim(), demo: false, timezone: "Europe/Zagreb" }); setName(""); qc.invalidateQueries(); })}>Create</Button>
        <Button onClick={() => run(async () => { await invoke("profiles:create", { name: "Demo (synthetic)", demo: true, timezone: "Europe/Zagreb" }); qc.invalidateQueries(); })}>New demo</Button></div>
      {view}
    </div>
  );
}

function PhoneTab() {
  const qc = useQueryClient();
  const st = useStatus();
  const sync = useEngine<any>("sync.status");
  const [pair, setPair] = useState<any>(null);
  const { run, view } = useRun();
  const running = sync.data?.running;
  return (
    <Grid cols={2}>
      <Card title="Secure phone sync (local network)" subtitle="HTTPS with a certificate your phone pins from the QR code; each phone gets its own revocable token">
        <div className="mb-3 flex items-center gap-3">
          <Badge tone={running ? "good" : "neutral"}>{running ? `Listening on ${sync.data.hosts.join(", ")}:${sync.data.port}` : "Sync off"}</Badge>
          {running ? <Button onClick={() => run(async () => { await invoke("sync:stop"); setPair(null); qc.invalidateQueries(); })}>Stop</Button>
            : <Button variant="primary" testId="sync-start" onClick={() => run(async () => { await invoke("sync:start", {}); qc.invalidateQueries(); }, "Sync server started. Windows may ask to allow VITALIS on private networks.")}>Enable phone sync</Button>}
          {running && <Button onClick={() => run(async () => setPair(await invoke("sync:pairing")))} testId="sync-pair"><QrCode size={13} />Pair a phone</Button>}
        </div>
        {view}
        {pair && (
          <div className="mt-3 flex gap-4 rounded-lg border border-line bg-white p-3 text-black" data-testid="pairing-qr">
            <img src={pair.qrDataUrl} alt="Pairing QR code" width={220} height={220} />
            <div className="text-xs">
              <div className="font-semibold">Scan with the VITALIS companion</div>
              <div className="mt-1">Or enter code <b className="font-mono text-base tracking-widest">{pair.code}</b></div>
              <div className="mt-1">Expires {new Date(pair.expires_at).toLocaleTimeString()}</div>
              <div className="mt-2 break-all text-[10px] text-gray-600">Certificate SHA-256: {pair.fingerprint}</div>
              <div className="mt-2 text-[10px] text-gray-600">Keep this QR private: it also contains the key for encrypted file transfer.</div>
            </div>
          </div>
        )}
        <Note>Last data import: {st.data?.counts?.last_import ? new Date(String(st.data.counts.last_import)).toLocaleString() : "never"}.</Note>
      </Card>
      <Card title="Paired phones">
        {!sync.data?.devices?.length ? <Empty title="No phones paired yet" icon={<Smartphone size={18} className="text-muted" />} /> : (
          <ul className="space-y-2">{sync.data.devices.map((d: any) => (
            <li key={d.id} className="flex items-center gap-2 text-[13px]"><Smartphone size={14} /><span className="flex-1">{d.name}</span>
              <span className="text-[11px] text-muted">last seen {d.last_seen ? fmtDate(d.last_seen) : "never"}</span>
              {d.revoked_at ? <Badge>revoked</Badge> : <Button size="sm" variant="danger" onClick={() => run(async () => { await engine("sync.revoke", { id: d.id }); qc.invalidateQueries({ queryKey: ["sync.status"] }); })}>Revoke</Button>}</li>))}</ul>
        )}
        <div className="mt-4 border-t border-line pt-3">
          <div className="text-xs font-semibold">No network connection between devices?</div>
          <p className="mt-1 text-xs text-fg2">In the companion choose "Export encrypted file", copy the <code>.vitalis-sync</code> file to this PC (USB, cloud drive) and import it under Imports. Files are AES-256-GCM encrypted with your pairing key.</p>
          <Button size="sm" variant="ghost" onClick={() => run(async () => { await engine("sync.rotate_file_key"); }, "File key rotated — re-pair phones before exporting files.")}>Rotate file key</Button>
        </div>
      </Card>
      <Card title="Setting up the Android companion">
        <ol className="list-decimal space-y-1.5 pl-5 text-xs text-fg2">
          <li>Install the VITALIS companion APK on the phone paired with your Galaxy Watch6.</li>
          <li>In Samsung Health → Settings → Health Connect, allow Samsung Health to <b>write</b> data to Health Connect.</li>
          <li>Open the companion, scan the QR above, then grant the Health Connect read permissions you want (each one is optional), including "Access past data".</li>
          <li>Optional, for Samsung-only data (sleep score, Energy Score, skin temperature): enable Samsung Health developer mode for personal testing (see INSTALLATION.md).</li>
          <li>Run "Sync now". New records then sync incrementally.</li>
        </ol>
        <div className="mt-2 flex gap-2"><Button size="sm" variant="ghost" onClick={() => openExternal("https://developer.android.com/health-and-fitness/health-connect")}>Health Connect<ExternalLink size={11} /></Button>
          <Button size="sm" variant="ghost" onClick={() => openExternal("https://developer.samsung.com/health/data")}>Samsung Health Data SDK<ExternalLink size={11} /></Button></div>
      </Card>
    </Grid>
  );
}

function ImportTab() {
  const qc = useQueryClient();
  const hist = useEngine<any[]>("import.history");
  const [pv, setPv] = useState<any>(null);
  const [result, setResult] = useState<any>(null);
  const { run, view, busy } = useRun();
  const pick = (kind: string) => run(async () => { setResult(null); const r = await invoke("import:pick", { kind }); if (r) setPv({ ...r, kind }); });
  const commit = () => run(async () => { const r = await invoke("import:commit", { token: pv.token }); setResult(r); setPv(null); qc.invalidateQueries(); });
  return (
    <Grid cols={2}>
      <Card title="Import files" subtitle="Nothing is imported until you review the preview">
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => pick("samsung_export")} testId="import-samsung"><Upload size={13} />Samsung Health export (ZIP/CSV)</Button>
          <Button onClick={() => pick("vitalis_sync_file")} testId="import-syncfile"><Upload size={13} />Encrypted phone file (.vitalis-sync)</Button>
        </div>
        <div className="mt-3">{view}</div>
        {result && (
          <div className="mt-3 rounded-lg bg-s2 p-3 text-xs" data-testid="import-result"><CheckCircle2 size={13} className="mr-1 inline text-good" />
            Received {result.received}, inserted {result.inserted}, updated {result.updated}, duplicates {result.duplicate}, rejected {result.rejected}.
            {Object.keys(result.unsupported_types ?? {}).length > 0 && <div className="mt-1 text-fg2">Unsupported record types (kept raw): {Object.entries(result.unsupported_types).map(([k, n]) => `${k} ×${n}`).join(", ")}</div>}
            {(result.errors ?? []).length > 0 && <div className="mt-1 text-critical">{result.errors.slice(0, 5).join("; ")}</div>}
          </div>
        )}
        <Note>Samsung Health: Settings → Download personal data. Export schemas change between app versions; VITALIS matches tables by name and columns by suffix (adapter samsung_export.v1) and shows what it recognized.</Note>
      </Card>
      <Card title="Import history">
        {!hist.data?.length ? <Empty title="No imports yet" /> : (
          <div className="max-h-96 overflow-auto"><table className="w-full text-xs"><thead className="sticky top-0 bg-s1 text-muted"><tr>{["When", "Source", "Method", "Status", "In", "New", "Dup"].map((h) => <th key={h} className="py-1 text-left font-normal">{h}</th>)}</tr></thead>
            <tbody>{hist.data.map((b) => (<tr key={b.id} className="border-t border-line"><td className="py-1">{new Date(b.started_at).toLocaleString()}</td><td>{b.provider}</td><td>{b.import_method}</td>
              <td><Badge tone={b.status === "completed" ? "good" : "warning"}>{b.status}</Badge></td><td className="num">{b.records_received}</td><td className="num">{b.records_inserted}</td><td className="num">{b.records_duplicate}</td></tr>))}</tbody></table></div>
        )}
      </Card>
      <Modal open={!!pv} onOpenChange={(o) => !o && setPv(null)} wide title={`Preview: ${pv?.fileName ?? ""}`}>
        {pv?.kind === "samsung_export" ? (
          <div className="text-xs" data-testid="import-preview">
            <p className="mb-2 text-fg2">Adapter {pv.preview.adapter_version}. Assumptions: {pv.preview.assumptions.join(" ")}</p>
            <table className="w-full"><thead className="text-muted"><tr><th className="text-left font-normal">File</th><th className="text-left font-normal">Recognized as</th><th className="text-right font-normal">Rows</th><th className="text-left font-normal">Period</th><th className="text-left font-normal">Mapped columns</th></tr></thead>
              <tbody>{pv.preview.tables.map((t: any) => <tr key={t.file} className="border-t border-line"><td className="py-1">{t.file}</td><td>{t.description}</td><td className="num text-right">{t.rows}</td>
                <td>{t.first?.slice(0, 10)} → {t.last?.slice(0, 10)}</td><td className="text-fg2">{t.mapped_columns.join(", ")}{t.stage_codes ? ` · stage codes ${JSON.stringify(t.stage_codes)}` : ""}</td></tr>)}</tbody></table>
            {pv.preview.unmatched_files.length > 0 && <p className="mt-2 text-fg2">Not recognized (ignored): {pv.preview.unmatched_files.map((f: any) => f.file).join(", ")}</p>}
          </div>
        ) : pv && (
          <div className="text-xs" data-testid="import-preview"><p>Batch {pv.preview.batch_id} from {pv.preview.source} · {pv.preview.records} records{pv.preview.already_imported && <b> — already imported</b>}</p>
            <p className="mt-1 text-fg2">{Object.entries(pv.preview.types).map(([k, n]) => `${k} ×${n}`).join(", ")}</p></div>
        )}
        <div className="mt-4 flex justify-end gap-2"><Button onClick={() => setPv(null)}>Cancel</Button><Button variant="primary" onClick={commit} disabled={busy} testId="import-commit">Import</Button></div>
      </Modal>
    </Grid>
  );
}

function GoogleTab() {
  const qc = useQueryClient();
  const g = useQuery({ queryKey: ["google:status"], queryFn: () => invoke<any>("google:status") });
  const [cid, setCid] = useState("");
  const [secret, setSecret] = useState("");
  const [days, setDays] = useState("30");
  const { run, view } = useRun();
  const refresh = () => qc.invalidateQueries({ queryKey: ["google:status"] });
  return (
    <Grid cols={2}>
      <Card title="Setup wizard" subtitle="Google Health API v4 · read-only scopes · your own Google Cloud project">
        <ol className="list-decimal space-y-1.5 pl-5 text-xs text-fg2">
          <li>Install the <b>Google Health</b> mobile app and sign in with the Google Account you want to connect (links your Google Health profile; otherwise the API returns ACCOUNT_NOT_LINKED).</li>
          <li>In Google Cloud Console create a project (or reuse one) and enable the <b>Google Health API</b>.</li>
          <li>OAuth consent screen: user type External, publishing status Testing, add your Google Account under <b>Test users</b>.</li>
          <li>Data Access: add the read-only scopes <code>googlehealth.activity_and_fitness.readonly</code>, <code>googlehealth.health_metrics_and_measurements.readonly</code>, <code>googlehealth.sleep.readonly</code>.</li>
          <li>Credentials → Create OAuth client ID → Application type <b>Desktop app</b>. Paste the client ID (and secret) below.</li>
          <li>Note: in Testing mode Google refresh tokens expire after 7 days — VITALIS will ask you to reconnect. More than 100 users would require Google's verification (not needed for personal use).</li>
        </ol>
        <div className="mt-2"><Button size="sm" variant="ghost" onClick={() => openExternal("https://console.cloud.google.com/")}>Open Google Cloud Console<ExternalLink size={11} /></Button>
          <Button size="sm" variant="ghost" onClick={() => openExternal("https://developers.google.com/health")}>Google Health API docs<ExternalLink size={11} /></Button></div>
        <div className="mt-3 grid grid-cols-1 gap-2">
          <Field label="OAuth client ID"><input className={inputCls} value={cid} onChange={(e) => setCid(e.target.value)} placeholder="….apps.googleusercontent.com" /></Field>
          <Field label="Client secret (Desktop clients: not confidential, stored encrypted)"><input className={inputCls} type="password" value={secret} onChange={(e) => setSecret(e.target.value)} /></Field>
          <div><Button disabled={!cid} onClick={() => run(async () => { await invoke("google:config", { clientId: cid.trim(), clientSecret: secret.trim() }); setCid(""); setSecret(""); refresh(); }, "Saved.")}>Save client</Button></div>
        </div>
      </Card>
      <Card title="Connection">
        {!g.data ? <Skeleton h={120} /> : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2"><Badge tone={g.data.configured ? "good" : "neutral"}>{g.data.configured ? "client configured" : "no client"}</Badge>
              <Badge tone={g.data.connected ? "good" : "neutral"}>{g.data.connected ? "connected" : "not connected"}</Badge></div>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" disabled={!g.data.configured} onClick={() => run(async () => { await invoke("google:connect"); refresh(); }, "Connected. Opened Google sign-in in your browser.")}>Connect (opens browser)</Button>
              <input className={inputCls + " w-20"} value={days} onChange={(e) => setDays(e.target.value.replace(/\D/g, ""))} aria-label="Initial days to sync" />
              <Button disabled={!g.data.connected} onClick={() => run(async () => { const r = await invoke("google:sync", { days: Math.min(365, Math.max(1, Number(days) || 30)) }); refresh(); return r; }, "Sync finished.")}>Sync now</Button>
              <Button variant="danger" disabled={!g.data.connected} onClick={() => run(async () => { await invoke("google:disconnect"); refresh(); }, "Disconnected and token revoked.")}>Disconnect</Button>
            </div>
            <div className="mt-3">{view}</div>
            {g.data.lastSync && <div className="mt-3 text-xs text-fg2">Last sync {new Date(g.data.lastSync.at).toLocaleString()}.
              {Object.keys(g.data.lastSync.errors ?? {}).length > 0 && <div className="text-critical">Errors: {Object.entries(g.data.lastSync.errors).map(([k, e]) => `${k}: ${e}`).join("; ")}</div>}</div>}
            <Note>Overlap with Samsung data arriving through Health Connect is handled by source priority and duplicate detection (Sources & quality). VITALIS works fully without Google.</Note>
          </>
        )}
      </Card>
    </Grid>
  );
}

function AiTab() {
  const qc = useQueryClient();
  const s = useAiStatus();
  const [key, setKey] = useState("");
  const [local, setLocal] = useState<string | null>(null);
  const { run, view } = useRun();
  const refresh = () => { qc.invalidateQueries({ queryKey: ["ai:status"] }); qc.invalidateQueries({ queryKey: ["ai:models"] }); };
  if (!s.data) return <Skeleton h={300} />;
  const cg = s.data.chatgpt;
  return (
    <Grid cols={2}>
      <Card title="ChatGPT plan (Sign in with ChatGPT)" subtitle="Use your ChatGPT Plus/Pro plan for the analyst — no API key needed">
        <p className="text-xs text-fg2">VITALIS uses OpenAI's documented flow for open-source, locally run apps: your browser opens OpenAI's sign-in, and you
          approve VITALIS to make eligible Responses API requests on your plan. This does <b>not</b> give VITALIS access to your ChatGPT conversations,
          memories, files or ChatGPT Health records. Tokens are stored encrypted by Windows and never shown to the app's interface.</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {cg.connected ? <>
            <Badge tone="good">Connected{cg.email ? ` · ${cg.email}` : ""}</Badge>{!cg.sharing && <Badge tone="warning">plan usage not enabled</Badge>}
            {!cg.sharing && <Button onClick={() => run(async () => { await invoke("ai:siwc:signIn", { reconsent: true }); refresh(); })}>Enable plan usage</Button>}
            <Button variant="danger" onClick={() => run(async () => { const r = await invoke("ai:siwc:signOut"); refresh(); return r; }, "Signed out.")}>Sign out & revoke</Button>
          </> : (
            <button onClick={() => run(async () => { await invoke("ai:siwc:signIn", {}); refresh(); }, "Signed in.")} data-testid="siwc-button"
              className="inline-flex items-center gap-2 rounded-lg bg-black px-4 py-2 text-[13px] font-medium text-white ring-1 ring-white/20 hover:bg-neutral-900">
              <span className="inline-block h-4 w-4 rounded-full bg-white" aria-hidden />Continue with ChatGPT</button>
          )}
          <Button size="sm" variant="ghost" onClick={() => openExternal(cg.usageUrl)}>Manage usage<ExternalLink size={11} /></Button>
          {!cg.connected && <Button size="sm" variant="ghost" onClick={() => invoke("ai:siwc:cancel")}>Cancel pending sign-in</Button>}
        </div>
        <Note>ChatGPT Plus has a five-hour usage limit shared across all apps using your plan. To disconnect fully, you can also remove VITALIS in ChatGPT Settings.</Note>
      </Card>
      <Card title="OpenAI API key" subtitle="Billed to your OpenAI API account (separate from a ChatGPT subscription)">
        <div className="flex gap-2"><input type="password" className={inputCls} placeholder={s.data.openai_api.configured ? "•••••••• (stored encrypted)" : "sk-…"} value={key} onChange={(e) => setKey(e.target.value)} autoComplete="off" />
          <Button variant="primary" disabled={!key.startsWith("sk-")} onClick={() => run(async () => { await invoke("ai:apikey:set", { apiKey: key.trim() }); setKey(""); refresh(); }, "Key saved (encrypted).")}><KeyRound size={13} />Save</Button>
          {s.data.openai_api.configured && <Button variant="danger" onClick={() => run(async () => { await invoke("ai:apikey:clear"); refresh(); }, "Key removed.")}>Remove</Button>}</div>
        <Button size="sm" variant="ghost" onClick={() => openExternal("https://platform.openai.com/api-keys")}>Create an API key<ExternalLink size={11} /></Button>
      </Card>
      <Card title="Local model (optional, on-device)" subtitle="Any OpenAI-compatible server on this PC: Ollama, LM Studio, llama.cpp">
        <Field label="Base URL (loopback only)"><input className={inputCls} value={local ?? s.data.local.baseUrl} onChange={(e) => setLocal(e.target.value)} /></Field>
        <div className="mt-2 flex items-center gap-3"><Switch checked={!!s.data.local.enabled} label="Enabled"
          onChange={(v) => run(async () => { await invoke("ai:local:set", { baseUrl: local ?? s.data.local.baseUrl, enabled: v }); refresh(); })} />
          <Button size="sm" onClick={() => run(async () => { await invoke("ai:local:set", { baseUrl: local ?? s.data.local.baseUrl, enabled: !!s.data.local.enabled }); refresh(); }, "Saved.")}>Save URL</Button></div>
        <Note>With a local model nothing leaves your PC. Tool use requires a model that supports function calling (e.g. recent Qwen or Llama instruct models). Answer quality varies by model.</Note>
      </Card>
      <Card title="How the analyst works">
        <ul className="list-disc space-y-1 pl-5 text-xs text-fg2">
          <li>The model never receives your database. It asks for specific summaries through read-only tools (validated arguments, max 400 days, no SQL/code).</li>
          <li>Before each cloud request you see the exact JSON that will be sent and can cancel. Approvals are logged (hash only) under Privacy.</li>
          <li>Private journal habits are removed from tool definitions and results for cloud providers.</li>
          <li>Answers cite evidence chips you can click to see the underlying data.</li>
        </ul>
        <div className="mt-2">{view}</div>
      </Card>
    </Grid>
  );
}

function SourcesTab() {
  const qc = useQueryClient();
  const range = useGlobalRange();
  const prefs = useEngine<any>("prefs.get");
  const avail = useEngine<any[]>("sources.availability");
  const q = useEngine<any>("quality.report", { start: addDays(range.end, -29), end: range.end }, { enabled: !!range.status });
  const prio: string[] = prefs.data?.source_priority ?? [];
  const move = async (i: number, d: number) => {
    const p = [...prio]; const j = i + d; if (j < 0 || j >= p.length) return;
    [p[i], p[j]] = [p[j], p[i]];
    await engine("prefs.set", { values: { source_priority: p } });
    qc.invalidateQueries();
  };
  return (
    <Grid cols={2}>
      <Card title="Source priority" subtitle="When two sources record the same measurement, the higher one wins (the other is kept, marked superseded)">
        <ol className="space-y-1">{prio.map((p, i) => (
          <li key={p} className="flex items-center gap-2 text-[13px]"><span className="w-5 text-muted">{i + 1}.</span><span className="flex-1">{p}</span>
            <Button size="sm" variant="ghost" onClick={() => move(i, -1)} title="Up"><ArrowUp size={12} /></Button><Button size="sm" variant="ghost" onClick={() => move(i, 1)} title="Down"><ArrowDown size={12} /></Button></li>))}</ol>
      </Card>
      <Card title="What your phone shares" subtitle="Availability probe reported by the companion (verified on your device)">
        {!avail.data?.length ? <Empty title="No report yet">Run "Check what my phone shares" in the companion after pairing. Until then, metrics marked 🔍 in DATA_ACCESS_MATRIX.md are unverified for your device.</Empty> : (
          <div className="max-h-80 overflow-auto text-xs">
            <div className="mb-1 text-fg2">Received {new Date(avail.data[0].received_at).toLocaleString()} ({avail.data[0].provider})</div>
            <table className="w-full"><thead className="text-muted"><tr><th className="text-left font-normal">Type</th><th className="text-left font-normal">Status</th><th className="text-right font-normal">Records (30 d)</th><th className="text-left font-normal">Origins</th></tr></thead>
              <tbody>{(avail.data[0].report.types ?? []).map((t: any) => (
                <tr key={t.type} className="border-t border-line"><td className="py-1">{t.type}</td><td><Badge tone={t.count > 0 ? "good" : t.permission === "granted" ? "neutral" : "warning"}>{t.count > 0 ? "has data" : t.permission}</Badge></td>
                  <td className="num text-right">{t.count}</td><td className="text-fg2">{(t.origins ?? []).join(", ")}</td></tr>))}</tbody></table>
          </div>
        )}
      </Card>
      <Card title="Data quality (last 30 days)" testId="quality-card">
        {!q.data ? <Skeleton h={200} /> : (
          <>
            <div className="grid grid-cols-4 gap-3">
              <Stat label="Completeness" value={`${Math.round(q.data.coverage.completeness_score * 100)}%`} />
              <Stat label="Outliers flagged" value={q.data.flags?.outliers ?? 0} />
              <Stat label="Out of range" value={q.data.flags?.out_of_range ?? 0} />
              <Stat label="Duplicates superseded" value={q.data.flags?.duplicates_superseded ?? 0} />
            </div>
            <table className="mt-3 w-full text-xs"><tbody>{Object.entries(q.data.coverage.per_metric).map(([k, v]: any) => (
              <tr key={k} className="border-t border-line"><td className="py-1 text-fg2">{k}</td><td className="num text-right">{v.days_present}/{v.days_total}</td>
                <td className="w-40"><div className="h-1.5 rounded bg-s3"><div className="h-1.5 rounded bg-accent" style={{ width: `${v.fraction * 100}%` }} /></div></td></tr>))}</tbody></table>
            {Object.keys(q.data.sensor_gaps_last_14d).length > 0 && <div className="mt-3 text-xs"><b>Heart-rate gaps ≥ 3 h (last 14 days):</b>
              <ul className="ml-4 list-disc text-fg2">{Object.entries(q.data.sensor_gaps_last_14d).map(([d, g]: any) => <li key={d}>{d}: {g.map((x: any) => `${x.hours} h`).join(", ")}</li>)}</ul></div>}
          </>
        )}
      </Card>
      <Card title="Freshness & reliability">
        {!q.data ? <Skeleton h={200} /> : (
          <div className="max-h-80 overflow-auto"><table className="w-full text-xs"><thead className="sticky top-0 bg-s1 text-muted"><tr><th className="text-left font-normal">Source</th><th className="text-left font-normal">Metric</th><th className="text-right font-normal">Newest</th><th className="text-right font-normal">Records</th></tr></thead>
            <tbody>{q.data.freshness.map((f: any, i: number) => (
              <tr key={i} className="border-t border-line"><td className="py-1">{f.provider}</td><td>{f.metric}</td><td className={`num text-right ${f.stale ? "text-warning" : ""}`}>{f.age_hours < 48 ? `${Math.round(f.age_hours)} h ago` : `${Math.round(f.age_hours / 24)} d ago`}</td>
                <td className="num text-right">{f.n.toLocaleString()}</td></tr>))}</tbody></table></div>
        )}
      </Card>
    </Grid>
  );
}

function PrivacyTab() {
  const qc = useQueryClient();
  const disc = useEngine<any[]>("ai.disclosures");
  const priv = useEngine<string[]>("ai.private_habits");
  const info = useQuery({ queryKey: ["app:info"], queryFn: () => invoke<any>("app:info") });
  const [pass, setPass] = useState("");
  const [restoreName, setRestoreName] = useState("Restored profile");
  const [confirm, setConfirm] = useState<null | "wipe" | "profile">(null);
  const [typed, setTyped] = useState("");
  const { run, view } = useRun();
  return (
    <Grid cols={2}>
      <Card title="Data flow" subtitle="Where your data goes">
        <ul className="space-y-1.5 text-xs text-fg2">
          <li className="flex gap-2"><ShieldCheck size={14} className="shrink-0 text-good" />Health data: encrypted SQLCipher database on this PC (key protected by {info.data?.keystore ?? "the OS"}).</li>
          <li className="flex gap-2"><ShieldCheck size={14} className="shrink-0 text-good" />Phone → PC: TLS on your local network with a pinned certificate, or an AES-256-GCM encrypted file.</li>
          <li className="flex gap-2"><ShieldCheck size={14} className="shrink-0 text-good" />Google Health: read-only download into this PC; nothing is uploaded to Google.</li>
          <li className="flex gap-2"><ShieldCheck size={14} className="shrink-0 text-warning" />Cloud AI (optional): only the previewed request body for each question; never automatically.</li>
          <li className="flex gap-2"><ShieldCheck size={14} className="shrink-0 text-good" />No telemetry, analytics, crash reporting or advertising SDKs.</li>
        </ul>
      </Card>
      <Card title="Cloud AI disclosure log" subtitle="Each approved request: when, where, which data categories, size and SHA-256 of the exact payload">
        {!disc.data?.length ? <Empty title="Nothing has been sent to a cloud AI provider" /> : (
          <div className="max-h-64 overflow-auto"><table className="w-full text-xs"><tbody>{disc.data.map((d) => (
            <tr key={d.id} className="border-t border-line"><td className="py-1">{new Date(d.approved_at).toLocaleString()}</td><td>{d.provider}</td><td>{d.model}</td>
              <td className="num">{(d.payload_bytes / 1024).toFixed(1)} KB</td><td className="font-mono text-[10px] text-muted">{d.payload_sha256.slice(0, 12)}…</td></tr>))}</tbody></table></div>
        )}
        <div className="mt-2 text-xs text-fg2">Private habits (never sent): {priv.data?.length ? priv.data.join(", ") : "none"} — change in Habit Journal → Manage habits.</div>
        <div className="mt-2"><Button size="sm" variant="danger" onClick={() => run(async () => { await engine("chat.delete_all"); qc.invalidateQueries(); }, "AI conversation history deleted.")}>Delete all AI conversations</Button></div>
      </Card>
      <Card title="Backup & restore" subtitle="Encrypted backup protected by a passphrase you choose (scrypt + SQLCipher)">
        <Field label="Backup passphrase (min. 10 characters — keep it safe, it cannot be recovered)"><input type="password" className={inputCls} value={pass} onChange={(e) => setPass(e.target.value)} autoComplete="new-password" /></Field>
        <div className="mt-2 flex flex-wrap gap-2">
          <Button disabled={pass.length < 10} onClick={() => run(async () => { const r = await invoke("backup:create", { passphrase: pass }); return r; }, "Backup written.")}>Create backup</Button>
          <input className={inputCls + " w-44"} value={restoreName} onChange={(e) => setRestoreName(e.target.value)} aria-label="Name for restored profile" />
          <Button disabled={pass.length < 10} onClick={() => run(async () => { await invoke("backup:restore", { passphrase: pass, name: restoreName }); qc.invalidateQueries(); }, "Backup restored as a new profile.")}>Restore as new profile</Button>
        </div>
        <div className="mt-2">{view}</div>
      </Card>
      <Card title="Export & delete">
        <div className="flex flex-wrap gap-2">
          <Button onClick={() => run(() => invoke("export:all"), "All data exported (CSV + manifest).")}>Export all data</Button>
          <Button variant="danger" onClick={() => { setTyped(""); setConfirm("wipe"); }} testId="wipe-data"><Trash2 size={13} />Delete all data in this profile</Button>
          <Button variant="danger" onClick={() => { setTyped(""); setConfirm("profile"); }}><Trash2 size={13} />Delete this profile & its key</Button>
        </div>
        <Note>Disconnecting cloud services: Settings → AI providers (sign out / remove key) and Google Health (disconnect revokes the token).</Note>
      </Card>
      <Modal open={!!confirm} onOpenChange={(o) => !o && setConfirm(null)} title={confirm === "wipe" ? "Delete all data in this profile?" : "Delete this profile permanently?"}
        description="This cannot be undone. Make a backup first if you may need the data.">
        <Field label={`Type ${confirm === "wipe" ? "DELETE ALL MY DATA" : "DELETE"} to confirm`}><input className={inputCls} value={typed} onChange={(e) => setTyped(e.target.value)} data-testid="confirm-input" /></Field>
        <div className="mt-3 flex justify-end gap-2"><Button onClick={() => setConfirm(null)}>Cancel</Button>
          <Button variant="danger" disabled={typed !== (confirm === "wipe" ? "DELETE ALL MY DATA" : "DELETE")} testId="confirm-delete"
            onClick={() => run(async () => {
              if (confirm === "wipe") await invokeWipe();
              else { const p = await invoke<any>("profiles:list"); await invoke("profiles:delete", { id: p.active.id, confirm: "DELETE" }); }
              setConfirm(null); qc.invalidateQueries();
            }, "Deleted.")}>Delete</Button></div>
      </Modal>
    </Grid>
  );
}

// data.wipe requires the exact confirmation phrase, validated again in main (Zod literal) and in the engine (Pydantic).
async function invokeWipe() {
  await invoke("data:wipe", { confirm: "DELETE ALL MY DATA" });
}
