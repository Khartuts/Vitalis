import { app, BrowserWindow, dialog, ipcMain, net, safeStorage, session, shell, type IpcMainInvokeEvent, type WebContents } from "electron";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import QRCode from "qrcode";
import { z } from "zod";
import {
  AiDecisionSchema, AiProviderSchema, AiSendSchema, ApiKeySchema, CsvExportSchema, EngineCallSchema, EXTERNAL_URL_ALLOWLIST,
  ExternalUrlSchema, GoogleConfigSchema, GoogleSyncSchema, IdSchema, ImportCommitSchema, ImportKindSchema, LocalAiSchema,
  PassphraseSchema, ProfileCreateSchema, RangeSchema, type AiProvider,
} from "../shared/contract";
import { Orchestrator } from "./ai/orchestrator";
import { chatCompletionsProvider, responsesProvider, type FetchLike } from "./ai/providers";
import { SIWC, SiwcClient } from "./ai/siwc";
import { EngineBridge, resolveEngineCommand } from "./engine";
import { GoogleHealthClient } from "./google/health";
import { electronCipher, insecureTestCipher, ProfileStore, Vault, type Profile } from "./vault";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEV_URL = process.env.VITE_DEV_SERVER_URL;
const TEST = !app.isPackaged && process.env.VITALIS_TEST === "1";
if (process.env.VITALIS_USER_DATA) app.setPath("userData", process.env.VITALIS_USER_DATA);

const userData = app.getPath("userData");
let mainWindow: BrowserWindow | null = null;
let vault: Vault;
let profiles: ProfileStore;
let engine: EngineBridge | null = null;
let activeProfile: Profile | null = null;
let orchestrator: Orchestrator;
let siwc: SiwcClient;
let google: GoogleHealthClient;
const importTokens = new Map<string, { kind: string; path: string }>();

const fetchImpl: FetchLike = (url, init) => (TEST ? fetch(url, init) : net.fetch(url, init as any));
const openExternal = async (url: string) => {
  if (TEST && process.env.VITALIS_TEST_OAUTH_FOLLOW === "1") { await fetch(url).catch(() => undefined); return; }
  await shell.openExternal(url);
};

const settingsFile = path.join(userData, "settings.json");
const readSettings = (): Record<string, any> => (fs.existsSync(settingsFile) ? JSON.parse(fs.readFileSync(settingsFile, "utf8")) : {});
const writeSettings = (s: Record<string, any>) => fs.writeFileSync(settingsFile, JSON.stringify(s, null, 2));

function engineOrThrow(): EngineBridge {
  if (!engine?.ready) throw new Error("No profile is open.");
  return engine;
}

function send(channel: string, payload: unknown) {
  for (const w of BrowserWindow.getAllWindows()) if (!w.isDestroyed()) w.webContents.send(channel, payload);
}

async function openProfile(p: Profile) {
  if (engine) await engine.stop();
  const cmd = resolveEngineCommand({ isPackaged: app.isPackaged, resourcesPath: process.resourcesPath, appPath: app.getAppPath() });
  engine = new EngineBridge(cmd);
  engine.on("event", (ev) => send("engine:event", ev));
  engine.on("exit", (info) => send("engine:event", { type: "engine_exit", ...info }));
  await engine.start({ dbPath: p.dbFile, keyHex: profiles.key(p.id), demo: p.demo, tz: p.timezone, profileName: p.name });
  activeProfile = p;
  const s = readSettings();
  writeSettings({ ...s, lastProfile: p.id });
  return p;
}

// ------------------------------------------------------------------------------------------- security
function isTrustedSender(e: IpcMainInvokeEvent) {
  const url = e.senderFrame?.url ?? "";
  return DEV_URL ? url.startsWith(DEV_URL) : url.startsWith("file://");
}

function handle<T extends z.ZodTypeAny>(channel: string, schema: T | null, fn: (args: z.infer<T>, e: IpcMainInvokeEvent) => unknown) {
  ipcMain.handle(channel, async (e, raw) => {
    if (!isTrustedSender(e)) throw new Error("untrusted sender");
    const args = schema ? schema.parse(raw ?? {}) : undefined;
    try {
      return { ok: true, data: await fn(args as z.infer<T>, e) };
    } catch (err: any) {
      return { ok: false, error: { code: err?.code ?? "error", message: String(err?.message ?? err) } };
    }
  });
}

