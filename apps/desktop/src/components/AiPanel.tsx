import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ExternalLink, Plus, Send, ShieldAlert, Square, Wrench, X } from "lucide-react";
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { AiEvent, AiProvider, Evidence } from "@shared/contract";
import { navigate } from "../App";
import { engine, invoke, onAiEvent, openExternal } from "../lib/bridge";
import { useUi } from "../lib/store";
import { Badge, Button, cx, inputCls, Modal, Select, Spinner } from "./ui";

const PROVIDER_LABEL: Record<AiProvider, string> = { chatgpt: "ChatGPT plan", openai_api: "OpenAI API key", local: "Local model" };
const USAGE_URL = "https://chatgpt.com/settings/usage";

interface Msg { id: string; role: "user" | "assistant"; content: string; evidence?: Evidence[] | null; tool_calls?: any[] | null;
  unverified?: boolean; provider?: string | null }

export function useAiStatus() {
  return useQuery({ queryKey: ["ai:status"], queryFn: () => invoke<any>("ai:status"), staleTime: 5000 });
}

export function AiPanel({ full = false, threadId: externalThread }: { full?: boolean; threadId?: string | null }) {
  const qc = useQueryClient();
  const { chartContext, pendingQuestion, clearPending, toggleAi } = useUi();
  const status = useAiStatus();
  const [provider, setProvider] = useState<AiProvider>(() => (localStorage.getItem("vitalis.ai.provider") as AiProvider) || "chatgpt");
  const [model, setModel] = useState<string>(() => localStorage.getItem("vitalis.ai.model") || "");
  const [threadId, setThreadId] = useState<string | null>(externalThread ?? null);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState<{ requestId: string | null; text: string; status: string; tools: { name: string; ok?: boolean; error?: string }[] } | null>(null);
  const [consent, setConsent] = useState<Extract<AiEvent, { type: "consent" }> | null>(null);
  const [approveRemaining, setApproveRemaining] = useState(false);
  const [error, setError] = useState<Extract<AiEvent, { type: "error" }> | null>(null);
  const [ctx, setCtx] = useState(chartContext);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => { if (externalThread !== undefined) setThreadId(externalThread); }, [externalThread]);
  useEffect(() => { localStorage.setItem("vitalis.ai.provider", provider); }, [provider]);
  useEffect(() => { if (model) localStorage.setItem("vitalis.ai.model", model); }, [model]);
  useEffect(() => {
    if (pendingQuestion) { setInput(pendingQuestion); setCtx(chartContext); clearPending(); }
  }, [pendingQuestion, chartContext, clearPending]);

  const available: Record<AiProvider, boolean> = useMemo(() => {
    const s = status.data;
    return { chatgpt: !!s?.chatgpt?.connected && !!s?.chatgpt?.sharing, openai_api: !!s?.openai_api?.configured, local: !!s?.local?.enabled };
  }, [status.data]);
  const models = useQuery({ queryKey: ["ai:models", provider], queryFn: () => invoke<{ id: string; label: string }[]>("ai:models", { provider }),
    enabled: available[provider], retry: false, staleTime: 300_000 });
  useEffect(() => {
    const list = models.data ?? [];
    if (list.length && !list.some((m) => m.id === model)) setModel(list[0].id);
  }, [models.data, model]);

  const messages = useQuery({ queryKey: ["chat.messages", threadId], enabled: !!threadId,
    queryFn: () => engine<Msg[]>("chat.messages", { thread_id: threadId }) });

  useEffect(() => onAiEvent((e) => {
    setStreaming((s) => {
      if (!s || (s.requestId && s.requestId !== e.requestId)) return s;
      const base = { ...s, requestId: e.requestId };
      if (e.type === "delta") return { ...base, text: base.text + e.text };
      if (e.type === "status") return { ...base, status: e.text };
      if (e.type === "tool_call") {
        const tools = [...base.tools];
        if (e.ok === undefined) tools.push({ name: e.name });
        else { const last = [...tools].reverse().find((t) => t.name === e.name && t.ok === undefined); if (last) { last.ok = e.ok; last.error = e.error; } }
        return { ...base, tools, text: "" };
      }
      return base;
    });
    if (e.type === "consent") { setConsent(e); setApproveRemaining(false); }
    if (e.type === "done") {
      setStreaming(null);
      qc.setQueryData<Msg[]>(["chat.messages", threadId], (old) => [...(old ?? []),
        { id: e.messageId, role: "assistant", content: e.content, evidence: e.evidence, unverified: e.unverifiedNumbers }]);
      qc.invalidateQueries({ queryKey: ["chat.threads"] });
    }
    if (e.type === "error") { setStreaming(null); setError(e); setConsent(null); qc.invalidateQueries({ queryKey: ["chat.messages", threadId] }); }
  }), [qc, threadId]);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [messages.data, streaming?.text]);

  const send = async () => {
    const q = input.trim();
    if (!q || streaming) return;
    setError(null);
    let tid = threadId;
    if (!tid) {
      const t = await engine<{ id: string }>("chat.thread_create", { title: "New conversation", provider });
      tid = t.id;
      setThreadId(tid);
    }
    qc.setQueryData<Msg[]>(["chat.messages", tid], (old) => [...(old ?? []), { id: `local-${Date.now()}`, role: "user", content: q }]);
    setInput("");
    setStreaming({ requestId: null, text: "", status: "Starting…", tools: [] });
    try {
      const requestId = await invoke<string>("ai:send", { threadId: tid, message: q, provider, model, chartContext: ctx ?? null });
      setStreaming((s) => (s ? { ...s, requestId } : s));
      setCtx(null);
    } catch (e: any) {
      setStreaming(null);
      setError({ type: "error", requestId: "", code: e.code ?? "error", message: e.message });
    }
  };
  const decide = async (approve: boolean) => {
    if (!consent) return;
    await invoke("ai:decide", { requestId: consent.requestId, approve, approveRemaining });
    setConsent(null);
  };

  const notReady = !available[provider];
  return (
    <div className={cx("flex h-full min-h-0 flex-col", full && "rounded-xl border border-line bg-s1")} data-testid="ai-panel">
      <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
        <div className="flex-1 text-[13px] font-semibold">AI Health Analyst</div>
        <Button size="sm" variant="ghost" onClick={() => { setThreadId(null); setError(null); }} title="New conversation"><Plus size={14} /></Button>
        {!full && <Button size="sm" variant="ghost" onClick={() => toggleAi(false)} title="Close"><X size={14} /></Button>}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
        <Select label="AI provider" value={provider} onChange={(v) => setProvider(v as AiProvider)} testId="ai-provider"
          options={(["chatgpt", "openai_api", "local"] as AiProvider[]).map((p) => ({ value: p, label: `${PROVIDER_LABEL[p]}${available[p] ? "" : " (not set up)"}` }))} />
        {available[provider] && (models.data?.length ? (
          <Select label="Model" value={model} onChange={setModel} options={models.data.map((m) => ({ value: m.id, label: m.label }))} testId="ai-model" />
        ) : models.isLoading ? <Spinner /> : models.error ? <span className="text-[11px] text-critical">{String((models.error as Error).message)}</span> : null)}
        {provider === "chatgpt" && available.chatgpt && (
          <span className="flex items-center gap-1 text-[11px] text-fg2">Using ChatGPT plan ·
            <button className="inline-flex items-center gap-0.5 text-accent-ink hover:underline" onClick={() => openExternal(USAGE_URL)}>Manage usage<ExternalLink size={10} /></button></span>)}
        {provider !== "local" && <Badge tone="warning"><ShieldAlert size={11} />Cloud · consent per request</Badge>}
        {provider === "local" && <Badge tone="good">On-device</Badge>}
      </div>

      <div className="min-h-0 flex-1 space-y-3 overflow-auto px-4 py-3">
        {notReady && (
          <div className="rounded-lg border border-line bg-s2 p-3 text-xs text-fg2">
            <b className="text-fg">{PROVIDER_LABEL[provider]} is not set up.</b> All dashboards, statistics and insights work without AI.
            To use the analyst, connect a provider in <button className="text-accent-ink hover:underline" onClick={() => navigate("settings")}>Settings → AI</button>.
          </div>
        )}
        {!threadId && !notReady && (
          <div className="space-y-2 text-xs text-fg2">
            <p>Ask about your own data. The analyst can only see numbers it retrieves through read-only VITALIS tools, and you
              approve exactly what is sent before each cloud request.</p>
            <div className="flex flex-wrap gap-1.5">
              {["Why was my recovery worse this morning?", "Compare my sleep in the last 30 days with the previous 30 days.",
                "Is caffeine after 3 PM associated with worse sleep?", "What data supports your conclusion?",
                "What information is missing?", "What should I focus on improving this week?"].map((s) => (
                <button key={s} onClick={() => setInput(s)} className="rounded-full border border-line px-2.5 py-1 text-[11px] hover:border-line-strong hover:text-fg">{s}</button>
              ))}
            </div>
          </div>
        )}
        {(messages.data ?? []).filter((m) => m.role !== ("tool" as any)).map((m) => <Message key={m.id} m={m} />)}
        {streaming && (
          <div className="rounded-lg bg-s2 p-3 text-[13px]" aria-live="polite">
            <div className="mb-1 flex items-center justify-between"><Spinner label={streaming.status} />
              {streaming.requestId && <Button size="sm" variant="ghost" onClick={() => invoke("ai:cancel", { requestId: streaming.requestId })}><Square size={11} />Stop</Button>}</div>
            {streaming.tools.length > 0 && <ToolList tools={streaming.tools} />}
            {streaming.text && <div className="prose-ai mt-2 whitespace-pre-wrap">{streaming.text}</div>}
          </div>
        )}
        {error && (
          <div role="alert" className="rounded-lg border border-critical/40 bg-critical/10 p-3 text-xs">
            <div className="flex items-center gap-1.5 font-medium"><AlertTriangle size={13} className="text-critical" />{error.message}</div>
            {error.manageUsage && <button className="mt-1 text-accent-ink hover:underline" onClick={() => openExternal(USAGE_URL)}>Manage usage in ChatGPT settings</button>}
          </div>
        )}
        <div ref={bottom} />
      </div>

      <div className="border-t border-line p-3">
        {ctx && (
          <div className="mb-2 flex items-center gap-2 text-[11px] text-fg2"><Badge tone="accent">Chart</Badge>
            {ctx.title ?? ctx.metrics.join(", ")} · {ctx.start} → {ctx.end}
            <button aria-label="Remove chart context" onClick={() => setCtx(null)}><X size={11} /></button></div>
        )}
        <div className="flex gap-2">
          <textarea value={input} onChange={(e) => setInput(e.target.value)} rows={full ? 3 : 2} data-testid="ai-input"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            placeholder={notReady ? "Set up a provider to ask questions" : "Ask about your sleep, recovery, habits…"}
            className={cx(inputCls, "resize-none")} disabled={notReady} aria-label="Question for the AI analyst" />
          <Button variant="primary" onClick={send} disabled={notReady || !input.trim() || !!streaming || !model} testId="ai-send"><Send size={14} /></Button>
        </div>
      </div>

      <Modal open={!!consent} onOpenChange={(o) => { if (!o) decide(false); }} wide title="Review data before it leaves this PC"
        description={consent ? `${PROVIDER_LABEL[consent.provider]} · ${consent.model} · request ${consent.round} · ${(consent.bytes / 1024).toFixed(1)} KB` : ""}>
        {consent && (
          <div data-testid="consent-dialog">
            <ul className="mb-3 list-disc pl-5 text-xs text-fg2">{consent.categories.map((c) => <li key={c}>{c}</li>)}</ul>
            <p className="mb-2 text-[11px] text-muted">This is the exact JSON body that will be sent (your credentials are sent separately in the
              authorization header and are not shown). Private journal habits are excluded.</p>
            <pre className="max-h-[46vh] overflow-auto rounded-lg border border-line bg-s2 p-3 text-[11px] leading-relaxed text-fg2" data-testid="consent-payload">{consent.payload}</pre>
            <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
              <label className="flex items-center gap-2 text-xs text-fg2"><input type="checkbox" checked={approveRemaining}
                onChange={(e) => setApproveRemaining(e.target.checked)} />Also approve the remaining tool-result requests for this question</label>
              <div className="flex gap-2">
                <Button onClick={() => decide(false)}>Cancel — send nothing</Button>
                <Button variant="primary" onClick={() => decide(true)} testId="consent-approve">Approve and send</Button>
              </div>
            </div>
          </div>
        )}
      </Modal>
      <ChatGptWelcome />
    </div>
  );
}

