import { createHash, randomBytes } from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { exportJWK, generateKeyPair, SignJWT, createLocalJWKSet } from "jose";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { EngineCallSchema, LocalAiSchema } from "../../shared/contract";
import { Orchestrator } from "../../electron/ai/orchestrator";
import { chatCompletionsProvider, responsesProvider } from "../../electron/ai/providers";
import { parseSse } from "../../electron/ai/sse";
import { SiwcClient } from "../../electron/ai/siwc";
import { EngineBridge, resolveEngineCommand } from "../../electron/engine";
import { GoogleHealthClient } from "../../electron/google/health";
import { insecureTestCipher, ProfileStore, Vault, type Cipher } from "../../electron/vault";
import { EXPLAIN, type Explanation } from "../../src/explain/content";

const tmp = () => fs.mkdtempSync(path.join(os.tmpdir(), "vit-unit-"));
const engineDir = path.resolve(__dirname, "..", "..", "..", "..", "services", "engine");
// The engine interpreter: VITALIS_PYTHON (CI uses the bundled Windows runtime), else the development virtualenv.
const py = process.env.VITALIS_PYTHON
  ?? (process.platform === "win32" ? path.join(engineDir, ".venv", "Scripts", "python.exe") : path.join(engineDir, ".venv", "bin", "python"));

function streamOf(text: string) {
  return new ReadableStream<Uint8Array>({ start(c) { for (let i = 0; i < text.length; i += 7) c.enqueue(new TextEncoder().encode(text.slice(i, i + 7))); c.close(); } });
}

describe("IPC contract", () => {
  it("rejects engine methods that are not renderer-callable", () => {
    expect(EngineCallSchema.safeParse({ method: "system.status" }).success).toBe(true);
    for (const m of ["ai.call", "backup.restore_to", "export.all", "import.commit", "data.wipe", "sync.start"]) {
      expect(EngineCallSchema.safeParse({ method: m }).success).toBe(false);
    }
  });
  it("local AI must be loopback", () => {
    expect(LocalAiSchema.safeParse({ baseUrl: "http://127.0.0.1:11434/v1", enabled: true }).success).toBe(true);
    expect(LocalAiSchema.safeParse({ baseUrl: "http://192.168.1.5:11434/v1", enabled: true }).success).toBe(false);
    expect(LocalAiSchema.safeParse({ baseUrl: "https://evil.example/v1", enabled: true }).success).toBe(false);
  });
});

describe("Vault & profiles", () => {
  it("refuses to store secrets when OS protection is unavailable", () => {
    const off: Cipher = { available: () => false, encrypt: () => Buffer.alloc(0), decrypt: () => "", backend: () => "none" };
    const v = new Vault(tmp(), off);
    expect(() => v.set("x", 1)).toThrow(/OS-protected storage/);
  });
  it("creates profiles with wrapped random keys and deletes them completely", () => {
    const dir = tmp();
    const v = new Vault(path.join(dir, "secrets"), insecureTestCipher());
    const ps = new ProfileStore(dir, v);
    const p = ps.create("Me", false, "Europe/Zagreb");
    expect(ps.key(p.id)).toMatch(/^[0-9a-f]{64}$/);
    fs.writeFileSync(p.dbFile, "dummy");
    ps.delete(p.id);
    expect(fs.existsSync(p.dbFile)).toBe(false);
    expect(() => ps.key(p.id)).toThrow();
    expect(ps.list()).toHaveLength(0);
  });
});

describe("Engine command", () => {
  it("an installed app always runs its bundled runtime in isolated mode, ignoring VITALIS_PYTHON", () => {
    const saved = process.env.VITALIS_PYTHON;
    process.env.VITALIS_PYTHON = "/somewhere/else/python";
    try {
      const c = resolveEngineCommand({ isPackaged: true, resourcesPath: path.join("R", "resources"), appPath: "A" });
      expect(c.command.startsWith(path.join("R", "resources", "engine", "python"))).toBe(true);
      expect(c.args).toEqual(["-I", "-m", "vitalis_engine"]);
      expect(resolveEngineCommand({ isPackaged: false, resourcesPath: "R", appPath: "A" }).command).toBe("/somewhere/else/python");
    } finally {
      if (saved === undefined) delete process.env.VITALIS_PYTHON; else process.env.VITALIS_PYTHON = saved;
    }
  });
});