function hardenSession() {
  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
  session.defaultSession.setPermissionCheckHandler(() => false);
  if (!DEV_URL) {
    session.defaultSession.webRequest.onHeadersReceived((details, cb) => {
      cb({ responseHeaders: { ...details.responseHeaders, "Content-Security-Policy": [
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; "
        + "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"] } });
    });
  }
  app.on("web-contents-created", (_e, wc: WebContents) => {
    wc.setWindowOpenHandler(() => ({ action: "deny" }));
    wc.on("will-navigate", (ev, url) => {
      if (!(DEV_URL ? url.startsWith(DEV_URL) : url.startsWith("file://"))) ev.preventDefault();
    });
    wc.on("will-attach-webview", (ev) => ev.preventDefault());
  });
}

function rendererUrl(hash = "") {
  return DEV_URL ? `${DEV_URL}${hash ? `#${hash}` : ""}` : null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1480, height: 940, minWidth: 1120, minHeight: 720, backgroundColor: "#0e1116", title: "VITALIS", show: false,
    autoHideMenuBar: true,
    webPreferences: { preload: path.join(__dirname, "preload.cjs"), contextIsolation: true, sandbox: true, nodeIntegration: false,
      spellcheck: false, webSecurity: true, devTools: !app.isPackaged },
  });
  mainWindow.once("ready-to-show", () => mainWindow?.show());
  const url = rendererUrl();
  if (url) mainWindow.loadURL(url);
  else mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
}

// ------------------------------------------------------------------------------------------- dialogs (test hooks)
async function pickOpen(opts: Electron.OpenDialogOptions): Promise<string | null> {
  if (TEST && process.env.VITALIS_TEST_OPEN_PATH) return process.env.VITALIS_TEST_OPEN_PATH;
  const r = await dialog.showOpenDialog(mainWindow!, opts);
  return r.canceled ? null : r.filePaths[0] ?? null;
}
async function pickSave(opts: Electron.SaveDialogOptions): Promise<string | null> {
  if (TEST && process.env.VITALIS_TEST_SAVE_DIR) return path.join(process.env.VITALIS_TEST_SAVE_DIR, opts.defaultPath ?? "out");
  const r = await dialog.showSaveDialog(mainWindow!, opts);
  return r.canceled ? null : r.filePath ?? null;
}

async function renderReportPdf(range: { start: string; end: string }): Promise<Buffer> {
  const win = new BrowserWindow({ show: false, width: 900, height: 1200, webPreferences: { preload: path.join(__dirname, "preload.cjs"),
    contextIsolation: true, sandbox: true, nodeIntegration: false } });
  const hash = `/print/report?start=${range.start}&end=${range.end}`;
  const ready = new Promise<void>((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("report rendering timed out")), 90000);
    ipcMain.once("report:ready", (e) => { if (e.sender.id === win.webContents.id) { clearTimeout(t); resolve(); } });
  });
  const url = rendererUrl(hash);
  if (url) await win.loadURL(url);
  else await win.loadFile(path.join(__dirname, "..", "dist", "index.html"), { hash });
  await ready;
  const pdf = await win.webContents.printToPDF({ pageSize: "A4", printBackground: true, margins: { top: 0.4, bottom: 0.4, left: 0.4, right: 0.4 } });
  win.destroy();
  return pdf;
}

