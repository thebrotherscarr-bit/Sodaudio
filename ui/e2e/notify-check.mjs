// P4: a desktop notification when a long job and then the whole queue finish. Playwright's clock makes a short
// fake-generator job look like a three-minute one.   node e2e/notify-check.mjs   (engine on 8770 with a project open)
import { chromium } from "@playwright/test";

const url = process.env.AD_URL || "http://127.0.0.1:8770/";
const browser = await chromium.launch({ channel: "msedge", headless: true });
const context = await browser.newContext({ permissions: ["notifications"] });
const page = await context.newPage();
await page.addInitScript(() => {
  window.__notes = [];
  const Real = window.Notification;
  class Recorder { constructor(title, opts) { window.__notes.push({ title, body: opts?.body }); } static get permission() { return "granted"; } static requestPermission() { return Promise.resolve("granted"); } }
  window.Notification = Recorder;
});
await page.clock.install();
await page.goto(url);
await page.waitForTimeout(1500);
const cues = ["mus_town", "mus_space"];
await page.evaluate(async (cues) => {
  await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ fake_delay: 0.4 }) });
  const r = await fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ type: "generate", cues, takes: 9, seconds: 4 }) });
  return r.json();
}, cues);
await page.waitForTimeout(1000);
await page.clock.fastForward(200000); // the running job now looks 200 s old
for (let i = 0; i < 120; i++) {
  const notes = await page.evaluate(() => window.__notes);
  if (notes.some((n) => n.title === "Queue finished")) { console.log(JSON.stringify(notes)); console.log("NOTIFY CHECK PASSED"); await browser.close(); process.exit(0); }
  await page.waitForTimeout(1000);
}
console.log(JSON.stringify(await page.evaluate(() => window.__notes)));
console.log("NOTIFY CHECK FAILED");
await browser.close();
process.exit(1);
