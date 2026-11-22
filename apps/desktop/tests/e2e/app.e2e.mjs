// End-to-end test of the real Electron app: node tests/e2e/app.e2e.mjs (Linux: under xvfb-run; Windows: directly)
// Covers: demo profile, every screen renders, journal save, correlation lab, AI analyst with consent + tool call +
// evidence (mock OpenAI server), PDF export, real profile creation, phone pairing + pinned-TLS batch sync.
import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import https from "node:https";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { startMockOpenAI } from "./mock-openai.mjs";

const require = createRequire(import.meta.url);
let pw;
try { pw = require("playwright"); } catch { pw = require("/opt/node-tools/node_modules/playwright"); }
const here = path.dirname(fileURLToPath(import.meta.url));
const appDir = path.resolve(here, "..", "..");
const engineDir = path.resolve(appDir, "..", "..", "services", "engine");
const shots = process.env.E2E_SHOTS ?? fs.mkdtempSync(path.join(os.tmpdir(), "vitalis-shots-"));
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "vitalis-ud-"));
const saveDir = fs.mkdtempSync(path.join(os.tmpdir(), "vitalis-out-"));
const results = [];
const step = async (name, fn) => {
  const t = Date.now();
  try { await fn(); results.push({ name, ok: true, ms: Date.now() - t }); console.log(`✓ ${name} (${Date.now() - t} ms)`); }
  catch (e) { results.push({ name, ok: false, error: String(e?.stack ?? e) }); console.log(`✗ ${name}\n  ${e?.stack ?? e}`); }
};

const mock = await startMockOpenAI();
const app = await pw._electron.launch({
  executablePath: createRequire(path.join(appDir, "package.json"))("electron"), // platform-specific binary path
  args: [appDir, "--no-sandbox"],
  env: { ...process.env, VITALIS_TEST: "1", VITALIS_TEST_INSECURE_KEYSTORE: "1", VITALIS_USER_DATA: userData,
    VITALIS_PYTHON: process.env.VITALIS_PYTHON ?? path.join(engineDir, ".venv", process.platform === "win32" ? "Scripts" : "bin",
      process.platform === "win32" ? "python.exe" : "python"), VITALIS_ENGINE_DIR: engineDir,
    VITALIS_TEST_SAVE_DIR: saveDir, VITALIS_OPENAI_BASE_URL: `http://127.0.0.1:${mock.port}/v1` },
});
const page = await app.firstWindow();
await page.setViewportSize({ width: 1600, height: 1000 });
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(String(e)));
const shot = (n) => page.screenshot({ path: path.join(shots, `${n}.png`) });

await step("welcome screen renders", async () => {
  await page.getByText("Create your private health profile").waitFor({ timeout: 30000 });
  await shot("00-welcome");
});

await step("create demo profile (synthetic data, clearly labelled)", async () => {
  await page.getByTestId("create-demo").click();
  await page.getByTestId("demo-banner").waitFor({ timeout: 180000 });
  await page.getByTestId("card-recovery").waitFor({ timeout: 60000 });
  assert.match(await page.getByTestId("demo-banner").innerText(), /SYNTHETIC/);
  await page.waitForTimeout(800);
  await shot("01-overview");
});

await step("overview cards show computed values", async () => {
  const sleep = await page.getByTestId("sleep-tst").innerText();
  assert.match(sleep, /\d+ h \d{2} min/);
  await page.getByTestId("card-baseline").waitFor();
  await page.getByTestId("card-insights").waitFor();
});