// ------------------------------------------------------------------------------------------- IPC
function registerIpc() {
  handle("app:info", null, () => ({ version: app.getVersion(), platform: process.platform, packaged: app.isPackaged,
    keystore: vault.backend(), hostname: os.hostname(), activeProfile, test: TEST }));
  handle("app:openExternal", ExternalUrlSchema, async ({ url }) => {
    if (!EXTERNAL_URL_ALLOWLIST.some((p) => url.startsWith(p))) throw new Error("URL not allowed");
    await shell.openExternal(url);
  });

  handle("profiles:list", null, () => ({ profiles: profiles.list(), active: activeProfile, last: readSettings().lastProfile ?? null }));
  handle("profiles:create", ProfileCreateSchema, async (a) => {
    const p = profiles.create(a.name, a.demo, a.timezone);
    await openProfile(p);
    const prefs: Record<string, unknown> = { profile_name: a.name, display_timezone: a.timezone };
    if (a.birthYear) prefs.birth_year = a.birthYear;
    if (a.sleepNeedMin) prefs.sleep_need_min = a.sleepNeedMin;
    await engine!.call("prefs.set", { values: prefs });
    if (a.demo) await engine!.call("demo.load", { days: 120 }, 600000);
    return p;
  });
  handle("profiles:open", IdSchema, async ({ id }) => {
    const p = profiles.list().find((x) => x.id === id);
    if (!p) throw new Error("profile not found");
    return openProfile(p);
  });
  handle("profiles:rename", IdSchema.extend({ name: z.string().trim().min(1).max(60) }), ({ id, name }) => profiles.rename(id, name));
  handle("profiles:delete", IdSchema.extend({ confirm: z.literal("DELETE") }), async ({ id }) => {
    if (activeProfile?.id === id && engine) { await engine.stop(); engine = null; activeProfile = null; }
    profiles.delete(id);
    return { ok: true };
  });

  handle("data:wipe", z.object({ confirm: z.literal("DELETE ALL MY DATA") }), ({ confirm }) => engineOrThrow().call("data.wipe", { confirm }));
  handle("engine:call", EngineCallSchema, ({ method, params }) => engineOrThrow().call(method, params ?? {}));

  handle("sync:start", z.object({ port: z.number().int().min(1024).max(65535).optional() }), ({ port }) =>
    engineOrThrow().call("sync.start", port ? { port } : {}));
  handle("sync:stop", null, () => engineOrThrow().call("sync.stop"));
  handle("sync:pairing", null, async () => {
    const p = await engineOrThrow().call<any>("sync.pairing", { desktop_name: os.hostname().slice(0, 40) });
    return { ...p, qrDataUrl: await QRCode.toDataURL(p.qr_text, { errorCorrectionLevel: "M", margin: 1, width: 320 }), qr_text: undefined };
  });

  handle("import:pick", ImportKindSchema, async ({ kind }) => {
    const filters = kind === "samsung_export" ? [{ name: "Samsung Health export", extensions: ["zip", "csv"] }]
      : [{ name: "VITALIS sync file", extensions: ["vitalis-sync"] }];
    const file = await pickOpen({ properties: ["openFile"], filters });
    if (!file) return null;
    const preview = await engineOrThrow().call("import.preview", { kind, path: file });
    const token = Math.random().toString(36).slice(2);
    importTokens.set(token, { kind, path: file });
    return { token, preview, fileName: path.basename(file) };
  });
  handle("import:commit", ImportCommitSchema, async ({ token }) => {
    const t = importTokens.get(token);
    if (!t) throw new Error("import expired; choose the file again");
    importTokens.delete(token);
    return engineOrThrow().call("import.commit", { kind: t.kind, path: t.path }, 1800000);
  });

  handle("export:all", null, async () => {
    const dir = TEST && process.env.VITALIS_TEST_SAVE_DIR ? path.join(process.env.VITALIS_TEST_SAVE_DIR, "export")
      : (await dialog.showOpenDialog(mainWindow!, { properties: ["openDirectory", "createDirectory"] })).filePaths[0];
    if (!dir) return null;
    return engineOrThrow().call("export.all", { path: dir }, 600000);
  });
  handle("export:csv", CsvExportSchema, async ({ start, end, features }) => {
    const file = await pickSave({ defaultPath: `vitalis-features-${start}-${end}.csv`, filters: [{ name: "CSV", extensions: ["csv"] }] });
    if (!file) return null;
    return engineOrThrow().call("export.features_csv", { start, end, path: file, ...(features ? { features } : {}) });
  });
  handle("report:json", RangeSchema, async ({ start, end }) => {
    const file = await pickSave({ defaultPath: `vitalis-report-${start}-${end}.json`, filters: [{ name: "JSON", extensions: ["json"] }] });
    if (!file) return null;
    const rep = await engineOrThrow().call("report.build", { start, end });
    fs.writeFileSync(file, JSON.stringify(rep, null, 2));
    return { path: file };
  });
  handle("report:pdf", RangeSchema, async (range) => {
    const file = await pickSave({ defaultPath: `vitalis-report-${range.start}-${range.end}.pdf`, filters: [{ name: "PDF", extensions: ["pdf"] }] });
    if (!file) return null;
    const pdf = await renderReportPdf(range);
    fs.writeFileSync(file, pdf);
    return { path: file, bytes: pdf.length };
  });
  handle("chart:savePng", z.object({ dataUrl: z.string().startsWith("data:image/png;base64,").max(30_000_000),
    name: z.string().max(80).regex(/^[\w .-]+$/) }), async ({ dataUrl, name }) => {
    const file = await pickSave({ defaultPath: `${name}.png`, filters: [{ name: "PNG", extensions: ["png"] }] });
    if (!file) return null;
    fs.writeFileSync(file, Buffer.from(dataUrl.split(",")[1], "base64"));
    return { path: file };
  });

  handle("backup:create", PassphraseSchema, async ({ passphrase }) => {
    const file = await pickSave({ defaultPath: `vitalis-${new Date().toISOString().slice(0, 10)}.vitalis-backup`,
      filters: [{ name: "VITALIS backup", extensions: ["vitalis-backup"] }] });
    if (!file) return null;
    return engineOrThrow().call("backup.create", { path: file, passphrase }, 600000);
  });
  handle("backup:restore", PassphraseSchema.extend({ name: z.string().trim().min(1).max(60) }), async ({ passphrase, name }) => {
    const file = await pickOpen({ properties: ["openFile"], filters: [{ name: "VITALIS backup", extensions: ["vitalis-backup"] }] });
    if (!file) return null;
    const target = profiles.newDbPath();
    fs.mkdirSync(path.dirname(target.dbFile), { recursive: true });
    await engineOrThrow().call("backup.restore_to", { path: file, passphrase, target_path: target.dbFile, target_key: target.key }, 600000);
    const p = profiles.adopt(name, false, activeProfile?.timezone ?? "Europe/Zagreb", target.key, target.dbFile);
    await openProfile(p);
    return p;
  });

  // ---- AI providers
  handle("ai:status", null, () => {
    const local = readSettings().localAi ?? { enabled: false, baseUrl: "http://127.0.0.1:11434/v1" };
    return { chatgpt: siwc.status(), openai_api: { configured: !!vault.get("openai_api_key") }, local };
  });
  handle("ai:models", z.object({ provider: AiProviderSchema }), async ({ provider }) => {
    if (provider === "chatgpt") return siwc.listModels();
    if (provider === "openai_api") {
      const key = vault.get<{ apiKey: string }>("openai_api_key");
      if (!key) throw new Error("Add an OpenAI API key first.");
      const res = await fetchImpl(`${openAiBase()}/models`, { method: "GET", headers: { authorization: `Bearer ${key.apiKey}` } });
      if (!res.ok) throw new Error(`OpenAI API key check failed (${res.status}).`);
      const body: any = await res.json();
      return (body.data ?? []).map((m: any) => m.id).filter((id: string) => /^(gpt|o\d)/.test(id)).sort().map((id: string) => ({ id, label: id }));
    }
    const local = readSettings().localAi;
    if (!local?.enabled) throw new Error("Local AI is disabled.");
    const res = await fetchImpl(`${local.baseUrl}/models`, { method: "GET" });
    if (!res.ok) throw new Error(`Local runtime not reachable at ${local.baseUrl}.`);
    const body: any = await res.json();
    return (body.data ?? []).map((m: any) => ({ id: m.id, label: m.id }));
  });
  handle("ai:apikey:set", ApiKeySchema, ({ apiKey }) => { vault.set("openai_api_key", { apiKey }); return { ok: true }; });
  handle("ai:apikey:clear", null, () => { vault.delete("openai_api_key"); return { ok: true }; });
  handle("ai:local:set", LocalAiSchema, (cfg) => { writeSettings({ ...readSettings(), localAi: cfg }); return cfg; });
  handle("ai:siwc:signIn", z.object({ reconsent: z.boolean().optional() }), (a) => siwc.signIn(a));
  handle("ai:siwc:cancel", null, () => siwc.cancel());
  handle("ai:siwc:signOut", null, () => siwc.signOut());
  handle("ai:siwc:welcomed", null, () => siwc.markWelcomed());
  handle("ai:send", AiSendSchema, (a, e) => orchestrator.ask(a, (ev) => { if (!e.sender.isDestroyed()) e.sender.send("ai:event", ev); }));
  handle("ai:decide", AiDecisionSchema, ({ requestId, approve, approveRemaining }) => orchestrator.decide(requestId, approve, approveRemaining));
  handle("ai:cancel", z.object({ requestId: z.string().max(64) }), ({ requestId }) => orchestrator.cancel(requestId));

  // ---- Google Health
  handle("google:status", null, () => google.status());
  handle("google:config", GoogleConfigSchema, (c) => { google.setConfig(c); return google.status(); });
  handle("google:connect", null, () => google.connect());
  handle("google:sync", GoogleSyncSchema, ({ days }) => google.sync(days, (msg) => send("engine:event", { type: "progress", stage: "google", text: msg })));
  handle("google:disconnect", null, () => google.disconnect());

  ipcMain.on("report:ready", () => undefined);
}

