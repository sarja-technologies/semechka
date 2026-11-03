// Development-only mock of Semechka's /api/work contract (docs/WORK_MODE.md section 6) with canned timelines, so the Work Mode
// screens can be built and screenshotted without a backend or a login. NOT part of the app bundle and never deployed.
//
//   node dev/work-mock/server.mjs [--port 8791] [--static build] [--manage]
//
// It also serves the built web app (pnpm build) from --static with the SPA fallback, answers just enough of /api/public and
// /api/me for the shell to render, and fakes a running session by releasing events over time so long-polling is exercised.
// Magic inputs for testing error paths: a message containing "trigger-limit" returns 429; an upload over 2 MB returns 413;
// /work/ws_403 answers 403; any unknown session id answers 404.
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

const arg = (k, d) => { const i = process.argv.indexOf(k); return i > 0 ? process.argv[i + 1] : d; };
const PORT = Number(arg('--port', 8791));
const STATIC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..', arg('--static', 'build'));
const MANAGE = process.argv.includes('--manage');
const NOW = Date.now();
const ago = (min) => new Date(NOW - min * 60000).toISOString();
const iso = () => new Date().toISOString();

// ---- tiny PNG encoder (for artifacts and previews) ---------------------------------------------------------------
const crcT = new Uint32Array(256).map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
const crc = (b) => { let c = 0xffffffff; for (const x of b) c = crcT[(c ^ x) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
const chunk = (t, d) => { const l = Buffer.alloc(4); l.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([l, td, c]); };
function png(w, h, px) {
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w * 3 + 1)] = 0; for (let x = 0; x < w; x++) { const [r, g, b] = px(x, y); const o = y * (w * 3 + 1) + 1 + x * 3; raw[o] = r; raw[o + 1] = g; raw[o + 2] = b; } }
  const ih = Buffer.alloc(13); ih.writeUInt32BE(w, 0); ih.writeUInt32BE(h, 4); ih[8] = 8; ih[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ih), chunk('IDAT', zlib.deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}
const chartPng = () => png(480, 320, (x, y) => {
  if (x < 40 || y > 280) return x === 40 || y === 280 ? [90, 90, 90] : [250, 250, 249];
  const bars = [120, 180, 150, 230, 200, 260]; const i = Math.floor((x - 56) / 70);
  if (i >= 0 && i < 6 && (x - 56) % 70 < 52 && y > 280 - bars[i]) return i === 5 ? [11, 110, 105] : [120, 150, 170];
  return [250, 250, 249];
});
const scenePng = (seed) => png(480, 320, (x, y) => {
  const t = y / 320;
  const sky = [Math.round(240 - 60 * t + seed * 10), Math.round(190 - 40 * t), Math.round(120 + 90 * t)];
  const d = Math.hypot(x - (160 + seed * 60), y - 200);
  if (d < 46) return [250, 224, 140];
  if (y > 230 + 14 * Math.sin(x / 38 + seed)) return [34 + seed * 8, 74, 70];
  return sky;
});

// ---- data --------------------------------------------------------------------------------------------------------------
const sessions = new Map();
const waiters = new Map(); // session id -> Set of wake functions
let nextSkill = 1;

function mk(id, p) {
  const s = { id, title: p.title, objective: p.objective ?? '', model_id: p.model_id ?? 'm_qwen', status: p.status, container_state: p.container ?? 'running', network: p.network ?? 'none',
    autonomy: p.autonomy ?? 'contained', skill_ids: p.skills ?? [], created_at: ago(p.created ?? 120), last_active_at: ago(p.active ?? 5), updated_at: ago(p.active ?? 5),
    events: [], jobs: p.jobs ?? [], artifacts: [], usage: p.usage ?? { steps: 0, tokens_in: 0, tokens_out: 0 }, script: [], fs: baseFs(id), nid: 0 };
  sessions.set(id, s);
  return s;
}
function ev(s, kind, data, min = 0, extra = {}) {
  const e = { id: ++s.nid, kind, run_id: 'run_1', step_n: extra.step ?? null, summary: extra.summary ?? '', created_at: min === null ? iso() : ago(min), data };
  s.events.push(e); s.last_active_at = e.created_at; wake(s.id); return e;
}
function wake(id) { for (const f of waiters.get(id) ?? []) f(); }
function baseFs(id) {
  const f = new Map();
  const add = (p, c, mime = 'text/plain') => f.set(p, { content: Buffer.isBuffer(c) ? c : Buffer.from(c), mime, modified: ago(8) });
  add('README.md', '# Sales analysis\n\nQuarterly numbers, cleaned, with one chart.\n', 'text/markdown');
  add('notes.txt', 'Remember: revenue is in thousands.\n');
  add('data/sales.csv', 'quarter,revenue,cost\nQ1,120,80\nQ2,180,95\nQ3,150,90\nQ4,230,110\n', 'text/csv');
  add('data/raw/export-2026-10.csv', 'id,amount\n1,10\n2,20\n', 'text/csv');
  add('src/plot.py', 'import pandas as pd\nimport matplotlib.pyplot as plt\n\ndf = pd.read_csv("data/sales.csv")\ndf.plot(x="quarter", y="revenue", kind="bar")\nplt.savefig("artifacts/chart.png")\n', 'text/x-python');
  add('artifacts/chart.png', chartPng(), 'image/png');
  return f;
}

