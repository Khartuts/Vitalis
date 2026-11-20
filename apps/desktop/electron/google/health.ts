// Google Health API v4 connector (read-only). OAuth 2.0 for installed apps: system browser, loopback redirect,
// PKCE, offline access. Docs read 2026-11-20: developers.google.com/health (setup, scopes, dataPoints.list).
import type { FetchLike } from "../ai/providers";
import { pkce, randomToken, startLoopback } from "../oauth-loopback";
import type { Vault } from "../vault";

export const GOOGLE = {
  authorize: "https://accounts.google.com/o/oauth2/v2/auth",
  token: "https://oauth2.googleapis.com/token",
  revoke: "https://oauth2.googleapis.com/revoke",
  api: "https://health.googleapis.com/v4",
};

export interface GoogleTokens {
  access_token: string;
  refresh_token?: string;
  expires_at: number;
  scope: string;
}

export interface EngineLike {
  call<T = unknown>(method: string, params?: Record<string, unknown>, timeoutMs?: number): Promise<T>;
}

const SMALL_PAGE_TYPES = new Set(["sleep", "exercise"]);

export class GoogleHealthClient {
  constructor(private deps: { vault: Vault; fetch: FetchLike; openExternal: (u: string) => Promise<void>; engine: EngineLike;
    endpoints?: Partial<typeof GOOGLE>; sleep?: (ms: number) => Promise<void> }) {}

  private get ep() {
    return { ...GOOGLE, ...(this.deps.endpoints ?? {}) };
  }

  config(): { clientId: string; clientSecret: string } | null {
    return this.deps.vault.get("google_client");
  }

  setConfig(c: { clientId: string; clientSecret: string }) {
    this.deps.vault.set("google_client", c);
  }

  status() {
    const t = this.deps.vault.get<GoogleTokens>("google_tokens");
    const cur = this.deps.vault.get<Record<string, string>>("google_cursors") ?? {};
    return { configured: !!this.config(), connected: !!t?.refresh_token, scope: t?.scope ?? null,
      cursors: cur, lastSync: this.deps.vault.get<{ at: string; summary: unknown }>("google_last_sync") };
  }

  async connect(): Promise<ReturnType<GoogleHealthClient["status"]>> {
    const cfg = this.config();
    if (!cfg) throw Object.assign(new Error("Add your Google OAuth client ID first (setup wizard)."), { code: "not_configured" });
    const { scopes } = await this.deps.engine.call<{ scopes: string[] }>("import.google_filter");
    const state = randomToken();
    const { verifier, challenge } = pkce();
    const lb = await startLoopback({ port: 0, path: "/oauth2callback", expectedState: state });
    const q = new URLSearchParams({ client_id: cfg.clientId, redirect_uri: lb.redirectUri, response_type: "code",
      scope: scopes.join(" "), access_type: "offline", prompt: "consent", include_granted_scopes: "true", state,
      code_challenge: challenge, code_challenge_method: "S256" });
    await this.deps.openExternal(`${this.ep.authorize}?${q}`);
    const { params } = await lb.result;
    if (params.get("error")) throw Object.assign(new Error(`Google sign-in was not completed (${params.get("error")}).`), { code: params.get("error") });
    const form: Record<string, string> = { code: params.get("code") ?? "", client_id: cfg.clientId, redirect_uri: lb.redirectUri,
      grant_type: "authorization_code", code_verifier: verifier };
    if (cfg.clientSecret) form.client_secret = cfg.clientSecret;
    const tok = await this.tokenCall(form);
    const granted = String(tok.scope ?? "");
    this.deps.vault.set("google_tokens", { access_token: tok.access_token, refresh_token: tok.refresh_token,
      expires_at: Date.now() + Number(tok.expires_in ?? 3600) * 1000, scope: granted });
    await this.identity();
    return this.status();
  }

