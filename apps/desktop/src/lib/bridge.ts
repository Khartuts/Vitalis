// Typed wrapper around the preload bridge. Every call goes through main-process validation.
import type { AiEvent, RendererEngineMethod } from "@shared/contract";

declare global {
  interface Window {
    vitalis: {
      invoke: (channel: string, payload?: unknown) => Promise<{ ok: boolean; data?: unknown; error?: { code: string; message: string } }>;
      on: (channel: "engine:event" | "ai:event" | "phone:event", cb: (p: any) => void) => () => void;
      reportReady: () => void;
    };
  }
}

export class BridgeError extends Error {
  constructor(public code: string, message: string) {
    super(message);
  }
}

export async function invoke<T = any>(channel: string, payload?: unknown): Promise<T> {
  if (!window.vitalis) throw new BridgeError("no_bridge", "VITALIS bridge unavailable");
  const r = await window.vitalis.invoke(channel, payload);
  if (!r.ok) throw new BridgeError(r.error?.code ?? "error", r.error?.message ?? "Unknown error");
  return r.data as T;
}

export const engine = <T = any>(method: RendererEngineMethod, params?: Record<string, unknown>) =>
  invoke<T>("engine:call", { method, params });

export const onEngineEvent = (cb: (e: any) => void) => window.vitalis?.on("engine:event", cb) ?? (() => undefined);
export const onAiEvent = (cb: (e: AiEvent) => void) => window.vitalis?.on("ai:event", cb) ?? (() => undefined);
export const openExternal = (url: string) => invoke("app:openExternal", { url });
/** Phone app only: Health Connect import progress and results. */
export const onPhoneEvent = (cb: (e: any) => void) => window.vitalis?.on("phone:event", cb) ?? (() => undefined);