// 1. running, with a live script
const run1 = mk('ws_run1', { title: 'Plot quarterly sales', objective: 'Clean data/sales.csv, plot revenue by quarter and save the chart as an artifact.', status: 'running', active: 0, created: 14, skills: ['sk_chart', 'sk_data'], usage: { steps: 4, tokens_in: 6210, tokens_out: 1480 } });
ev(run1, 'message', { role: 'user', content: 'Please clean data/sales.csv, plot revenue by quarter and keep the chart as an artifact.' }, 13);
ev(run1, 'state', { status: 'running' }, 13);
ev(run1, 'message', { role: 'assistant', content: 'I will start by looking at the data, then write a small script and run it.\n\n1. Inspect the CSV\n2. Plot revenue with matplotlib\n3. Save the chart in `artifacts/`' }, 12.5);
ev(run1, 'tool_call', { call_id: 'c1', tool: 'shell', command: 'ls -la data && head -n 5 data/sales.csv' }, 12, { step: 1, summary: 'Look at the data' });
ev(run1, 'tool_result', { call_id: 'c1', ok: true, exit_code: 0, duration_ms: 84, output: 'total 12\ndrwxr-xr-x 3 65534 65534 4096 Nov  4 10:02 .\ndrwxr-xr-x 6 65534 65534 4096 Nov  4 10:02 ..\n-rw-r--r-- 1 65534 65534   62 Nov  4 10:02 sales.csv\ndrwxr-xr-x 2 65534 65534 4096 Nov  4 10:02 raw\nquarter,revenue,cost\nQ1,120,80\nQ2,180,95\nQ3,150,90\nQ4,230,110' }, 11.9, { step: 1 });
ev(run1, 'tool_call', { call_id: 'c2', tool: 'write_file', command: '', args: { path: 'src/plot.py', content: 'import pandas as pd\n...' } }, 11, { step: 2, summary: 'Write src/plot.py' });
ev(run1, 'tool_result', { call_id: 'c2', ok: true, duration_ms: 12, output: 'Wrote 212 bytes to /work/src/plot.py' }, 10.9, { step: 2 });
ev(run1, 'tool_call', { call_id: 'c3', tool: 'python', command: 'python src/plot.py', args: {} }, 0.2, { step: 3, summary: 'Run the plot script' });
run1.script = [
  [3, 'tool_result', { call_id: 'c3', ok: false, exit_code: 1, duration_ms: 1840, output: 'Traceback (most recent call last):\n  File "src/plot.py", line 6, in <module>\n    plt.savefig("artifacts/chart.png")\nFileNotFoundError: [Errno 2] No such file or directory: \'artifacts\'', truncated: false }, { step: 3 }],
  [3, 'message', { role: 'assistant', content: 'The `artifacts` folder does not exist yet. I will create it and run the script again.' }],
  [2, 'tool_call', { call_id: 'c4', tool: 'shell', command: 'mkdir -p artifacts && python src/plot.py && ls -l artifacts' }, { step: 4 }],
  [4, 'tool_result', { call_id: 'c4', ok: true, exit_code: 0, duration_ms: 2310, output: 'total 8\n-rw-r--r-- 1 65534 65534 5311 Nov  4 10:11 chart.png' }, { step: 4 }],
  [1, 'artifact', { artifact_id: 'wa_chart', name: 'chart.png', path: 'artifacts/chart.png', mime: 'image/png', size: 5311 }],
  [2, 'message', { role: 'assistant', content: 'Done. The chart shows revenue by quarter; **Q4 is the strongest at 230**, Q3 dipped to 150.\n\nThe script is in `src/plot.py` and the picture is under Artifacts.' }],
  [0, 'state', { status: 'idle' }]
];
run1.artifacts.push({ id: 'wa_chart', name: 'chart.png', path: 'artifacts/chart.png', mime: 'image/png', size: 5311, created_at: ago(0), saved_to_library: false, hidden: true });
run1.usage = { steps: 3, tokens_in: 6210, tokens_out: 1480 };