function openAiBase() {
  return (!app.isPackaged && process.env.VITALIS_OPENAI_BASE_URL) || "https://api.openai.com/v1";
}

async function makeProvider(p: AiProvider) {
  if (p === "openai_api") {
    return responsesProvider({ baseUrl: openAiBase(), fetch: fetchImpl, token: async () => {
      const k = vault.get<{ apiKey: string }>("openai_api_key");
      if (!k) throw Object.assign(new Error("Add an OpenAI API key in Settings → AI."), { code: "no_api_key" });
      return k.apiKey;
    } });
  }
  if (p === "chatgpt") {
    return responsesProvider({ baseUrl: (!app.isPackaged && process.env.VITALIS_SIWC_API_BASE) || SIWC.resource,
      fetch: fetchImpl, token: () => siwc.accessToken() });
  }
  const local = readSettings().localAi;
  if (!local?.enabled) throw Object.assign(new Error("Enable a local AI runtime in Settings → AI."), { code: "local_disabled" });
  return chatCompletionsProvider({ baseUrl: local.baseUrl, fetch: fetchImpl });
}

// ------------------------------------------------------------------------------------------- lifecycle
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => { if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.focus(); } });
  app.whenReady().then(() => {
    const cipher = TEST && process.env.VITALIS_TEST_INSECURE_KEYSTORE === "1" ? insecureTestCipher() : electronCipher(safeStorage);
    vault = new Vault(path.join(userData, "secrets"), cipher);
    profiles = new ProfileStore(userData, vault);
    const hostIdFile = path.join(userData, "siwc-host-id.txt");
    siwc = new SiwcClient({ vault, fetch: fetchImpl, openExternal,
      hostIdStore: { get: () => (fs.existsSync(hostIdFile) ? fs.readFileSync(hostIdFile, "utf8").trim() : null),
        set: (v) => fs.writeFileSync(hostIdFile, v) } });
    const engineProxy = { call: <T,>(m: string, p?: Record<string, unknown>, t?: number) => engineOrThrow().call<T>(m, p, t) };
    orchestrator = new Orchestrator(engineProxy, makeProvider);
    google = new GoogleHealthClient({ vault, fetch: fetchImpl, openExternal, engine: engineProxy });
    hardenSession();
    registerIpc();
    createWindow();
  });
  app.on("window-all-closed", async () => {
    if (engine) await engine.stop();
    app.quit();
  });
  app.on("before-quit", () => { engine?.stop(); });
}
