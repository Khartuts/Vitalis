// Sign in with ChatGPT — ChatGPT plan usage for open-source / locally hosted apps.
// Implements the documented protocol (developers.openai.com/siwc/token-sharing-open-source/*, read 2026-11-20):
// dynamic registration with client_id=dynamic_agent_client, stable ext_agent_host_id, PKCE + nonce + state,
// loopback redirect http://127.0.0.1:<port>/auth/callback, ID-token validation against OpenAI JWKS,
// rotating refresh tokens, and revocation on sign-out. Tokens never reach the renderer.
import { randomUUID } from "node:crypto";
import { createRemoteJWKSet, jwtVerify, type JWTVerifyGetKey } from "jose";
import { pkce, randomToken, startLoopback } from "../oauth-loopback";
import type { FetchLike } from "./providers";
import type { Vault } from "../vault";

export const SIWC = {
  issuer: "https://auth.openai.com",
  authorize: "https://auth.openai.com/api/accounts/authorize",
  token: "https://auth.openai.com/api/accounts/oauth/token",
  discovery: "https://auth.openai.com/.well-known/openid-configuration",
  resource: "https://api.openai.com/v1",
  scopes: "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct",
  sharingScope: "chatgpt.tokens.use.direct",
  defaultPort: 1455,
  callbackPath: "/auth/callback",
  agentName: "VITALIS",
  usageUrl: "https://chatgpt.com/settings/usage",
};

export interface SiwcRecord {
  email?: string;
  issuer: string;
  subject: string;
  client_id: string;
  ext_agent_host_id: string;
  id_token?: string;
  access_token?: string;
  refresh_token?: string;
  token_type?: string;
  expires_at?: number; // epoch ms
  scopes: string[];
  saved_at: string;
  welcomed?: boolean;
}

export interface SiwcDeps {
  vault: Vault;
  fetch: FetchLike;
  openExternal: (url: string) => Promise<void>;
  hostIdStore: { get(): string | null; set(v: string): void };
  endpoints?: Partial<typeof SIWC>;
  jwks?: JWTVerifyGetKey; // injectable for tests
}

export class SiwcClient {
  private cfg: typeof SIWC;
  private refreshing: Promise<string> | null = null;
  private pendingCancel: (() => void) | null = null;

  constructor(private deps: SiwcDeps) {
    this.cfg = { ...SIWC, ...(deps.endpoints ?? {}) };
  }

  hostId(): string {
    let h = this.deps.hostIdStore.get();
    if (!h) {
      h = `urn:uuid:${randomUUID()}`; // opaque, not user-identifying; persisted before first sign-in
      this.deps.hostIdStore.set(h);
    }
    return h;
  }

  record(): SiwcRecord | null {
    return this.deps.vault.get<SiwcRecord>("siwc");
  }

  status() {
    const r = this.record();
    const connected = !!(r?.access_token && r.refresh_token);
    return { connected, email: r?.email ?? null, sharing: !!(connected && r?.scopes.includes(this.cfg.sharingScope)),
      expiresAt: r?.expires_at ?? null, welcomed: !!r?.welcomed, hasRegistration: !!r?.client_id,
      usageUrl: this.cfg.usageUrl };
  }

  markWelcomed() {
    const r = this.record();
    if (r) this.deps.vault.set("siwc", { ...r, welcomed: true });
  }

  cancel() {
    this.pendingCancel?.();
  }

  async signIn(opts: { reconsent?: boolean } = {}): Promise<ReturnType<SiwcClient["status"]>> {
    const prev = this.record();
    const hostId = this.hostId();
    const state = randomToken();
    const nonce = randomToken();
    const { verifier, challenge } = pkce();
    const lb = await startLoopback({ port: this.cfg.defaultPort, path: this.cfg.callbackPath, expectedState: state,
      fallbackToEphemeral: true });
    this.pendingCancel = lb.close;
    const clientId = prev?.client_id ?? "dynamic_agent_client";
    const q = new URLSearchParams({ client_id: clientId, ext_agent_host_id: hostId, response_type: "code",
      redirect_uri: lb.redirectUri, scope: this.cfg.scopes, resource: this.cfg.resource, state, nonce,
      code_challenge_method: "S256", code_challenge: challenge });
    if (!prev?.client_id) q.set("agent_name_hint", this.cfg.agentName);
    if (prev?.id_token) q.set("id_token_hint", prev.id_token);
    if (prev?.email) q.set("login_hint", prev.email);
    if (opts.reconsent) q.set("prompt", "consent");
    await this.deps.openExternal(`${this.cfg.authorize}?${q.toString()}`);
    const { params } = await lb.result;
    this.pendingCancel = null;
    if (params.get("error")) {
      throw Object.assign(new Error(params.get("error") === "access_denied" ? "Sign-in was declined." :
        `Sign-in failed: ${params.get("error")}`), { code: params.get("error") });
    }
    const code = params.get("code");
    const cbClient = params.get("client_id");
    let issued = clientId;
    if (clientId === "dynamic_agent_client") {
      if (!cbClient) throw Object.assign(new Error("Registration incomplete: no client ID returned."), { code: "registration_incomplete" });
      issued = cbClient;
    } else if (cbClient && cbClient !== clientId) {
      throw Object.assign(new Error("The callback returned a different client registration."), { code: "client_mismatch" });
    }
    if (!code) throw Object.assign(new Error("No authorization code returned."), { code: "no_code" });
    const tok = await this.tokenRequest({ grant_type: "authorization_code", client_id: issued, code, code_verifier: verifier,
      redirect_uri: lb.redirectUri, resource: this.cfg.resource });
    const claims = await this.verifyIdToken(tok.id_token, issued, nonce);
    if (prev?.subject && prev.subject !== claims.sub) {
      throw Object.assign(new Error("Signed in to a different ChatGPT account than the saved one."), { code: "account_mismatch" });
    }
    const scopes = String(tok.scope ?? params.get("scope") ?? "").split(/[\s+]+/).filter(Boolean);
    const rec: SiwcRecord = { email: (claims.email as string) ?? prev?.email, issuer: String(claims.iss), subject: String(claims.sub),
      client_id: issued, ext_agent_host_id: hostId, id_token: tok.id_token, access_token: tok.access_token,
      refresh_token: tok.refresh_token, token_type: tok.token_type, expires_at: Date.now() + Number(tok.expires_in ?? 3600) * 1000,
      scopes, saved_at: new Date().toISOString(), welcomed: prev?.welcomed };
    this.deps.vault.set("siwc", rec);
    return this.status();
  }