// 2. done, with artifacts
const done = mk('ws_done', { title: 'Resize product photos', status: 'idle', active: 95, created: 180, network: 'none', usage: { steps: 9, tokens_in: 14880, tokens_out: 3120 }, skills: ['sk_media'] });
ev(done, 'message', { role: 'user', content: 'Make 480px wide thumbnails of the three photos in photos/ and a contact sheet.' }, 130);
ev(done, 'tool_call', { call_id: 'd1', tool: 'shell', command: 'python -c "from PIL import Image; import glob; [Image.open(f).resize((480, 320)).save(f.replace(\'photos/\', \'artifacts/thumb-\')) for f in glob.glob(\'photos/*.jpg\')]"' }, 128, { step: 1 });
ev(done, 'tool_result', { call_id: 'd1', ok: true, exit_code: 0, duration_ms: 910, output: '' }, 127.9, { step: 1 });
ev(done, 'artifact', { artifact_id: 'wa_t1', name: 'thumb-dunes.png', path: 'artifacts/thumb-dunes.png', mime: 'image/png', size: 18204 }, 126);
ev(done, 'artifact', { artifact_id: 'wa_t2', name: 'contact-sheet.png', path: 'artifacts/contact-sheet.png', mime: 'image/png', size: 22110 }, 125);
ev(done, 'artifact', { artifact_id: 'wa_pdf', name: 'summary.pdf', path: 'artifacts/summary.pdf', mime: 'application/pdf', size: 90210 }, 124);
ev(done, 'message', { role: 'assistant', content: 'The thumbnails and a contact sheet are ready, and I added a one-page summary as a PDF.' }, 123);
ev(done, 'state', { status: 'idle' }, 122);
done.artifacts.push({ id: 'wa_t1', name: 'thumb-dunes.png', path: 'artifacts/thumb-dunes.png', mime: 'image/png', size: 18204, created_at: ago(126), saved_to_library: true },
  { id: 'wa_t2', name: 'contact-sheet.png', path: 'artifacts/contact-sheet.png', mime: 'image/png', size: 22110, created_at: ago(125), saved_to_library: false },
  { id: 'wa_pdf', name: 'summary.pdf', path: 'artifacts/summary.pdf', mime: 'application/pdf', size: 90210, created_at: ago(124), saved_to_library: false });

// 3. waiting for approval
const appr = mk('ws_appr', { title: 'Publish the cleaned dataset', status: 'awaiting_approval', network: 'internet', autonomy: 'contained', active: 2, created: 40, usage: { steps: 6, tokens_in: 8800, tokens_out: 1900 } });
ev(appr, 'message', { role: 'user', content: 'Commit the cleaned data and push it to the team repository.' }, 20);
ev(appr, 'tool_call', { call_id: 'a1', tool: 'git', command: 'git add -A && git commit -m "Clean sales data"' }, 18, { step: 1 });
ev(appr, 'tool_result', { call_id: 'a1', ok: true, exit_code: 0, duration_ms: 240, output: '[main 3f2a9c1] Clean sales data\n 2 files changed, 14 insertions(+), 3 deletions(-)' }, 17.9, { step: 1 });
ev(appr, 'approval', { step: 2, tool: 'git', command: 'git push origin main', reason: 'Pushing sends your work outside the container.', status: 'pending' }, 2, { step: 2, summary: 'Push the commit to origin' });
ev(appr, 'state', { status: 'awaiting_approval' }, 2);

// 4. failed
const fail = mk('ws_fail', { title: 'Build the weekly report', status: 'failed', container: 'error', active: 300, created: 400, usage: { steps: 12, tokens_in: 22100, tokens_out: 4000 } });
ev(fail, 'message', { role: 'user', content: 'Build the weekly report from the exports and render it as a .docx.' }, 330);
ev(fail, 'tool_call', { call_id: 'f1', tool: 'shell', command: 'python build_report.py --week 44' }, 328, { step: 1 });
ev(fail, 'tool_result', { call_id: 'f1', ok: false, exit_code: 1, duration_ms: 3120, output: 'Traceback (most recent call last):\n  File "build_report.py", line 21, in <module>\n    import docx\nModuleNotFoundError: No module named \'docx\'' }, 327.9, { step: 1 });
ev(fail, 'tool_call', { call_id: 'f2', tool: 'shell', command: 'pip install python-docx' }, 326, { step: 2 });
ev(fail, 'tool_result', { call_id: 'f2', ok: false, exit_code: 1, duration_ms: 450, output: 'ERROR: Could not find a version that satisfies the requirement python-docx (no network)' }, 325.9, { step: 2 });
ev(fail, 'error', { code: 'container_error', message: 'The container stopped unexpectedly (out of memory at 4 GiB). Send a message to start it again.' }, 300);
ev(fail, 'state', { status: 'failed', note: 'container error' }, 300);