function ToolList({ tools }: { tools: { name: string; ok?: boolean; error?: string }[] }) {
  return (
    <ul className="space-y-0.5 text-[11px] text-fg2">
      {tools.map((t, i) => (
        <li key={i} className="flex items-center gap-1.5"><Wrench size={11} />{t.name}
          {t.ok === true && <span className="text-good">✓</span>}{t.ok === false && <span className="text-critical">✗ {t.error}</span>}</li>
      ))}
    </ul>
  );
}

function Message({ m }: { m: Msg }) {
  const showEvidence = useUi((s) => s.showEvidence);
  const evidence = m.evidence ?? [];
  if (m.role === "user") {
    return <div className="ml-8 rounded-lg bg-s3 px-3 py-2 text-[13px] text-fg">{m.content}</div>;
  }
  const byId = new Map(evidence.map((e) => [e.id, e]));
  const open = (e: Evidence) => e.metric && e.start && e.end && showEvidence({ metric: e.metric, start: e.start, end: e.end, label: e.label });
  return (
    <div className="rounded-lg border border-line bg-s2 p-3 text-[13px]" data-testid="ai-answer">
      {m.unverified && (
        <div className="mb-2 flex items-center gap-1.5 rounded bg-warning/15 px-2 py-1 text-[11px] text-warning">
          <AlertTriangle size={12} />No data was retrieved for this answer — treat any numbers as unverified.</div>
      )}
      <div className="prose-ai">{renderMarkdown(m.content, (id) => {
        const e = byId.get(id);
        return (
          <button key={id + Math.random()} onClick={() => e && open(e)} data-testid="evidence-chip" title={id}
            className={cx("mx-0.5 inline-flex items-center rounded border px-1 text-[10px]", e ? "border-accent/50 text-accent-ink hover:bg-accent/10" : "border-critical/50 text-critical")}>
            {e ? `${e.label ?? e.metric}${e.start ? ` · ${fmtShort(e.start)}–${fmtShort(e.end!)}` : ""}` : "unknown source"}
          </button>
        );
      })}</div>
      {evidence.length > 0 && (
        <details className="mt-2 text-[11px] text-fg2"><summary className="cursor-pointer">Data used ({evidence.length})</summary>
          <ul className="mt-1 space-y-0.5">{evidence.map((e) => (
            <li key={e.id}><button className="text-accent-ink hover:underline" onClick={() => open(e)}>{e.label ?? e.kind}</button>
              {" "}{e.start ? `${e.start} → ${e.end}` : ""}{e.n != null ? ` · n=${e.n}` : ""}</li>))}</ul>
        </details>
      )}
      {(m.tool_calls?.length ?? 0) > 0 && <details className="mt-1 text-[11px] text-muted"><summary className="cursor-pointer">Tool calls ({m.tool_calls!.length})</summary>
        <ToolList tools={m.tool_calls!.map((t: any) => ({ name: t.name, ok: t.ok, error: t.error }))} /></details>}
    </div>
  );
}