  private async tokenRequest(form: Record<string, string>): Promise<any> {
    const res = await this.deps.fetch(this.cfg.token, { method: "POST",
      headers: { "content-type": "application/x-www-form-urlencoded" }, body: new URLSearchParams(form).toString() });
    const body: any = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw Object.assign(new Error(body?.error_description ?? body?.error ?? `token endpoint ${res.status}`),
        { code: body?.error ?? `http_${res.status}` });
    }
    return body;
  }

  private async verifyIdToken(idToken: string, clientId: string, nonce: string) {
    if (!idToken) throw Object.assign(new Error("No ID token returned."), { code: "no_id_token" });
    let jwks = this.deps.jwks;
    if (!jwks) {
      const disc: any = await (await this.deps.fetch(this.cfg.discovery, { method: "GET" })).json();
      jwks = createRemoteJWKSet(new URL(disc.jwks_uri));
    }
    const { payload } = await jwtVerify(idToken, jwks, { issuer: this.cfg.issuer, audience: clientId });
    if (payload.nonce !== nonce) throw Object.assign(new Error("ID token nonce mismatch."), { code: "nonce_mismatch" });
    return payload;
  }

  /** Access token for inference; refreshes (serialized) when it expires within 60 s. */
  async accessToken(): Promise<string> {
    const r = this.record();
    if (!r?.access_token || !r.refresh_token) throw Object.assign(new Error("Sign in with ChatGPT first."), { code: "sign_in_required" });
    if (!r.scopes.includes(this.cfg.sharingScope)) {
      throw Object.assign(new Error("ChatGPT plan usage is not enabled for this sign-in."), { code: "sharing_not_enabled" });
    }
    if ((r.expires_at ?? 0) - Date.now() > 60_000) return r.access_token;
    if (!this.refreshing) {
      this.refreshing = (async () => {
        try {
          const tok = await this.tokenRequest({ grant_type: "refresh_token", client_id: r.client_id,
            refresh_token: r.refresh_token!, resource: this.cfg.resource });
          const scopes = tok.scope ? String(tok.scope).split(/\s+/) : r.scopes;
          this.deps.vault.set("siwc", { ...r, access_token: tok.access_token, refresh_token: tok.refresh_token ?? r.refresh_token,
            expires_at: Date.now() + Number(tok.expires_in ?? 3600) * 1000, scopes, saved_at: new Date().toISOString() });
          return tok.access_token as string;
        } catch (e: any) {
          if (["invalid_grant", "invalid_refresh_token", "token_expired", "refresh_token_expired",
            "refresh_token_invalidated", "refresh_token_reused"].includes(e.code)) {
            this.deps.vault.set("siwc", { ...r, access_token: undefined, refresh_token: undefined });
            throw Object.assign(new Error("Your ChatGPT session expired. Please continue with ChatGPT again."), { code: "reauth_required" });
          }
          throw e;
        } finally {
          this.refreshing = null;
        }
      })();
    }
    return this.refreshing;
  }

  /** Sign out: revoke the renewable session, then clear tokens (keep client registration + host ID). */
  async signOut(): Promise<{ revoked: boolean; message?: string }> {
    const r = this.record();
    if (!r) return { revoked: true };
    let revoked = false;
    if (r.refresh_token) {
      try {
        const disc: any = await (await this.deps.fetch(this.cfg.discovery, { method: "GET" })).json();
        for (let attempt = 0; attempt < 3 && !revoked; attempt++) {
          const res = await this.deps.fetch(disc.revocation_endpoint, { method: "POST",
            headers: { "content-type": "application/x-www-form-urlencoded" },
            body: new URLSearchParams({ token: r.refresh_token, token_type_hint: "refresh_token", client_id: r.client_id }).toString() });
          if (res.status === 200) revoked = true;
          else if (res.status < 500) break;
          else await new Promise((s) => setTimeout(s, 500 * 2 ** attempt));
        }
      } catch { /* network failure: revocation not confirmed */ }
    }
    this.deps.vault.set("siwc", { ...r, access_token: undefined, refresh_token: undefined, id_token: r.id_token });
    return revoked ? { revoked } : { revoked, message: "Signed out locally, but remote revocation was not confirmed. "
      + "You can disconnect VITALIS in ChatGPT Settings." };
  }

  async listModels(): Promise<{ id: string; label: string }[]> {
    const token = await this.accessToken();
    const res = await this.deps.fetch(`${this.cfg.resource}/models`, { method: "GET", headers: { authorization: `Bearer ${token}` } });
    if (!res.ok) throw Object.assign(new Error(`Model list failed (${res.status})`), { code: `http_${res.status}` });
    const body: any = await res.json();
    return (body.models ?? []).filter((m: any) => m.visibility === "list").map((m: any) => ({ id: m.slug, label: m.display_name ?? m.slug }));
  }
}