// 5. engine job with progress
const jobS = mk('ws_job', { title: 'Storyboard video clip', status: 'running', active: 0, created: 30, skills: ['sk_video'], usage: { steps: 5, tokens_in: 9100, tokens_out: 2050 },
  jobs: [ { id: 'job_9f31', kind: 'video', model: 'ltx-video', status: 'running', phase: 'Denoising, step 21 of 50', progress: 0.42, started_at: ago(4) },
          { id: 'job_9f32', kind: 'image', model: 'flux-schnell', status: 'queued', queue_position: 2 },
          { id: 'job_9e10', kind: 'image', model: 'flux-schnell', status: 'completed', phase: 'Done', progress: 1, started_at: ago(12), finished_at: ago(11) } ] });
ev(jobS, 'message', { role: 'user', content: 'Make a 4 second clip of waves on a beach, based on the first storyboard frame.' }, 25);
ev(jobS, 'tool_call', { call_id: 'j1', tool: 'generate_image', command: '', args: { prompt: 'waves on a beach, first frame', size: '768x512' } }, 22, { step: 1, summary: 'Generate the first frame' });
ev(jobS, 'job', { job_id: 'job_9e10', job_kind: 'image', model: 'flux-schnell', status: 'running', phase: 'Sampling', progress: 0.5 }, 21.5);
ev(jobS, 'job', { job_id: 'job_9e10', job_kind: 'image', model: 'flux-schnell', status: 'completed', phase: 'Done', progress: 1 }, 21);
ev(jobS, 'tool_result', { call_id: 'j1', ok: true, duration_ms: 31200, output: 'Saved /work/artifacts/frame-1.png (768x512)' }, 21, { step: 1 });
ev(jobS, 'artifact', { artifact_id: 'wa_f1', name: 'frame-1.png', path: 'artifacts/frame-1.png', mime: 'image/png', size: 40113 }, 21);
ev(jobS, 'job', { job_id: 'job_9f31', job_kind: 'video', model: 'ltx-video', status: 'queued' }, 5);
ev(jobS, 'job', { job_id: 'job_9f31', job_kind: 'video', model: 'ltx-video', status: 'running', phase: 'Denoising, step 21 of 50', progress: 0.42 }, 0.5);
ev(jobS, 'job', { job_id: 'job_9f32', job_kind: 'image', model: 'flux-schnell', status: 'queued' }, 0.3);
ev(jobS, 'delegate', { delegate_id: 'dg1', model: 'qwen2.5-vl-7b', task: 'Describe the first frame and say whether the waves look natural.', depth: 1, status: 'done', result: 'The frame shows a **calm shoreline** with gentle waves. The foam edge is slightly too regular; adding variation would look more natural.' }, 18);
jobS.artifacts.push({ id: 'wa_f1', name: 'frame-1.png', path: 'artifacts/frame-1.png', mime: 'image/png', size: 40113, created_at: ago(21), saved_to_library: false });
jobS.tickJob = true;

// 6. paused, stopped container
const paused = mk('ws_paused', { title: 'Rename holiday photos', status: 'paused', container: 'stopped', active: 1500, created: 1600 });
ev(paused, 'message', { role: 'user', content: 'Rename all the photos by date taken.' }, 1520);
ev(paused, 'state', { status: 'paused' }, 1500);

const artifactBytes = (a) => a.mime === 'image/png' ? (a.name.startsWith('contact') ? scenePng(2) : a.id === 'wa_chart' ? chartPng() : scenePng(a.id.length % 3 + 1)) : Buffer.from('%PDF-1.4 mock');

