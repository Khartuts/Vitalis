// AI turn orchestration: consent preview → provider round → validated read-only tool calls → … → stored answer.
import { createHash, randomUUID } from "node:crypto";
import type { AiEvent, AiProvider, Evidence } from "../../shared/contract";
import type { Conversation, Provider, ToolDef } from "./providers";
import { ProviderError } from "./providers";

export interface EngineLike {
  call<T = unknown>(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<T>;
}

export const MAX_ROUNDS = 8;

interface PendingConsent {
  resolve: (d: { approve: boolean; approveRemaining: boolean }) => void;
}

export class Orchestrator {
  private consents = new Map<string, PendingConsent>();
  private aborts = new Map<string, AbortController>();

  constructor(private engine: EngineLike, private makeProvider: (p: AiProvider) => Promise<Provider>) {}

  decide(requestId: string, approve: boolean, approveRemaining: boolean) {
    const c = this.consents.get(requestId);
    if (c) {
      this.consents.delete(requestId);
      c.resolve({ approve, approveRemaining });
    }
  }

  cancel(requestId: string) {
    this.aborts.get(requestId)?.abort();
    this.decide(requestId, false, false);
  }

  private awaitConsent(requestId: string) {
    return new Promise<{ approve: boolean; approveRemaining: boolean }>((resolve) => this.consents.set(requestId, { resolve }));
  }

  static categories(body: Record<string, unknown>, round: number, toolNames: string[]): string[] {
    const cats = ["Your question and conversation history from this thread", "Analyst instructions",
      "Tool definitions (metric and non-private habit names)"];
    if (round > 1) cats.push(`Results of read-only analytics tools: ${[...new Set(toolNames)].join(", ")}`);
    return cats;
  }

  async ask(req: { threadId: string; message: string; provider: AiProvider; model: string;
    chartContext?: { metrics: string[]; start: string; end: string; title?: string } | null },
  emit: (e: AiEvent) => void): Promise<string> {
    const requestId = randomUUID();
    const cloud = req.provider !== "local";
    const ac = new AbortController();
    this.aborts.set(requestId, ac);
    const run = async () => {
      emit({ type: "status", requestId, text: "Preparing analysis…" });
      const provider = await this.makeProvider(req.provider);
      const { prompt } = await this.engine.call<{ prompt: string }>("ai.system_prompt", {
        provider_kind: req.provider, chart_context: req.chartContext ?? null });
      const tools = await this.engine.call<ToolDef[]>("ai.tools", { cloud });
      const prior = await this.engine.call<{ role: string; content: string }[]>("chat.messages", { thread_id: req.threadId });
      const history = prior.filter((m) => m.role === "user" || m.role === "assistant").slice(-16)
        .map((m) => ({ role: m.role as "user" | "assistant", content: m.content.slice(0, 12000) }));
      await this.engine.call("chat.append", { thread_id: req.threadId, role: "user", content: req.message });
      if (prior.length === 0) {
        await this.engine.call("chat.thread_rename", { id: req.threadId, title: req.message.slice(0, 80) });
      }
      const conv: Conversation = provider.startConversation(prompt, history, req.message);
      const evidence = new Map<string, Evidence>();
      const toolLog: { name: string; arguments: unknown; ok: boolean; error?: string }[] = [];
      let approveAll = !cloud;
      let finalText = "";
      for (let round = 1; round <= MAX_ROUNDS; round++) {
        const body = provider.buildBody(conv, req.model, tools);
        const payload = JSON.stringify(body, null, 2);
        if (!approveAll) {
          const decision = this.awaitConsent(requestId); // register before emitting so an immediate decision is not lost
          emit({ type: "consent", requestId, round, provider: req.provider, model: req.model, payload,
            categories: Orchestrator.categories(body, round, toolLog.map((t) => t.name)), bytes: Buffer.byteLength(payload) });
          const d = await decision;
          if (!d.approve) {
            emit({ type: "error", requestId, code: "declined", message: "Nothing was sent. The request was cancelled at your choice." });
            return;
          }
          approveAll = d.approveRemaining;
        }
        if (cloud) {
          await this.engine.call("ai.disclosure_log", { thread_id: req.threadId, provider: req.provider, model: req.model,
            categories: Orchestrator.categories(body, round, toolLog.map((t) => t.name)),
            payload_sha256: createHash("sha256").update(payload).digest("hex"), payload_bytes: Buffer.byteLength(payload) });
        }
        emit({ type: "status", requestId, text: round === 1 ? "Thinking…" : "Interpreting results…" });
        const res = await provider.send(body, (t) => emit({ type: "delta", requestId, text: t }), ac.signal);
        if (res.toolCalls.length === 0) {
          finalText = res.text;
          break;
        }
        const outputs = [];
        for (const call of res.toolCalls) {
          let args: unknown = call.arguments;
          try { args = JSON.parse(call.arguments || "{}"); } catch { /* engine reports invalid_json */ }
          emit({ type: "tool_call", requestId, name: call.name, arguments: args });
          const r = await this.engine.call<{ ok: boolean; result?: any; error?: { code: string; message: string } }>(
            "ai.call", { name: call.name, arguments: call.arguments || "{}", cloud });
          toolLog.push({ name: call.name, arguments: args, ok: r.ok, error: r.error?.message });
          emit({ type: "tool_call", requestId, name: call.name, arguments: args, ok: r.ok, error: r.error?.message });
          for (const ev of (r.ok ? r.result?.evidence ?? [] : []) as Evidence[]) evidence.set(ev.id, ev);
          outputs.push({ callId: call.callId, output: JSON.stringify(r.ok ? r.result : { error: r.error }) });
        }
        provider.appendRound(conv, res, outputs);
        if (round === MAX_ROUNDS) finalText = res.text || "I could not finish the analysis within the allowed number of steps.";
      }
      const successfulCalls = toolLog.filter((t) => t.ok).length;
      const unverifiedNumbers = successfulCalls === 0 && /\d/.test(finalText.replace(/\[\[ev:[^\]]+\]\]/g, ""));
      const ev = [...evidence.values()];
      const saved = await this.engine.call<{ id: string }>("chat.append", { thread_id: req.threadId, role: "assistant",
        content: finalText, tool_calls: toolLog, evidence: ev, provider: req.provider, model: req.model });
      emit({ type: "done", requestId, messageId: saved.id, content: finalText, evidence: ev, toolCalls: successfulCalls,
        unverifiedNumbers });
    };
    run().catch((e: any) => {
      const code = e instanceof ProviderError ? e.code : e?.code ?? "error";
      emit({ type: "error", requestId, code, message: ac.signal.aborted ? "Cancelled." : String(e?.message ?? e),
        manageUsage: String(code).startsWith("subscription_sharing_usage") });
    }).finally(() => this.aborts.delete(requestId));
    return requestId;
  }
}