await step("Explain: built-in explanation with live data, then ask the analyst", async () => {
  await page.getByTestId("explain-ov.recovery").click();
  const panel = page.getByTestId("explain-panel");
  await panel.waitFor({ timeout: 10000 });
  assert.match(await panel.innerText(), /How it's calculated/i);
  const yours = page.getByTestId("explain-your-data");
  await yours.getByText(/days? with data/).first().waitFor({ timeout: 15000 });
  assert.match(await yours.innerText(), /Resting heart rate/);
  await shot("01b-explain");
  await page.getByTestId("explain-ask").click();
  await page.getByTestId("ai-panel").waitFor({ timeout: 10000 });
  assert.match(await page.getByTestId("ai-input").inputValue(), /recovery estimate/);
  await page.getByTestId("toggle-ai").click(); // close the side panel again
  await page.getByTestId("ai-panel").waitFor({ state: "detached", timeout: 10000 });
});

const pages = ["sleep", "recovery", "stress", "cardio", "temperature", "activity", "trends", "journal", "lab", "ai", "reports", "settings"];
for (const p of pages) {
  await step(`screen: ${p}`, async () => {
    await page.getByTestId(`nav-${p}`).click();
    const id = { sleep: "sleep-lab", recovery: "recovery-page", stress: "stress-page", cardio: "cardio-page", activity: "activity-page",
      temperature: "temperature-page", trends: "trends-page", journal: "journal-page", lab: "lab-page", ai: "analyst-page", reports: "reports-page", settings: "settings-page" }[p];
    await page.getByTestId(id).waitFor({ timeout: 30000 });
    await page.waitForTimeout(1200);
    const errs = await page.locator('[role="alert"]').allInnerTexts();
    assert.deepEqual(errs.filter((e) => !/No data was retrieved/.test(e)), [], `alerts on ${p}: ${errs.join(" | ")}`);
    await shot(`02-${p}`);
    const ex = page.locator('[data-testid^="explain-"]').filter({ hasText: "Explain" });
    if (await ex.count()) { // every analysis screen: the first Explain panel opens and its data loads
      await ex.first().click();
      const panel = page.getByTestId("explain-panel");
      await panel.waitFor({ timeout: 10000 });
      await page.waitForTimeout(400);
      assert.ok(!/Could not load/.test(await panel.innerText()), `Explain on ${p} failed to load its data`);
      await page.keyboard.press("Escape");
      await panel.waitFor({ state: "detached", timeout: 10000 });
    }
  });
}

await step("sleep lab: hypnogram and consistency heatmap", async () => {
  await page.getByTestId("nav-sleep").click();
  await page.getByRole("tab", { name: "Stages & timeline" }).click();
  await page.getByTestId("hypnogram").waitFor({ timeout: 15000 });
  await page.waitForTimeout(600);
  await shot("03-hypnogram");
  await page.getByRole("tab", { name: "Consistency" }).click();
  await page.getByTestId("bedtime-heatmap").waitFor();
  await page.getByRole("tab", { name: "Sleep Planner" }).click();
  await page.getByTestId("planner-result").waitFor();
  assert.match(await page.getByTestId("planner-result").innerText(), /\d\d:\d\d – \d\d:\d\d/);
});

await step("recovery drivers explain the estimate", async () => {
  await page.getByTestId("nav-recovery").click();
  await page.getByTestId("recovery-drivers").waitFor();
  const t = await page.getByTestId("recovery-drivers").innerText();
  assert.match(t, /Resting heart rate|Insufficient/);
  await shot("04-recovery-drivers");
});

await step("temperature: tonight's bedroom suggestion is personal in the demo; bedroom temperature saves", async () => {
  await page.getByTestId("nav-temperature").click();
  await page.getByTestId("thermal-suggestion").waitFor({ timeout: 30000 });
  await page.getByTestId("thermal-value").waitFor({ timeout: 30000 });
  assert.match(await page.getByTestId("thermal-value").innerText(), /\d+\.\d °C/);
  assert.match(await page.getByTestId("thermal-suggestion").innerText(), /personal/);
  await page.getByTestId("bedroom-temp-input").fill("18.5");
  await page.getByTestId("bedroom-temp-save").click();
  await page.getByText("Saved ✓").waitFor({ timeout: 10000 });
  await shot("04b-temperature");
});

await step("journal: save a daily check-in", async () => {
  await page.getByTestId("nav-journal").click();
  await page.getByTestId("habit-coffee_cups").fill("3");
  await page.getByTestId("habit-walk-Yes").click();
  await page.getByTestId("journal-save").click();
  await page.getByText("Saved ✓").waitFor({ timeout: 15000 });
});

await step("correlation lab answers a habit question from stored data", async () => {
  await page.getByTestId("nav-lab").click();
  await page.getByTestId("question-1").click();
  await page.getByTestId("habit-result").getByText(/Association detected|Inconclusive|Suggestive|Insufficient/).first().waitFor({ timeout: 30000 });
  const t = await page.getByTestId("habit-result").innerText();
  assert.match(t, /CI/);
  await shot("05-lab");
  await page.getByRole("tab", { name: "Discover (FDR-controlled)" }).click();
  await page.getByTestId("scan-table").waitFor({ timeout: 30000 });
});

await step("AI analyst: consent preview, read-only tool call, cited evidence (mock provider)", async () => {
  await page.evaluate(() => window.vitalis.invoke("ai:apikey:set", { apiKey: "sk-test-0000000000000000000000" }));
  await page.getByTestId("nav-ai").click();
  await page.getByTestId("ai-provider").selectOption("openai_api");
  await page.getByTestId("ai-model").waitFor({ timeout: 15000 });
  await page.getByTestId("ai-input").fill("How has my resting heart rate looked over the last month?");
  await page.getByTestId("ai-send").click();
  await page.getByTestId("consent-dialog").waitFor({ timeout: 30000 });
  const payload = await page.getByTestId("consent-payload").innerText();
  assert.match(payload, /"store": false/);
  assert.ok(!payload.includes("sk-test"), "API key must not appear in the previewed payload");
  assert.ok(!payload.includes("j:alcohol_units"), "private habits must not appear in cloud payloads");
  await shot("06-consent");
  await page.getByTestId("consent-approve").click();
  // Round 2 (tool results). Wait for the new payload itself: the round-1 dialog may still be closing.
  const dialog2 = page.getByTestId("consent-dialog").filter({ hasText: "function_call_output" });
  await dialog2.waitFor({ timeout: 30000 });
  assert.match(await dialog2.getByTestId("consent-payload").innerText(), /function_call_output/);
  await dialog2.getByTestId("consent-approve").click();
  await page.getByTestId("ai-answer").last().waitFor({ timeout: 30000 });
  const chip = page.getByTestId("evidence-chip").first();
  await chip.waitFor();
  assert.match(await chip.innerText(), /Resting heart rate/);
  await shot("07-ai-answer");
  await chip.click();
  await page.getByText("Days with data").waitFor();
  await page.keyboard.press("Escape");
  assert.equal(mock.requests.length, 2);
  for (const r of mock.requests) {
    assert.equal(r.body.store, false); assert.equal(r.body.stream, true);
    assert.equal(r.body.tools[0].type, "namespace");
    assert.ok(!("temperature" in r.body) && !("previous_response_id" in r.body));
    assert.match(r.headers.authorization, /^Bearer sk-test/);
  }
  const disc = await page.evaluate(() => window.vitalis.invoke("engine:call", { method: "ai.disclosures" }));
  assert.equal(disc.data.length, 2, "both approved requests are logged");
});

await step("reports: PDF export", async () => {
  await page.getByTestId("nav-reports").click();
  await page.getByTestId("report-view").waitFor({ timeout: 30000 });
  await shot("08-report");
  await page.getByTestId("export-pdf").click();
  await page.getByText(/Saved .*\.pdf/).waitFor({ timeout: 120000 });
  const pdf = fs.readdirSync(saveDir).find((f) => f.endsWith(".pdf"));
  assert.ok(pdf);
  const buf = fs.readFileSync(path.join(saveDir, pdf));
  assert.equal(buf.subarray(0, 4).toString(), "%PDF");
  fs.copyFileSync(path.join(saveDir, pdf), path.join(shots, "report.pdf"));
});

await step("command palette and theme toggle", async () => {
  await page.keyboard.press("Control+k");
  await page.getByTestId("palette-input").fill("Cardio");
  await page.keyboard.press("Enter");
  await page.getByTestId("cardio-page").waitFor();
  await page.getByTitle("Toggle light/dark theme").click();
  await page.waitForTimeout(600);
  await shot("09-light-theme");
  await page.getByTitle("Toggle light/dark theme").click();
});

let fp = null; let port = null; let code = null;
await step("phone-width layout: bottom tabs, menu, single column", async () => {
  await page.setViewportSize({ width: 412, height: 900 });
  await page.getByTestId("tab-overview").click();
  await page.getByTestId("card-recovery").waitFor({ timeout: 15000 });
  assert.equal(await page.getByTestId("nav-overview").isVisible(), false, "desktop sidebar hidden on phones");
  await shot("12-phone-overview");
  await page.getByTestId("tab-sleep").click();
  await page.getByTestId("sleep-lab").waitFor({ timeout: 15000 });
  await shot("13-phone-sleep");
  await page.getByTestId("mobile-menu").click();
  await page.getByRole("dialog", { name: "All screens" }).getByText("Cardiovascular Health").first().click();
  await page.getByTestId("cardio-page").waitFor({ timeout: 15000 });
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.getByTestId("nav-overview").click();
});

await step("create a real (empty) profile and enable phone sync", async () => {
  const r = await page.evaluate(() => window.vitalis.invoke("profiles:create", { name: "Test person", demo: false, timezone: "Europe/Zagreb", birthYear: 1985 }));
  assert.ok(r.ok, JSON.stringify(r));
  await page.reload();
  await page.getByTestId("nav-settings").click();
  await page.getByRole("tab", { name: "Phone sync" }).click();
  await page.getByTestId("sync-start").click();
  await page.getByTestId("sync-pair").click();
  await page.getByTestId("pairing-qr").waitFor({ timeout: 15000 });
  await shot("10-pairing");
  const st = await page.evaluate(() => window.vitalis.invoke("engine:call", { method: "sync.status" }));
  fp = st.data.fingerprint; port = st.data.port;
  code = (await page.getByTestId("pairing-qr").innerText()).match(/\b\d{8}\b/)[0];
});

function phoneRequest(method, p, body, token) {
  return new Promise((resolve, reject) => {
    const data = body ? Buffer.from(JSON.stringify(body)) : null;
    const req = https.request({ host: "127.0.0.1", port, path: p, method, rejectUnauthorized: false,
      checkServerIdentity: (_h, cert) => { // certificate pinning, exactly like the companion
        const got = crypto.createHash("sha256").update(cert.raw).digest("hex");
        return got === fp ? undefined : new Error(`pin mismatch ${got}`);
      },
      headers: { "content-type": "application/json", ...(data ? { "content-length": data.length } : {}), ...(token ? { authorization: `Bearer ${token}` } : {}) } },
    (res) => { let b = ""; res.on("data", (c) => (b += c)); res.on("end", () => resolve({ status: res.statusCode, body: b ? JSON.parse(b) : null })); });
    req.on("error", reject);
    if (data) req.write(data);
    req.end();
  });
}

await step("simulated phone: pair over pinned TLS, upload Health Connect batch, data appears", async () => {
  const pair = await phoneRequest("POST", "/v1/pair", { code, device_name: "Galaxy S24 (simulated)" });
  assert.equal(pair.status, 200);
  const token = pair.body.token;
  const days = 20;
  const records = [];
  const now = new Date(); now.setUTCHours(0, 0, 0, 0);
  for (let i = days; i >= 1; i--) {
    const wake = new Date(now.getTime() - (i - 1) * 86400000 + 5.5 * 3600000);
    const bed = new Date(wake.getTime() - 7.5 * 3600000);
    const iso = (d) => d.toISOString();
    records.push({ type: "SleepSessionRecord", id: `s${i}`, origin: "com.sec.android.app.shealth", device: { type: "WATCH", manufacturer: "Samsung", model: "SM-R930" },
      startTime: iso(bed), startZoneOffset: "+01:00", endTime: iso(wake), endZoneOffset: "+01:00",
      stages: [{ startTime: iso(bed), endTime: iso(new Date(bed.getTime() + 1800000)), stage: "awake" },
        { startTime: iso(new Date(bed.getTime() + 1800000)), endTime: iso(new Date(bed.getTime() + 3 * 3600000)), stage: "deep" },
        { startTime: iso(new Date(bed.getTime() + 3 * 3600000)), endTime: iso(wake), stage: "light" }] });
    records.push({ type: "HeartRateRecord", id: `h${i}`, origin: "com.sec.android.app.shealth", device: { type: "WATCH" }, startTime: iso(bed), startZoneOffset: "+01:00",
      endTime: iso(wake), samples: Array.from({ length: 45 }, (_, k) => ({ time: iso(new Date(bed.getTime() + k * 600000)), bpm: 58 + (k % 5) - (i % 3) })) });
    records.push({ type: "StepsAggregateDaily", origin: "com.sec.android.app.shealth", startTime: iso(new Date(wake.getTime() - 5.5 * 3600000)),
      endTime: iso(new Date(wake.getTime() + 18.5 * 3600000)), startZoneOffset: "+01:00", value: 6000 + i * 100 });
  }
  const batch = { schema: "vitalis.sync.v1", batch_id: crypto.randomUUID(), source: "health_connect", device: { model: "SM-S921B" }, records, deletions: [] };
  const up = await phoneRequest("POST", "/v1/batches", batch, token);
  assert.equal(up.status, 200, JSON.stringify(up.body));
  assert.ok(up.body.summary.inserted > 900);
  const again = await phoneRequest("POST", "/v1/batches", batch, token);
  assert.equal(again.body.status, "already_imported");
  const bad = await phoneRequest("POST", "/v1/batches", batch, "wrong-token");
  assert.equal(bad.status, 401);
  await page.waitForTimeout(4000); // background recompute
  await page.getByTestId("nav-overview").click();
  await page.getByTestId("card-sleep").waitFor({ timeout: 30000 });
  await page.getByTestId("sleep-tst").waitFor({ timeout: 30000 });
  const t = await page.getByTestId("sleep-tst").innerText();
  assert.match(t, /7 h 00 min/); // 7.5 h in bed minus 30 min awake
  assert.equal(await page.getByTestId("demo-banner").count(), 0, "real profile shows no demo banner");
  await shot("11-real-profile-after-sync");
});

console.log("\nconsole errors:", consoleErrors.filter((e) => !/Download the React DevTools/.test(e)).slice(0, 10));
await app.close();
mock.server.close();
fs.writeFileSync(path.join(shots, "results.json"), JSON.stringify(results, null, 2));
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} steps passed · screenshots: ${shots}`);
process.exit(failed.length ? 1 : 0);