// ---- skills ------------------------------------------------------------------------------------------------------------
const skills = [
  { id: 'sk_chart', scope: 'user', name: 'clean-charts', title: 'Clean charts', description: 'Make plain, readable charts with matplotlib: labelled axes, one accent colour, saved as PNG.', category: 'data-analysis', enabled: true, body_md: '# Clean charts\n\nUse matplotlib. Always label both axes and set a title.\n\n```python\nimport matplotlib.pyplot as plt\nplt.savefig("artifacts/chart.png", dpi=160)\n```\n\n- One accent colour\n- No 3D', files: [{ name: 'style.mplstyle', content: 'axes.spines.top: False\naxes.spines.right: False\n' }], updated_at: ago(3000) },
  { id: 'sk_data', scope: 'user', name: 'data-cleanup', title: 'Data cleanup', description: 'Inspect a CSV, fix types and missing values with pandas, and report what changed.', category: 'data-analysis', enabled: true, body_md: '# Data cleanup\n\n1. Load with pandas\n2. Report dtypes and missing values\n3. Fix, then save a new file; never overwrite the original', files: [], updated_at: ago(5000) },
  { id: 'sk_media', scope: 'org', name: 'image-resize', title: 'Resize images', description: 'Resize and convert pictures with Pillow while keeping the aspect ratio.', category: 'media-editing', enabled: true, body_md: '# Resize images\n\nUse Pillow `Image.thumbnail`. Keep the aspect ratio.', files: [], updated_at: ago(9000) },
  { id: 'sk_video', scope: 'org', name: 'short-video', title: 'Short video from a still', description: 'Plan a short clip, start the engine video job and wait for it to finish.', category: 'video-production', enabled: true, body_md: '# Short video\n\nStart with `generate_image`, then `generate_video` with the frame as input.', files: [{ name: 'shotlist.md', content: '# Shot list\n' }], updated_at: ago(7000) },
  { id: 'sk_old', scope: 'org', name: 'legacy-report', title: 'Legacy report format', description: 'The old weekly report layout. Kept for reference only.', category: 'document-creation', enabled: false, body_md: '# Legacy report', files: [], updated_at: ago(20000) }
];
const canEditSkill = (s) => s.scope === 'user' || MANAGE;
const skillOut = (s, full) => { const { body_md, files, ...rest } = s; return { ...rest, can_edit: canEditSkill(s), ...(full ? { body_md, files } : {}) }; };

// ---- http --------------------------------------------------------------------------------------------------------------
const json = (res, code, body) => { const b = JSON.stringify(body); res.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(b); };
const fail_ = (res, code, message, c = 'error') => json(res, code, { detail: { code: c, message } });
const readBody = (req) => new Promise((r) => { const a = []; req.on('data', (c) => a.push(c)); req.on('end', () => r(Buffer.concat(a))); });
const readJson = async (req) => { try { return JSON.parse((await readBody(req)).toString() || '{}'); } catch { return {}; } };
function multipart(buf, ct) {
  const b = '--' + /boundary=(.+)$/.exec(ct)[1];
  const out = { fields: {}, file: null };
  for (const part of buf.toString('latin1').split(b).slice(1, -1)) {
    const [head, ...rest] = part.split('\r\n\r\n'); const body = rest.join('\r\n\r\n').replace(/\r\n$/, '');
    const name = /name="([^"]+)"/.exec(head)?.[1]; const fn = /filename="([^"]*)"/.exec(head)?.[1];
    if (fn !== undefined) out.file = { name: fn, data: Buffer.from(body, 'latin1') }; else if (name) out.fields[name] = Buffer.from(body, 'latin1').toString('utf8');
  }
  return out;
}

const PERMS = { network: true, skills_manage: MANAGE };
const models = [
  { id: 'm_qwen', alias: 'Qwen 3 32B', tools: true, vision: false, context: 32768, ready: true },
  { id: 'm_vl', alias: 'Qwen 2.5 VL 7B', tools: true, vision: true, context: 16384, ready: true },
  { id: 'm_big', alias: 'Llama 3.3 70B', tools: true, vision: false, context: 65536, ready: false }
];
const me = () => ({
  user: { id: 'u_mock', email: 'mock@example.test', display_name: 'Mock Person', mfa: false, prefs: {} }, roles: [], teams: [], permissions: ['mode.work', 'work.network', ...(MANAGE ? ['work.skills.manage'] : [])],
  admin: false, mfa_enrolment_required: false, can_delete_account: false, csrf: 'mock-csrf',
  ui: { brand: {}, theme: { default: 'system', follow_system: true, user_choice: true, choices: ['light', 'dark', 'graphite'], active: 'system', tokens: [] }, ui: { sidebar: true, folders: false, pinning: false, help_links: false }, 
    pages: [{ id: 'agents', label: 'Assistants', available: true, reason: '' }, { id: 'usage', label: 'Usage', available: true, reason: '' }],
    nav: [{ id: 'agents', label: 'Assistants', available: true, reason: '' }, { id: 'usage', label: 'Usage', available: true, reason: '' }], home: 'chat',
    modes: { chat: { enabled: true, reason: '' }, code: { enabled: false, reason: '' } }, operations: { text: { enabled: true }, work: process.env.MOCK_NO_WORK ? undefined : { enabled: true, reason: '' } }, default_models: {}, sidebar_collapsed: false, suggested_prompts: [], density: 'comfortable',
    composer: { attachments: false, branching: false, export: false, max_chars: 20000, ephemeral: false, delete: true, enter_sends: true, prompt_history: false }, tech: { drawer: false, fields: [] }, retention_days: 0, retention: { days: 0, text: '', show: false, place: 'header' }, privacy_notice: '' }
});

