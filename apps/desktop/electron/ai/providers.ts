// AI provider adapters. Both wire formats keep the full conversation client-side (store:false, no
// previous_response_id) so the same code works for an API key and for ChatGPT plan usage.
import { parseSse } from "./sse";

export type FetchLike = (url: string, init: RequestInit) => Promise<Response>;

export interface ToolDef {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
}

export interface ToolCall {
  callId: string;
  name: string;
  arguments: string;
}

export interface RoundResult {
  text: string;
  toolCalls: ToolCall[];
  raw: unknown[];
}

export class ProviderError extends Error {
  constructor(public code: string, message: string, public status?: number, public requestId?: string | null) {
    super(message);
  }
}

export interface Conversation {
  system: string;
  items: unknown[];
}

export interface Provider {
  kind: "responses" | "chat_completions";
  buildBody(conv: Conversation, model: string, tools: ToolDef[]): Record<string, unknown>;
  send(body: Record<string, unknown>, onDelta: (t: string) => void, signal: AbortSignal): Promise<RoundResult>;
  appendRound(conv: Conversation, round: RoundResult, outputs: { callId: string; output: string }[]): void;
  startConversation(system: string, history: { role: "user" | "assistant"; content: string }[], message: string): Conversation;
}

const SIWC_ERRORS: Record<string, string> = {
  subscription_sharing_user_not_eligible: "ChatGPT plan usage is not available for this account or workspace.",
  subscription_sharing_usage_limit_exceeded: "You've reached the ChatGPT plan usage limit for this app. Manage usage in ChatGPT settings.",
  subscription_sharing_usage_unavailable: "ChatGPT usage availability could not be checked. Try again shortly.",
  subscription_sharing_unsupported_capability: "This request used a feature not supported with ChatGPT plan usage.",
  subscription_sharing_route_not_supported: "This API route is not supported with ChatGPT plan usage.",
  subscription_sharing_invalid_user: "ChatGPT session could not be validated. Please sign in again.",
  subscription_sharing_user_unavailable: "ChatGPT account information is temporarily unavailable. Try again shortly.",
};

export async function readError(res: Response): Promise<ProviderError> {
  const rid = res.headers.get("x-request-id");
  let code = `http_${res.status}`;
  let message = res.statusText || "request failed";
  try {
    const body: any = await res.json();
    if (body?.error) {
      code = body.error.code || body.error.type || code;
      message = body.error.message || message;
    } else if (body?.detail) {
      message = String(body.detail);
    }
  } catch { /* non-JSON body */ }
  if (SIWC_ERRORS[code]) message = SIWC_ERRORS[code];
  if (res.status === 401 && code === `http_401`) message = "Authorization was not accepted (check the selected account or API key).";
  return new ProviderError(code, message, res.status, rid);
}