describe("Explain buttons", () => {
  const pagesDir = path.resolve(__dirname, "..", "..", "src", "pages");
  const pages = ["Overview", "SleepLab", "Recovery", "Stress", "Cardio", "Activity", "Trends", "Journal", "Lab", "Reports"];
  const sources = pages.map((p) => fs.readFileSync(path.join(pagesDir, `${p}.tsx`), "utf8"));
  it("every card on the analysis screens has an Explain entry", () => {
    pages.forEach((p, i) => {
      const cards = (sources[i].match(/<Card[\s>]/g) ?? []).length;
      const explained = (sources[i].match(/\bexplain=/g) ?? []).length;
      expect(cards, p).toBeGreaterThan(0);
      expect(explained, `${p}: cards without Explain`).toBe(cards);
    });
  });
  it("every entry is complete and used by a screen", () => {
    const all = sources.join("\n");
    for (const [id, e] of Object.entries(EXPLAIN) as [string, Explanation][]) {
      expect(e.what.length, id).toBeGreaterThan(10);
      expect(e.how.length && e.read.length && e.limits.length, id).toBeTruthy();
      expect(e.ask.length, id).toBeGreaterThan(10);
      expect(all.includes(`"${id}"`), `${id} is not used by any screen`).toBe(true);
    }
  });
});

describe("EngineBridge with the real Python engine", () => {
  let eng: EngineBridge;
  beforeAll(async () => {
    eng = new EngineBridge({ command: py, args: ["-m", "vitalis_engine"], cwd: engineDir, env: { ...process.env } });
    await eng.start({ dbPath: path.join(tmp(), "e.db"), keyHex: randomBytes(32).toString("hex"), demo: true, tz: "Europe/Zagreb", profileName: "t" });
  }, 60000);
  afterAll(async () => { await eng.stop(); });
  it("round-trips calls and surfaces validation errors", async () => {
    const st = await eng.call<any>("system.status");
    expect(st.demo).toBe(true);
    await expect(eng.call("features.series", { start: "x" })).rejects.toMatchObject({ code: "invalid_params" });
    await expect(eng.call("no.such")).rejects.toMatchObject({ code: "method_not_found" });
  });
  it("Explain entries name only metrics the engine knows; explain.data answers", async () => {
    const cat = await eng.call<any[]>("features.catalog");
    const known = new Set(cat.map((c) => c.key));
    for (const [id, e] of Object.entries(EXPLAIN) as [string, Explanation][]) {
      for (const f of e.features ?? []) expect(known.has(f), `${id}: unknown feature ${f}`).toBe(true);
    }
    const r = await eng.call<any>("explain.data", { date: "2026-11-19", features: ["rhr", "sleep_tst_min"], scores: ["recovery"] });
    expect(r.features.map((f: any) => f.key)).toEqual(["rhr", "sleep_tst_min"]);
    expect(["insufficient", "provisional", "stable"]).toContain(r.features[0].baseline.status);
    await expect(eng.call("explain.data", { date: "2026-11-19", features: ["nope"] })).rejects.toMatchObject({ code: "invalid_params" });
  });
  it("handles concurrent calls", async () => {
    const r = await Promise.all(Array.from({ length: 10 }, () => eng.call<any>("prefs.get")));
    expect(r.every((x) => x.sleep_need_min === 450)).toBe(true);
  });
});