function stateOf(s) {
  const active = s.status === 'running' || s.status === 'awaiting_approval';
  return { status: s.status, container: { state: s.container_state, note: s.container_state === 'error' ? 'Out of memory at the 4 GiB limit' : undefined }, network: s.network, autonomy: s.autonomy, model_id: s.model_id,
    active_run: active ? { id: 'run_1', step_n: s.usage.steps || 1, max_steps: 40 } : null, jobs: s.jobs, usage: { ...s.usage, workspace_bytes: 18_400_000, workspace_limit_bytes: 2048 * 1024 * 1024 } };
}
const sessionOut = (s) => { const { events, jobs, artifacts, usage, script, fs: _f, nid, tickJob, ...o } = s; return { ...o, model_alias: models.find((m) => m.id === s.model_id)?.alias }; };

function dirList(s, dir) {
  const prefix = dir ? dir.replace(/\/$/, '') + '/' : ''; const seen = new Map();
  for (const [p, f] of s.fs) {
    if (!p.startsWith(prefix)) continue;
    const rest = p.slice(prefix.length); const i = rest.indexOf('/');
    if (i < 0) seen.set(rest, { name: rest, path: p, type: 'file', size: f.content.length, modified: f.modified, mime: f.mime });
    else seen.set(rest.slice(0, i), { name: rest.slice(0, i), path: prefix + rest.slice(0, i), type: 'dir' });
  }
  return { path: dir, entries: [...seen.values()] };
}

// timers that make the mock sessions move
const FREEZE = !!process.env.MOCK_FREEZE; // keep the canned timelines still, for screenshots
for (const s of FREEZE ? [] : [run1]) {
  let i = 0; const next = () => {
    const step = s.script[i++]; if (!step) return;
    const [delay, kind, data, extra] = step;
    setTimeout(() => {
      ev(s, kind, data, null, extra ?? {});
      if (kind === 'state') { s.status = data.status; s.container_state = 'running'; }
      if (kind === 'tool_result') s.usage.steps++;
      next();
    }, delay * 1000 + 2000);
  }; next();
}
if (!FREEZE) setInterval(() => {
  const s = jobS; const j = s.jobs[0];
  if (j.status === 'running' && j.progress < 1) {
    j.progress = Math.min(1, +(j.progress + 0.03).toFixed(2)); j.phase = `Denoising, step ${Math.round(j.progress * 50)} of 50`;
    ev(s, 'job', { job_id: j.id, job_kind: 'video', model: j.model, status: j.status, phase: j.phase, progress: j.progress }, null);
  }
}, 3000);

