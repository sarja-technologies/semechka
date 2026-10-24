// Client performance benchmark: real Chrome (headless), a running Semechka instance, a real or test backend.
//
//   SEM_BASE=http://127.0.0.1:8890 SEM_EMAIL=member@example.test SEM_PASSWORD_FILE=/path/to/0600-file \
//   SEM_LONG_CONV=c_... SEM_MODEL_ID=m_... SEM_PROMPT="benchmark-markdown" [SEM_CPU_THROTTLE=4] node perf/bench.mjs [label]
//
// Measures, on the same machine and data, whatever frontend build the instance serves:
//   open_long   time from navigation to every message of a long conversation rendered; main-thread long tasks
//   stream      while one reply streams: long tasks (sum/max), frames slower than 50 ms, and input latency of
//               keystrokes typed into the composer during streaming (Event Timing API: keydown to next paint)
// The password is read from a file and never printed. Results go to stdout as one JSON object.
import { chromium } from '@playwright/test';
import { readFileSync } from 'node:fs';

const BASE = process.env.SEM_BASE ?? 'http://127.0.0.1:8890';
const EMAIL = process.env.SEM_EMAIL;
const PASSWORD = readFileSync(process.env.SEM_PASSWORD_FILE, 'utf8').trim();
const CONV = process.env.SEM_LONG_CONV;
const MODEL = process.env.SEM_MODEL_ID;
const PROMPT = process.env.SEM_PROMPT ?? 'benchmark-markdown';
const RUNS = Number(process.env.SEM_RUNS ?? 3);
const THROTTLE = Number(process.env.SEM_CPU_THROTTLE ?? 1); // Chrome DevTools CPU slowdown factor (4 ~ mid-range laptop)
const label = process.argv[2] ?? 'run';

const probe = () => {
  // Installed before any page script: collects long tasks, slow frames and event timings.
  const w = window;
  w.__perf = { long: [], frames: [], events: [] };
  new PerformanceObserver((l) => { for (const e of l.getEntries()) w.__perf.long.push([e.startTime, e.duration]); }).observe({ type: 'longtask', buffered: true });
  new PerformanceObserver((l) => { for (const e of l.getEntries()) if (e.name === 'keydown' || e.name === 'keypress' || e.name === 'input') w.__perf.events.push([e.name, e.duration, e.processingEnd - e.processingStart]); }).observe({ type: 'event', durationThreshold: 16, buffered: true });
  let last = 0;
  const loop = (t) => { if (last) w.__perf.frames.push(t - last); last = t; requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
};

const sum = (a) => a.reduce((x, y) => x + y, 0);
const pct = (a, p) => { if (!a.length) return 0; const s = [...a].sort((x, y) => x - y); return s[Math.min(s.length - 1, Math.floor(p * s.length))]; };
const r1 = (x) => Math.round(x * 10) / 10;

async function main() {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await ctx.addInitScript(probe);
  const page = await ctx.newPage();
  if (THROTTLE > 1) await (await ctx.newCDPSession(page)).send('Emulation.setCPUThrottlingRate', { rate: THROTTLE });
  await page.goto(BASE + '/login');
  const login = await page.evaluate(async ([e, p]) => (await fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: e, password: p }) })).status, [EMAIL, PASSWORD]);
  if (login !== 200) throw new Error('sign-in failed: ' + login);
  // the visible branch: 0.3.0 reports its length (and renders a slice of it); 0.2.0 renders every message it returns
  const expected = await page.evaluate(async (c) => {
    const t = await fetch(`/api/chat/conversations/${c}/thread?limit=1`);
    if (t.ok) return (await t.json()).total;
    const j = await (await fetch('/api/chat/conversations/' + c)).json(); return j.messages.length;
  }, CONV);

  const out = { label, cpu_throttle: THROTTLE, open_long: [], stream: [] };
  for (let i = 0; i < RUNS; i++) {
    // ---- open a long conversation (cold navigation, so the whole app boots) ----
    await page.goto('about:blank');
    const t0 = Date.now();
    await page.goto(`${BASE}/chat/${CONV}`);
    // every message on the active path is in the DOM, or (virtualised UIs) the last one is visible and the count is reported
    await page.waitForFunction(([n, total]) => {
      const a = document.querySelectorAll('article[data-msg], article.msg');
      const sc = document.querySelector('[data-chat-scroll]');
      return a.length >= n || (sc && sc.getAttribute('data-total') === String(total) && a.length > 0);
    }, [Math.min(expected, 200), expected], { timeout: 30000 });
    await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r))));
    const ms = Date.now() - t0;
    const p = await page.evaluate(() => ({ long: window.__perf.long, nodes: document.querySelectorAll('*').length, articles: document.querySelectorAll('article').length }));
    out.open_long.push({ ms, long_sum: r1(sum(p.long.map((x) => x[1]))), long_max: r1(Math.max(0, ...p.long.map((x) => x[1]))), dom_nodes: p.nodes, articles: p.articles });

    // ---- stream one reply in a new conversation while typing into the composer ----
    await page.goto(`${BASE}/chat?new=${Date.now()}&model=${MODEL}`);
    const box = page.locator('#msg');
    await box.waitFor();
    await page.waitForFunction(() => !document.querySelector('#msg')?.hasAttribute('disabled'));
    await box.fill(PROMPT);
    await page.evaluate(() => { window.__perf.long = []; window.__perf.frames = []; window.__perf.events = []; });
    const s0 = Date.now();
    await box.press('Enter');
    // wait until the reply starts, then type while it streams
    await page.waitForFunction(() => /replying|writing|thinking/i.test(document.querySelector('[data-chat-status]')?.textContent ?? '') || document.querySelector('[data-streaming]'), null, { timeout: 30000 });
    await page.waitForTimeout(300);
    await box.pressSequentially('typing while the model streams a long reply', { delay: 40 });
    await page.waitForFunction(() => /complete|stopped|failed/i.test(document.querySelector('[data-chat-status]')?.textContent ?? ''), null, { timeout: 180000 });
    const total = Date.now() - s0;
    const q = await page.evaluate(() => window.__perf);
    const frames = q.frames;
    const evd = q.events.filter((e) => e[0] === 'keydown').map((e) => e[1]);
    out.stream.push({
      total_ms: total,
      long_sum: r1(sum(q.long.map((x) => x[1]))), long_max: r1(Math.max(0, ...q.long.map((x) => x[1]))), long_count: q.long.length,
      frames: frames.length, slow_frames_50ms: frames.filter((f) => f > 50).length, frame_p95: r1(pct(frames, 0.95)), frame_max: r1(Math.max(0, ...frames)),
      key_events_over_16ms: evd.length, key_latency_p95: r1(pct(evd, 0.95)), key_latency_max: r1(Math.max(0, ...evd))
    });
  }
  await browser.close();
  console.log(JSON.stringify(out));
}
main().catch((e) => { console.error(e.message); process.exit(1); });