describe("SSE and providers", () => {
  it("parses split SSE frames", async () => {
    const evs: any[] = [];
    for await (const e of parseSse(streamOf('event: a\ndata: {"x":1}\n\n: comment\ndata: {"y"\ndata: :2}\n\n'))) evs.push(e);
    expect(evs).toEqual([{ event: "a", data: '{"x":1}' }, { event: null, data: '{"y"\n:2}' }]);
  });
  it("Responses provider: function calls, deltas, errors", async () => {
    const sse = [
      { type: "response.output_text.delta", delta: "Hi " },
      { type: "response.output_item.done", item: { type: "function_call", call_id: "c1", name: "get_baseline", arguments: '{"metric":"rhr","date":"2026-11-01"}' } },
      { type: "response.completed" },
    ].map((o) => `event: ${o.type}\ndata: ${JSON.stringify(o)}\n\n`).join("");
    let sent: any = null;
    const p = responsesProvider({ baseUrl: "http://x/v1", token: async () => "tok", fetch: async (_u, init) => { sent = init; return new Response(streamOf(sse), { status: 200 }); } });
    const conv = p.startConversation("sys", [{ role: "user", content: "earlier" }], "q");
    const body = p.buildBody(conv, "m", [{ name: "get_baseline", description: "d", parameters: { type: "object" } }]);
    expect(body).toMatchObject({ store: false, stream: true, instructions: "sys" });
    expect(body).not.toHaveProperty("temperature");
    expect(body).not.toHaveProperty("previous_response_id");
    const deltas: string[] = [];
    const r = await p.send(body, (d) => deltas.push(d), new AbortController().signal);
    expect(sent.headers.authorization).toBe("Bearer tok");
    expect(r.toolCalls[0]).toEqual({ callId: "c1", name: "get_baseline", arguments: '{"metric":"rhr","date":"2026-11-01"}' });
    p.appendRound(conv, r, [{ callId: "c1", output: "{}" }]);
    expect(conv.items.at(-1)).toEqual({ type: "function_call_output", call_id: "c1", output: "{}" });
    const fail = responsesProvider({ baseUrl: "http://x", token: async () => "t", fetch: async () => new Response(JSON.stringify({ error: { code: "subscription_sharing_usage_limit_exceeded", message: "x" } }), { status: 429 }) });
    await expect(fail.send({}, () => undefined, new AbortController().signal)).rejects.toMatchObject({ code: "subscription_sharing_usage_limit_exceeded" });
    const cut = responsesProvider({ baseUrl: "http://x", token: async () => "t", fetch: async () => new Response(streamOf('data: {"type":"response.output_text.delta","delta":"a"}\n\n'), { status: 200 }) });
    await expect(cut.send({}, () => undefined, new AbortController().signal)).rejects.toMatchObject({ code: "stream_interrupted" });
  });
  it("Chat Completions provider accumulates streamed tool calls", async () => {
    const chunks = [{ choices: [{ delta: { tool_calls: [{ index: 0, id: "t1", function: { name: "get_", arguments: '{"a"' } }] } }] },
      { choices: [{ delta: { tool_calls: [{ index: 0, function: { name: "baseline", arguments: ":1}" } }] } }] }];
    const sse = chunks.map((c) => `data: ${JSON.stringify(c)}\n\n`).join("") + "data: [DONE]\n\n";
    const p = chatCompletionsProvider({ baseUrl: "http://127.0.0.1:1/v1", fetch: async () => new Response(streamOf(sse), { status: 200 }) });
    const r = await p.send({}, () => undefined, new AbortController().signal);
    expect(r.toolCalls).toEqual([{ callId: "t1", name: "get_baseline", arguments: '{"a":1}' }]);
  });
});

describe("Orchestrator", () => {
  it("waits for consent per round, runs validated tools, stores evidence; nothing is sent if declined", async () => {
    const calls: string[] = [];
    const engine = { call: async (m: string, p?: any): Promise<any> => {
      calls.push(m);
      if (m === "ai.system_prompt") return { prompt: "SYS" };
      if (m === "ai.tools") return [{ name: "get_metric_history", description: "d", parameters: {} }];
      if (m === "chat.messages") return [];
      if (m === "ai.call") return { ok: true, result: { summary: { n: 3 }, evidence: [{ id: "rhr|a|b", kind: "feature_series", metric: "rhr" }] } };
      if (m === "chat.append") return { id: "msg1" };
      return {};
    } };
    let round = 0;
    const provider = { kind: "responses" as const, startConversation: () => ({ system: "SYS", items: [] as unknown[] }),
      buildBody: () => ({ round }), appendRound: () => undefined,
      send: async () => (++round === 1 ? { text: "", toolCalls: [{ callId: "c", name: "get_metric_history", arguments: "{}" }], raw: [] }
        : { text: "Answer [[ev:rhr|a|b]]", toolCalls: [], raw: [] }) };
    const orch = new Orchestrator(engine, async () => provider);
    const events: any[] = [];
    const done = new Promise<void>((resolve) => {
      orch.ask({ threadId: "t", message: "q", provider: "openai_api", model: "m" }, (e) => {
        events.push(e);
        if (e.type === "consent") setTimeout(() => orch.decide(e.requestId, true, false), 5);
        if (e.type === "done" || e.type === "error") resolve();
      });
    });
    await done;
    expect(events.filter((e) => e.type === "consent")).toHaveLength(2);
    const fin = events.find((e) => e.type === "done");
    expect(fin.evidence[0].id).toBe("rhr|a|b");
    expect(fin.unverifiedNumbers).toBe(false);
    expect(calls.filter((c) => c === "ai.disclosure_log")).toHaveLength(2);

    round = 0;
    const ev2: any[] = [];
    await new Promise<void>((resolve) => {
      orch.ask({ threadId: "t", message: "q", provider: "chatgpt", model: "m" }, (e) => {
        ev2.push(e);
        if (e.type === "consent") orch.decide(e.requestId, false, false);
        if (e.type === "error" || e.type === "done") resolve();
      });
    });
    expect(ev2.at(-1)).toMatchObject({ type: "error", code: "declined" });
    expect(round).toBe(0);
  });
});