const fmtShort = (iso: string) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** Tiny, safe markdown renderer (no HTML injection): headings, bullets, bold, inline code, evidence tokens. */
export function renderMarkdown(text: string, chip: (id: string) => ReactNode): ReactNode {
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  let list: ReactNode[] = [];
  const inline = (s: string, k: string): ReactNode[] => s.split(/(\[\[ev:[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) => {
    if (part.startsWith("[[ev:")) return <Fragment key={`${k}${i}`}>{chip(part.slice(5, -2))}</Fragment>;
    if (part.startsWith("**")) return <strong key={`${k}${i}`}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("`")) return <code key={`${k}${i}`}>{part.slice(1, -1)}</code>;
    return <Fragment key={`${k}${i}`}>{part}</Fragment>;
  });
  const flush = () => { if (list.length) { out.push(<ul key={`ul${out.length}`}>{list}</ul>); list = []; } };
  lines.forEach((ln, i) => {
    const h = ln.match(/^#{1,4}\s+(.*)/);
    const b = ln.match(/^\s*(?:[-*•]|\d+\.)\s+(.*)/);
    if (h) { flush(); out.push(<h4 key={i}>{inline(h[1], `h${i}`)}</h4>); }
    else if (b) list.push(<li key={i}>{inline(b[1], `l${i}`)}</li>);
    else if (ln.trim() === "") flush();
    else { flush(); out.push(<p key={i}>{inline(ln, `p${i}`)}</p>); }
  });
  flush();
  return out;
}

function ChatGptWelcome() {
  const status = useAiStatus();
  const qc = useQueryClient();
  const s = status.data?.chatgpt;
  const open = !!s?.connected && s?.sharing && !s?.welcomed;
  return (
    <Modal open={open} onOpenChange={async (o) => { if (!o) { await invoke("ai:siwc:welcomed"); qc.invalidateQueries({ queryKey: ["ai:status"] }); } }}
      title="You're using your ChatGPT plan">
      <p className="text-[13px] text-fg2">Eligible AI requests in VITALIS will use your ChatGPT plan. VITALIS cannot see your ChatGPT
        conversations, memories or other account data. You can review usage and limits in ChatGPT settings at any time.</p>
      <div className="mt-4 flex justify-end gap-2">
        <Button onClick={() => openExternal(USAGE_URL)}>Manage usage</Button>
        <Button variant="primary" onClick={async () => { await invoke("ai:siwc:welcomed"); qc.invalidateQueries({ queryKey: ["ai:status"] }); }}>Got it</Button>
      </div>
    </Modal>
  );
}