/** OpenAI Responses API (POST /v1/responses), streaming, function tools grouped in one namespace. */
export function responsesProvider(opts: { baseUrl: string; token: () => Promise<string>; fetch: FetchLike }): Provider {
  return {
    kind: "responses",
    startConversation(system, history, message) {
      const items: unknown[] = history.map((m) => ({ role: m.role, content: m.content }));
      items.push({ role: "user", content: message });
      return { system, items };
    },
    buildBody(conv, model, tools) {
      return {
        model,
        instructions: conv.system,
        input: conv.items,
        tools: [{ type: "namespace", name: "vitalis", description: "Read-only analytics over the user's own stored health data.",
          tools: tools.map((t) => ({ type: "function", name: t.name, description: t.description, parameters: t.parameters })) }],
        store: false,
        stream: true,
      };
    },
    async send(body, onDelta, signal) {
      const res = await opts.fetch(`${opts.baseUrl}/responses`, {
        method: "POST", signal,
        headers: { "content-type": "application/json", authorization: `Bearer ${await opts.token()}`, accept: "text/event-stream" },
        body: JSON.stringify(body),
      });
      if (!res.ok || !res.body) throw await readError(res);
      let text = "";
      const calls: ToolCall[] = [];
      const raw: unknown[] = [];
      let completed = false;
      for await (const ev of parseSse(res.body, signal)) {
        if (!ev.data || ev.data === "[DONE]") continue;
        let d: any;
        try { d = JSON.parse(ev.data); } catch { continue; }
        const type = d.type ?? ev.event;
        if (type === "response.output_text.delta") { text += d.delta; onDelta(d.delta); }
        else if (type === "response.output_item.done") {
          const it = d.item;
          if (it?.type === "function_call") {
            calls.push({ callId: it.call_id, name: it.name, arguments: it.arguments ?? "{}" });
            raw.push({ type: "function_call", call_id: it.call_id, name: it.name, arguments: it.arguments ?? "{}",
              ...(it.namespace ? { namespace: it.namespace } : {}) });
          }
        } else if (type === "response.completed") completed = true;
        else if (type === "response.failed" || type === "error") {
          const e = d.response?.error ?? d.error ?? d;
          throw new ProviderError(e?.code ?? "response_failed", SIWC_ERRORS[e?.code] ?? e?.message ?? "The model request failed.");
        } else if (type === "response.incomplete") {
          throw new ProviderError("incomplete", `The response was incomplete (${d.response?.incomplete_details?.reason ?? "unknown"}).`);
        }
      }
      if (!completed) throw new ProviderError("stream_interrupted", "The response stream ended before completion.");
      return { text, toolCalls: calls, raw };
    },
    appendRound(conv, round, outputs) {
      if (round.text) conv.items.push({ role: "assistant", content: round.text });
      conv.items.push(...round.raw);
      for (const o of outputs) conv.items.push({ type: "function_call_output", call_id: o.callId, output: o.output });
    },
  };
}

/** OpenAI-compatible Chat Completions for local runtimes (Ollama, LM Studio, llama.cpp server) on loopback only. */
export function chatCompletionsProvider(opts: { baseUrl: string; fetch: FetchLike }): Provider {
  return {
    kind: "chat_completions",
    startConversation(system, history, message) {
      return { system, items: [...history.map((m) => ({ role: m.role, content: m.content })), { role: "user", content: message }] };
    },
    buildBody(conv, model, tools) {
      return {
        model, stream: true,
        messages: [{ role: "system", content: conv.system }, ...conv.items],
        tools: tools.map((t) => ({ type: "function", function: { name: t.name, description: t.description, parameters: t.parameters } })),
      };
    },
    async send(body, onDelta, signal) {
      const res = await opts.fetch(`${opts.baseUrl}/chat/completions`, {
        method: "POST", signal, headers: { "content-type": "application/json" }, body: JSON.stringify(body),
      });
      if (!res.ok || !res.body) throw await readError(res);
      let text = "";
      const acc = new Map<number, ToolCall>();
      for await (const ev of parseSse(res.body, signal)) {
        if (!ev.data || ev.data === "[DONE]") continue;
        let d: any;
        try { d = JSON.parse(ev.data); } catch { continue; }
        const delta = d.choices?.[0]?.delta ?? {};
        if (delta.content) { text += delta.content; onDelta(delta.content); }
        for (const tc of delta.tool_calls ?? []) {
          const cur = acc.get(tc.index ?? 0) ?? { callId: tc.id ?? `call_${tc.index ?? 0}`, name: "", arguments: "" };
          if (tc.id) cur.callId = tc.id;
          if (tc.function?.name) cur.name += tc.function.name;
          if (tc.function?.arguments) cur.arguments += tc.function.arguments;
          acc.set(tc.index ?? 0, cur);
        }
      }
      const calls = [...acc.values()];
      return { text, toolCalls: calls, raw: calls.length ? [{ role: "assistant", content: text || null,
        tool_calls: calls.map((c) => ({ id: c.callId, type: "function", function: { name: c.name, arguments: c.arguments } })) }] : [] };
    },
    appendRound(conv, round, outputs) {
      if (round.raw.length) conv.items.push(...round.raw);
      else if (round.text) conv.items.push({ role: "assistant", content: round.text });
      for (const o of outputs) conv.items.push({ role: "tool", tool_call_id: o.callId, content: o.output });
    },
  };
}
