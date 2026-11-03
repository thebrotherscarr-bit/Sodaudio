// PWA check in the installed Microsoft Edge: the manifest makes the site installable, the service worker controls
// the page, and with the engine stopped the cached shell opens to "Engine not running".
//   node e2e/pwa-check.mjs            (the engine must be running on 8770; this script stops it at the end)
import { chromium } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const url = process.env.AD_URL || "http://127.0.0.1:8770/";
// A real (non-incognito) profile: Edge only offers installs outside InPrivate.
const context = await chromium.launchPersistentContext(mkdtempSync(join(tmpdir(), "ad-pwa-")), { channel: "msedge", headless: true });
const page = context.pages()[0] || (await context.newPage());
await page.goto(url);
await page.waitForFunction(async () => !!(await navigator.serviceWorker.getRegistration())?.active, null, { timeout: 30000 });
await page.reload();
await page.waitForFunction(() => !!navigator.serviceWorker.controller, null, { timeout: 10000 }).catch(() => {});
const controlled = await page.evaluate(() => !!navigator.serviceWorker.controller);
const cdp = await context.newCDPSession(page);
const { installabilityErrors } = await cdp.send("Page.getInstallabilityErrors");
const manifest = await cdp.send("Page.getAppManifest");
console.log("service worker controls the page:", controlled);
console.log("installability errors:", JSON.stringify(installabilityErrors));
console.log("manifest errors:", JSON.stringify(manifest.errors));
await fetch(url + "api/shutdown", { method: "POST", headers: { "Content-Type": "application/json" }, body: '{"mode":"finish"}' });
for (let i = 0; i < 20; i++) {
  try { await fetch(url + "api/health"); await new Promise((r) => setTimeout(r, 500)); } catch { break; }
}
await page.reload();
await page.waitForSelector("text=Engine not running", { timeout: 15000 });
console.log("engine off: page shows", JSON.stringify(await page.textContent("h1")));
await context.close();
const ok = controlled && installabilityErrors.length === 0;
console.log(ok ? "PWA CHECK PASSED" : "PWA CHECK FAILED");
process.exit(ok ? 0 : 1);
