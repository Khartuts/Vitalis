// Drives the INSTALLED (packaged) app: node tests/e2e/installed.e2e.mjs "<path to VITALIS.exe>"
// Unlike app.e2e.mjs there are no test hooks here (they exist only in unpackaged builds): the app uses the real OS
// keystore (DPAPI on Windows) and its bundled Python engine. Covers: launch, demo profile creation (vault + engine +
// synthetic data), every screen renders without errors.
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const pw = require("playwright");
const exe = process.argv[2];
if (!exe || !fs.existsSync(exe)) { console.error(`usage: installed.e2e.mjs <VITALIS.exe> (not found: ${exe})`); process.exit(2); }
const shots = process.env.E2E_SHOTS ?? fs.mkdtempSync(path.join(os.tmpdir(), "vitalis-installed-"));
fs.mkdirSync(shots, { recursive: true });
const userData = fs.mkdtempSync(path.join(os.tmpdir(), "vitalis-installed-ud-"));
const results = [];
let aborted = false; // after a failed critical step the remaining steps are reported as not run, not as failures
const step = async (name, fn, critical = false) => {
  if (aborted) { results.push({ name, ok: false, notRun: true }); console.log(`- ${name} (not run)`); return; }
  const t = Date.now();
  try { await fn(); results.push({ name, ok: true }); console.log(`✓ ${name} (${Date.now() - t} ms)`); }
  catch (e) {
    results.push({ name, ok: false }); console.log(`✗ ${name}\n  ${e?.stack ?? e}`);
    if (critical) { aborted = true; console.log("  body:", (await page.locator("body").innerText().catch(() => "")).slice(0, 800)); }
  }
};

const env = { ...process.env, VITALIS_USER_DATA: userData };
for (const k of ["VITALIS_PYTHON", "VITALIS_ENGINE_DIR", "VITALIS_TEST", "VITALIS_TEST_INSECURE_KEYSTORE"]) delete env[k];
const args = process.platform === "linux" ? ["--no-sandbox"] : []; // local Linux container runs are root
const app = await pw._electron.launch({ executablePath: exe, args, env, timeout: 120000 });
const page = await app.firstWindow();
const consoleErrors = [];
page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
page.on("pageerror", (e) => consoleErrors.push(String(e)));
const shot = (n) => page.screenshot({ path: path.join(shots, `${n}.png`) });

await step("installed app is the packaged build", async () => {
  const info = await app.evaluate(({ app: a }) => ({ packaged: a.isPackaged, version: a.getVersion(), resources: process.resourcesPath }));
  console.log("  ", JSON.stringify(info));
  assert.equal(info.packaged, true);
  const bundled = process.platform === "win32" ? ["python", "python.exe"] : ["python", "bin", "python3"];
  assert.ok(fs.existsSync(path.join(info.resources, "engine", ...bundled)), "bundled engine runtime present");
}, true);

await step("welcome screen renders", async () => {
  await page.getByText("Create your private health profile").waitFor({ timeout: 60000 });
  await shot("installed-00-welcome");
}, true);

await step("create demo profile: OS keystore + bundled engine + synthetic data", async () => {
  await page.getByTestId("create-demo").click();
  await page.getByTestId("demo-banner").waitFor({ timeout: 300000 });
  await page.getByTestId("card-recovery").waitFor({ timeout: 120000 });
  assert.match(await page.getByTestId("demo-banner").innerText(), /SYNTHETIC/);
  assert.match(await page.getByTestId("sleep-tst").innerText(), /\d+ h \d{2} min/);
  await page.waitForTimeout(800);
  await shot("installed-01-overview");
}, true);

const screens = { sleep: "sleep-lab", recovery: "recovery-page", stress: "stress-page", cardio: "cardio-page",
  activity: "activity-page", trends: "trends-page", journal: "journal-page", lab: "lab-page", ai: "analyst-page",
  reports: "reports-page", settings: "settings-page" };
for (const [nav, id] of Object.entries(screens)) {
  await step(`screen: ${nav}`, async () => {
    await page.getByTestId(`nav-${nav}`).click();
    await page.getByTestId(id).waitFor({ timeout: 60000 });
    await page.waitForTimeout(1200);
    const errs = await page.locator('[role="alert"]').allInnerTexts();
    assert.deepEqual(errs.filter((e) => !/No data was retrieved/.test(e)), [], `alerts on ${nav}: ${errs.join(" | ")}`);
    await shot(`installed-02-${nav}`);
  });
}

await step("no renderer errors", async () => {
  assert.deepEqual(consoleErrors, []);
});

await app.close();
fs.writeFileSync(path.join(shots, "installed-results.json"), JSON.stringify(results, null, 2));
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} steps passed · screenshots: ${shots}`);
process.exit(failed.length ? 1 : 0);