async function handle(req, res) {
  const url = new URL(req.url, 'http://x'); const p = url.pathname; const m = req.method;
  if (!p.startsWith('/api/')) return serveStatic(res, p);
  if (p === '/api/public') return json(res, 200, { needs_setup: false, brand: {}, privacy_notice: '', theme: { default: 'system', follow_system: true }, signup: { open: false, needs_invite: false, requires_approval: false, message: '' } });
  if (p === '/api/me') return json(res, 200, me());
  if (p === '/api/chat/conversations') return json(res, 200, []);
  if (!p.startsWith('/api/work/')) return fail_(res, 404, 'Not part of the Work Mode mock.');
  const w = p.slice('/api/work'.length); let g;

  if (w === '/options') return json(res, 200, { models, skills: skills.filter((s) => s.enabled).map((s) => skillOut(s)), limits: { upload_max_mb: 25, workspace_max_mb: 2048, max_sessions_per_user: 5, max_steps: 40 }, network_allowed: !process.env.MOCK_NO_NET, perms: PERMS, image_ready: true });
  if (w === '/sessions' && m === 'GET') return json(res, 200, [...sessions.values()].map(sessionOut));
  if (w === '/sessions' && m === 'POST') {
    const b = await readJson(req);
    if (b.model_id && models.find((x) => x.id === b.model_id)?.ready === false) return fail_(res, 409, 'That model is not loaded and cannot be started right now.');
    const id = 'ws_' + Math.random().toString(36).slice(2, 8);
    const s = mk(id, { title: b.title || 'Untitled session', objective: b.objective, model_id: b.model_id, status: 'idle', container: 'absent', network: b.network === 'internet' ? 'internet' : 'none', autonomy: b.autonomy, skills: b.skill_ids, created: 0, active: 0 });
    s.fs = new Map(); return json(res, 201, sessionOut(s));
  }
  if ((g = /^\/sessions\/([^/]+)(\/.*)?$/.exec(w))) {
    const [, sid, sub = ''] = g;
    if (sid === 'ws_403') return fail_(res, 403, 'This session belongs to another person.', 'forbidden');
    const s = sessions.get(sid); if (!s) return fail_(res, 404, 'Session not found.', 'not_found');
    if (sub === '' && m === 'GET') return json(res, 200, sessionOut(s));
    if (sub === '' && m === 'PATCH') { Object.assign(s, await readJson(req)); return json(res, 200, sessionOut(s)); }
    if (sub === '' && m === 'DELETE') { sessions.delete(sid); return json(res, 200, { ok: true }); }
    if (sub === '/state') return json(res, 200, stateOf(s));
    if (sub === '/events') {
      const since = Number(url.searchParams.get('since') || 0); const wait = Math.min(25, Number(url.searchParams.get('wait') || 0));
      const out = () => json(res, 200, { events: s.events.filter((e) => e.id > since), last_id: s.nid });
      if (s.nid > since || wait <= 0) return out();
      let done = false; const set = waiters.get(sid) ?? new Set(); waiters.set(sid, set);
      const fin = () => { if (done) return; done = true; set.delete(fin); clearTimeout(t); out(); };
      const t = setTimeout(fin, wait * 1000); set.add(fin); req.on('close', () => { done = true; set.delete(fin); clearTimeout(t); });
      return;
    }
    if (sub === '/messages' && m === 'POST') {
      const b = await readJson(req);
      if (String(b.content).includes('trigger-limit')) return fail_(res, 429, 'You can send 20 messages a minute to a session. Try again in 30 seconds.', 'rate_limited');
      ev(s, 'message', { role: 'user', content: b.content }, null); s.status = 'running'; ev(s, 'state', { status: 'running' }, null); s.container_state = s.container_state === 'absent' ? 'running' : s.container_state;
      setTimeout(() => { ev(s, 'message', { role: 'assistant', content: `Mock reply to: “${String(b.content).slice(0, 80)}”. In the real backend the model would work here.` }, null); s.status = 'idle'; ev(s, 'state', { status: 'idle' }, null); }, 2500);
      return json(res, 202, { queued: true });
    }
    if (g = /^\/(pause|resume|cancel)$/.exec(sub)) { s.status = { pause: 'paused', resume: 'running', cancel: 'cancelled' }[g[1]]; ev(s, 'state', { status: s.status }, null); return json(res, 200, { status: s.status }); }
    if (g = /^\/approvals\/([^/]+)$/.exec(sub)) {
      const b = await readJson(req); const last = [...s.events].reverse().find((e) => e.kind === 'approval' && String(e.data.step) === g[1]);
      if (!last) return fail_(res, 404, 'No such approval.');
      ev(s, 'approval', { ...last.data, status: b.approve ? 'approved' : 'denied' }, null, { step: last.step_n });
      s.status = 'running'; ev(s, 'state', { status: 'running' }, null);
      setTimeout(() => { ev(s, 'message', { role: 'assistant', content: b.approve ? 'The push went through.' : 'Understood, I will leave the commit local.' }, null); s.status = 'idle'; ev(s, 'state', { status: 'idle' }, null); }, 2000);
      return json(res, 200, { ok: true });
    }
    if (sub === '/files' && m === 'GET') return json(res, 200, dirList(s, url.searchParams.get('path') || ''));
    if (sub === '/files' && m === 'DELETE') { const q = url.searchParams.get('path'); for (const k of [...s.fs.keys()]) if (k === q || k.startsWith(q + '/')) s.fs.delete(k); return json(res, 200, { ok: true }); }
    if (sub === '/files' && m === 'PUT') {
      const mp = multipart(await readBody(req), req.headers['content-type']); if (!mp.file) return fail_(res, 422, 'No file was sent.');
      if (mp.file.data.length > 2 * 1024 * 1024) return fail_(res, 413, `${mp.file.name} is ${(mp.file.data.length / 1048576).toFixed(1)} MB. This workspace accepts files up to 2 MB at a time (mock limit).`, 'too_large');
      const dir = url.searchParams.get('path') || ''; s.fs.set((dir ? dir + '/' : '') + mp.file.name, { content: mp.file.data, mime: /\.png$/i.test(mp.file.name) ? 'image/png' : 'text/plain', modified: iso() });
      return json(res, 200, { ok: true });
    }
    if (sub === '/files/content') {
      const f = s.fs.get(url.searchParams.get('path') || ''); if (!f) return fail_(res, 404, 'No such file.');
      res.writeHead(200, { 'content-type': f.mime, ...(url.searchParams.get('download') ? { 'content-disposition': 'attachment' } : {}) }); return res.end(f.content);
    }
    if (sub === '/artifacts') return json(res, 200, s.artifacts.filter((a) => !a.hidden || s.events.some((e) => e.kind === 'artifact' && e.data.artifact_id === a.id)).map(({ hidden, ...a }) => a));
  }
  if ((g = /^\/artifacts\/([^/]+)\/(content|save-to-library)$/.exec(w))) {
    const a = [...sessions.values()].flatMap((s) => s.artifacts).find((x) => x.id === g[1]); if (!a) return fail_(res, 404, 'No such artifact.');
    if (g[2] === 'save-to-library') { a.saved_to_library = true; return json(res, 200, { ok: true }); }
    res.writeHead(200, { 'content-type': a.mime }); return res.end(artifactBytes(a));
  }
  // skills
  if (w === '/skills' && m === 'GET') return json(res, 200, skills.map((s) => skillOut(s)));
  if (w === '/skills' && m === 'POST') {
    const b = await readJson(req);
    if (b.scope === 'org' && !MANAGE) return fail_(res, 403, 'You do not have permission to manage organisation skills.', 'forbidden');
    if (skills.some((s) => s.scope === b.scope && s.name === b.name)) return fail_(res, 409, `A ${b.scope === 'org' ? 'organisation' : 'personal'} skill named "${b.name}" already exists.`, 'conflict');
    const s = { id: 'sk_n' + nextSkill++, scope: b.scope, name: b.name, title: b.title, description: b.description, category: b.category, enabled: b.enabled !== false, body_md: b.body_md, files: b.files ?? [], updated_at: iso() }; skills.unshift(s); return json(res, 201, skillOut(s, true));
  }
  if (w === '/skills/import' && m === 'POST') {
    const mp = multipart(await readBody(req), req.headers['content-type']); const text = mp.file.data.toString('utf8');
    const fm = /^---\n([\s\S]*?)\n---\n?([\s\S]*)$/.exec(text.replace(/\r\n/g, '\n')); if (!fm) return fail_(res, 422, 'The file has no front matter.');
    const meta = Object.fromEntries(fm[1].split('\n').map((l) => l.split(/:\s*(.*)/)).filter((x) => x[0]).map(([k, v]) => [k, (v ?? '').replace(/^["']|["']$/g, '')]));
    if (skills.some((s) => s.scope === mp.fields.scope && s.name === meta.name)) return fail_(res, 409, `A skill named "${meta.name}" already exists.`);
    const s = { id: 'sk_n' + nextSkill++, scope: mp.fields.scope || 'user', name: meta.name, title: meta.title || meta.name, description: meta.description, category: meta.category || 'general', enabled: true, body_md: fm[2].trim(), files: [], updated_at: iso() }; skills.unshift(s); return json(res, 201, skillOut(s, true));
  }
  if ((g = /^\/skills\/([^/]+)(?:\/(enable|disable))?$/.exec(w))) {
    const s = skills.find((x) => x.id === g[1]); if (!s) return fail_(res, 404, 'Skill not found.', 'not_found');
    if (m === 'GET') return json(res, 200, skillOut(s, true));
    if (!canEditSkill(s)) return fail_(res, 403, 'Only people who manage organisation skills can change this skill.', 'forbidden');
    if (g[2]) { s.enabled = g[2] === 'enable'; return json(res, 200, skillOut(s)); }
    if (m === 'PUT') { const b = await readJson(req); Object.assign(s, { name: b.name, title: b.title, description: b.description, category: b.category, enabled: b.enabled, body_md: b.body_md, files: b.files ?? [], updated_at: iso() }); return json(res, 200, skillOut(s, true)); }
    if (m === 'DELETE') { skills.splice(skills.indexOf(s), 1); return json(res, 200, { ok: true }); }
  }
  return fail_(res, 404, 'Unknown route in the mock.');
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.mjs': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.woff': 'font/woff', '.json': 'application/json', '.png': 'image/png', '.ico': 'image/x-icon' };
function serveStatic(res, p) {
  let f = path.join(STATIC, decodeURIComponent(p)); if (!f.startsWith(STATIC)) f = STATIC;
  if (!fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(STATIC, 'index.html');
  res.writeHead(200, { 'content-type': TYPES[path.extname(f)] ?? 'application/octet-stream' }); fs.createReadStream(f).pipe(res);
}
http.createServer((req, res) => handle(req, res).catch((e) => { console.error(e); if (!res.headersSent) fail_(res, 500, 'Mock server error: ' + e.message); })).listen(PORT, '127.0.0.1', () => console.log(`work mock on http://127.0.0.1:${PORT} (static: ${STATIC}${MANAGE ? ', skills manager' : ''})`));