// ---------------------------------------------------------------------------------------- OAuth mocks
async function oauthServer(handler: (req: http.IncomingMessage, body: string, res: http.ServerResponse) => void) {
  const server = http.createServer((req, res) => { let b = ""; req.on("data", (c) => (b += c)); req.on("end", () => handler(req, b, res)); });
  await new Promise<void>((r) => server.listen(0, "127.0.0.1", () => r()));
  return { server, url: `http://127.0.0.1:${(server.address() as any).port}` };
}

describe("Sign in with ChatGPT (documented protocol, mock authorization server)", () => {
  it("registers dynamically, validates ID token + nonce, stores tokens, refreshes, revokes", async () => {
    const { privateKey, publicKey } = await generateKeyPair("RS256");
    const jwk = { ...(await exportJWK(publicKey)), kid: "k1", alg: "RS256" };
    const jwks = createLocalJWKSet({ keys: [jwk] });
    let nonce = "";
    let tokenForms: URLSearchParams[] = [];
    let revoked: URLSearchParams | null = null;
    const { server, url } = await oauthServer(async (req, body, res) => {
      if (req.url === "/.well-known/openid-configuration") { res.end(JSON.stringify({ revocation_endpoint: `${url}/revoke`, jwks_uri: `${url}/jwks` })); return; }
      if (req.url === "/revoke") { revoked = new URLSearchParams(body); res.writeHead(200).end(); return; }
      if (req.url === "/token") {
        const f = new URLSearchParams(body); tokenForms.push(f);
        if (f.get("grant_type") === "authorization_code") {
          const idt = await new SignJWT({ email: "me@example.com", nonce }).setProtectedHeader({ alg: "RS256", kid: "k1" })
            .setIssuer("https://auth.openai.com").setAudience("oaiapp_123").setSubject("user-1").setExpirationTime("1h").setIssuedAt().sign(privateKey);
          res.end(JSON.stringify({ access_token: "at1", refresh_token: "rt1", id_token: idt, token_type: "Bearer", expires_in: 3600,
            scope: "chatgpt.tokens.use.direct email offline_access openid profile resource.invoke" }));
        } else res.end(JSON.stringify({ access_token: "at2", refresh_token: "rt2", expires_in: 3600 }));
        return;
      }
      res.writeHead(404).end();
    });
    const dir = tmp();
    const vault = new Vault(dir, insecureTestCipher());
    let host: string | null = null;
    const client = new SiwcClient({ vault, fetch: (u, i) => fetch(u, i), jwks,
      hostIdStore: { get: () => host, set: (v) => { host = v; } },
      endpoints: { token: `${url}/token`, discovery: `${url}/.well-known/openid-configuration`, authorize: `${url}/authorize`, defaultPort: 0 },
      openExternal: async (authUrl) => {
        const u = new URL(authUrl);
        expect(u.searchParams.get("client_id")).toBe("dynamic_agent_client");
        expect(u.searchParams.get("agent_name_hint")).toBe("VITALIS");
        expect(u.searchParams.get("ext_agent_host_id")).toMatch(/^urn:uuid:/);
        expect(u.searchParams.get("code_challenge_method")).toBe("S256");
        expect(u.searchParams.get("resource")).toBe("https://api.openai.com/v1");
        expect(u.searchParams.get("scope")).toContain("chatgpt.tokens.use.direct");
        const redirect = new URL(u.searchParams.get("redirect_uri")!);
        expect(redirect.hostname).toBe("127.0.0.1");
        expect(redirect.pathname).toBe("/auth/callback");
        nonce = u.searchParams.get("nonce")!;
        setTimeout(() => fetch(`${redirect}?code=abc&state=${u.searchParams.get("state")}&client_id=oaiapp_123&scope=chatgpt.tokens.use.direct`), 10);
      } });
    const st = await client.signIn();
    expect(st).toMatchObject({ connected: true, sharing: true, email: "me@example.com" });
    const verifier = tokenForms[0].get("code_verifier")!;
    expect(tokenForms[0].get("client_id")).toBe("oaiapp_123");
    expect(createHash("sha256").update(verifier).digest("base64url")).toBeTruthy();
    expect(JSON.stringify(fs.readdirSync(dir))).toContain("siwc.bin");
    expect(await client.accessToken()).toBe("at1");
    vault.set("siwc", { ...vault.get<any>("siwc"), expires_at: Date.now() + 1000 });
    const [a, b] = await Promise.all([client.accessToken(), client.accessToken()]);
    expect(a).toBe("at2"); expect(b).toBe("at2");
    expect(tokenForms.filter((f) => f.get("grant_type") === "refresh_token")).toHaveLength(1); // serialized refresh
    expect(tokenForms.at(-1)!.get("client_id")).toBe("oaiapp_123");
    const out = await client.signOut();
    expect(out.revoked).toBe(true);
    expect(revoked!.get("token")).toBe("rt2");
    expect(revoked!.get("token_type_hint")).toBe("refresh_token");
    expect(client.status().connected).toBe(false);
    expect(client.status().hasRegistration).toBe(true); // client registration retained for the next sign-in
    server.close();
  });
  it("rejects an ID token with the wrong nonce", async () => {
    const { privateKey, publicKey } = await generateKeyPair("RS256");
    const jwks = createLocalJWKSet({ keys: [{ ...(await exportJWK(publicKey)), kid: "k", alg: "RS256" }] });
    const { server, url } = await oauthServer(async (req, _b, res) => {
      const idt = await new SignJWT({ nonce: "WRONG" }).setProtectedHeader({ alg: "RS256", kid: "k" }).setIssuer("https://auth.openai.com")
        .setAudience("oaiapp_9").setSubject("s").setExpirationTime("1h").sign(privateKey);
      res.end(JSON.stringify({ access_token: "a", refresh_token: "r", id_token: idt, expires_in: 3600, scope: "chatgpt.tokens.use.direct" }));
    });
    let host: string | null = null;
    const client = new SiwcClient({ vault: new Vault(tmp(), insecureTestCipher()), fetch: (u, i) => fetch(u, i), jwks,
      hostIdStore: { get: () => host, set: (v) => { host = v; } }, endpoints: { token: `${url}/token`, authorize: `${url}/a`, defaultPort: 0 },
      openExternal: async (authUrl) => { const u = new URL(authUrl); setTimeout(() => fetch(`${u.searchParams.get("redirect_uri")}?code=c&state=${u.searchParams.get("state")}&client_id=oaiapp_9`), 10); } });
    await expect(client.signIn()).rejects.toMatchObject({ code: "nonce_mismatch" });
    server.close();
  });
});