  private async tokenCall(form: Record<string, string>) {
    const res = await this.deps.fetch(this.ep.token, { method: "POST", headers: { "content-type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams(form).toString() });
    const body: any = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(body.error_description ?? body.error ?? `token error ${res.status}`), { code: body.error ?? `http_${res.status}` });
    return body;
  }

  private async accessToken(): Promise<string> {
    const t = this.deps.vault.get<GoogleTokens>("google_tokens");
    if (!t?.refresh_token) throw Object.assign(new Error("Google Health is not connected."), { code: "not_connected" });
    if (t.expires_at - Date.now() > 60_000) return t.access_token;
    const cfg = this.config()!;
    const form: Record<string, string> = { grant_type: "refresh_token", refresh_token: t.refresh_token, client_id: cfg.clientId };
    if (cfg.clientSecret) form.client_secret = cfg.clientSecret;
    try {
      const tok = await this.tokenCall(form);
      const nt = { ...t, access_token: tok.access_token, expires_at: Date.now() + Number(tok.expires_in ?? 3600) * 1000,
        scope: tok.scope ?? t.scope };
      this.deps.vault.set("google_tokens", nt);
      return nt.access_token;
    } catch (e: any) {
      if (e.code === "invalid_grant") {
        this.deps.vault.delete("google_tokens");
        throw Object.assign(new Error("Google authorization expired (refresh tokens of apps in Testing mode expire after "
          + "7 days). Please reconnect."), { code: "reconnect_required" });
      }
      throw e;
    }
  }

  private async api(path: string, attempt = 0): Promise<any> {
    const res = await this.deps.fetch(`${this.ep.api}${path}`, { method: "GET",
      headers: { authorization: `Bearer ${await this.accessToken()}` } });
    if ((res.status === 429 || res.status >= 500) && attempt < 5) {
      const ra = Number(res.headers.get("retry-after"));
      await (this.deps.sleep ?? ((ms) => new Promise((r) => setTimeout(r, ms))))(
        Number.isFinite(ra) && ra > 0 ? ra * 1000 : 1000 * 2 ** attempt);
      return this.api(path, attempt + 1);
    }
    if (res.status === 401 && attempt === 0) {
      const t = this.deps.vault.get<GoogleTokens>("google_tokens");
      if (t) this.deps.vault.set("google_tokens", { ...t, expires_at: 0 });
      return this.api(path, 1);
    }
    const body: any = await res.json().catch(() => ({}));
    if (!res.ok) {
      const reason = body?.error?.details?.find?.((d: any) => d.reason)?.reason ?? body?.error?.status;
      throw Object.assign(new Error(reason === "ACCOUNT_NOT_LINKED"
        ? "Your Google Account is not linked to Google Health yet. Open the Google Health app and sign in first."
        : body?.error?.message ?? `Google Health API error ${res.status}`), { code: reason ?? `http_${res.status}`, status: res.status });
    }
    return body;
  }

  async identity() {
    return this.api("/users/me/identity");
  }

  /** Incremental sync: per data type, re-read from (cursor − 3 days) to now, all pages; import via the engine. */
  async sync(days: number, progress?: (msg: string) => void) {
    const { types } = await this.deps.engine.call<{ types: string[] }>("import.google_filter");
    const cursors = this.deps.vault.get<Record<string, string>>("google_cursors") ?? {};
    const now = new Date();
    const summary: Record<string, unknown> = {};
    const errors: Record<string, string> = {};
    for (const type of types) {
      const since = cursors[type] ? new Date(new Date(cursors[type]).getTime() - 3 * 86400000)
        : new Date(now.getTime() - days * 86400000);
      try {
        const { filter } = await this.deps.engine.call<{ filter: string }>("import.google_filter_for",
          { data_type: type, start: since.toISOString(), end: now.toISOString() });
        const records: unknown[] = [];
        let pageToken = "";
        let pages = 0;
        do {
          const q = new URLSearchParams({ filter, pageSize: SMALL_PAGE_TYPES.has(type) ? "25" : "1440" });
          if (pageToken) q.set("pageToken", pageToken);
          const body = await this.api(`/users/me/dataTypes/${type}/dataPoints?${q}`);
          for (const dp of body.dataPoints ?? []) records.push({ dataType: type, dataPoint: dp });
          pageToken = body.nextPageToken ?? "";
          pages++;
          progress?.(`${type}: ${records.length} records (${pages} pages)`);
        } while (pageToken && pages < 2000);
        if (records.length) {
          summary[type] = await this.deps.engine.call("import.google_health", { records, detail: { data_type: type } }, 600000);
        } else summary[type] = { received: 0 };
        cursors[type] = now.toISOString();
      } catch (e: any) {
        errors[type] = String(e.message ?? e);
        if (e.code === "reconnect_required" || e.code === "ACCOUNT_NOT_LINKED") break;
        if (e.status === 403) continue; // scope not granted for this type: partial consent is respected
      }
    }
    this.deps.vault.set("google_cursors", cursors);
    const out = { at: now.toISOString(), summary, errors };
    this.deps.vault.set("google_last_sync", out);
    return out;
  }

  async disconnect() {
    const t = this.deps.vault.get<GoogleTokens>("google_tokens");
    let revoked = false;
    if (t?.refresh_token) {
      try {
        const res = await this.deps.fetch(`${this.ep.revoke}?token=${encodeURIComponent(t.refresh_token)}`, { method: "POST",
          headers: { "content-type": "application/x-www-form-urlencoded" } });
        revoked = res.ok;
      } catch { /* offline */ }
    }
    this.deps.vault.delete("google_tokens");
    this.deps.vault.delete("google_cursors");
    return { revoked, note: revoked ? undefined : "Local tokens removed. Also remove access at myaccount.google.com/permissions." };
  }
}