describe("Google Health connector (mock API)", () => {
  it("paginates, retries 429 with Retry-After, imports per type, and handles invalid_grant", async () => {
    let pages = 0;
    let throttled = false;
    const { server, url } = await oauthServer((req, body, res) => {
      if (req.url!.startsWith("/token")) {
        const f = new URLSearchParams(body);
        if (f.get("refresh_token") === "dead") { res.writeHead(400).end(JSON.stringify({ error: "invalid_grant" })); return; }
        res.end(JSON.stringify({ access_token: "ga2", expires_in: 3600 })); return;
      }
      if (req.url!.includes("/dataTypes/steps/")) {
        if (!throttled) { throttled = true; res.writeHead(429, { "retry-after": "0" }).end("{}"); return; }
        pages++;
        const tok = new URL(req.url!, url).searchParams.get("pageToken");
        res.end(JSON.stringify({ dataPoints: [{ steps: { count: "10" } }], nextPageToken: tok ? "" : "p2" }));
        return;
      }
      res.end(JSON.stringify({ dataPoints: [] }));
    });
    const vault = new Vault(tmp(), insecureTestCipher());
    vault.set("google_client", { clientId: "x.apps.googleusercontent.com", clientSecret: "" });
    vault.set("google_tokens", { access_token: "ga1", refresh_token: "r1", expires_at: Date.now() + 3600_000, scope: "" });
    const imported: any[] = [];
    const engine = { call: async (m: string, p?: any): Promise<any> => {
      if (m === "import.google_filter") return { types: ["steps", "sleep"], scopes: [] };
      if (m === "import.google_filter_for") return { filter: "f" };
      if (m === "import.google_health") { imported.push(p); return { inserted: p.records.length }; }
    } };
    const g = new GoogleHealthClient({ vault, fetch: (u, i) => fetch(u, i), openExternal: async () => undefined, engine, endpoints: { api: url, token: `${url}/token` }, sleep: async () => undefined });
    const out = await g.sync(30);
    expect(pages).toBe(2);
    expect(imported[0].records).toHaveLength(2);
    expect(out.errors).toEqual({});
    expect(Object.keys(vault.get<any>("google_cursors"))).toEqual(["steps", "sleep"]);
    vault.set("google_tokens", { access_token: "x", refresh_token: "dead", expires_at: 0, scope: "" });
    const out2 = await g.sync(30);
    expect(Object.values(out2.errors)[0]).toMatch(/7 days/);
    expect(vault.get("google_tokens")).toBeNull();
    server.close();
  });
});
