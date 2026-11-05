// A stand-in for Semechka's /api/studio/jobs/{job}/... routes, serving ScriptedRun documents over real HTTP: real Server-Sent Events
// (with  ids and Last-Event-ID), real Range requests on real MP4 files, real JPEG previews. The test decides when the run moves
// (advance / advanceTo) and when connections break (dropStreams), so every state can be looked at and every failure produced on cue.
import http from 'node:http';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, statSync, createReadStream } from 'node:fs';
import path from 'node:path';
import { ScriptedRun, completedRun, failedRun } from './run.ts';

export function makeMedia(dir: string) {
  mkdirSync(dir, { recursive: true });
  const seg = path.join(dir, 'segment.mp4'), prev = path.join(dir, 'preview.jpg'), fin = path.join(dir, 'final.mp4');
  const ff = (args: string[], out: string) => { if (!existsSync(out)) execFileSync('ffmpeg', ['-nostdin', '-loglevel', 'error', '-y', ...args, out]); };
  ff(['-f', 'lavfi', '-i', 'testsrc2=size=736x1312:rate=24', '-frames:v', '141', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '38', '-pix_fmt', 'yuv420p', '-movflags', '+faststart'], seg);
  ff(['-f', 'lavfi', '-i', 'mandelbrot=size=368x656', '-frames:v', '1', '-q:v', '5'], prev);
  ff(['-f', 'lavfi', '-i', 'testsrc=size=368x656:rate=24:duration=3', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-movflags', '+faststart'], fin);
  return { seg, prev, fin };
}

interface Stream { res: http.ServerResponse; job: string; last: number }

export class MockStudio {
  runs = new Map<string, ScriptedRun>();
  server!: http.Server;
  port = 0;
  streams = new Set<Stream>();
  stats = { inspect: 0, streamOpens: 0, lastEventIds: [] as (string | undefined)[], graph: 0, history: 0, preview: 0, segments: [] as string[], afters: [] as number[] };
  streamEnabled = true;
  /** 'all': a fresh stream starts with every event (up to 500); 'none': only the summary */
  streamFirst: 'all' | 'none' = 'none';
  inspectFailures = 0;
  /** the next N stream requests answer 200 and end at once (a flaky network: the browser reconnects after `retryMs`) */
  streamBreaks = 0;
  retryMs = 400;
  previewEnabled = true;
  historyEnabled = true;
  staleNext = false;
  readonly media: { seg: string; prev: string; fin: string };
  /** everything that is not /api/... (the harness page and its modules) is handed to this, e.g. Vite's middleware */
  fallback?: (req: http.IncomingMessage, res: http.ServerResponse) => void;
  forbidden = new Set<string>();
  missing = new Set<string>();
  noTrace = new Set<string>();
  noGraph = new Set<string>();

  constructor(mediaDir: string) { this.media = makeMedia(mediaDir); }

  reset() {
    this.runs.clear(); this.stats = { inspect: 0, streamOpens: 0, lastEventIds: [], graph: 0, history: 0, preview: 0, segments: [], afters: [] };
    this.streamEnabled = true; this.streamBreaks = 0; this.retryMs = 400; this.streamFirst = 'none'; this.inspectFailures = 0; this.previewEnabled = true; this.historyEnabled = true; this.staleNext = false;
    this.forbidden.clear(); this.missing.clear(); this.noTrace.clear(); this.noGraph.clear();
    this.dropStreams();
    this.add(new ScriptedRun('j_run'));
    this.add(completedRun('j_done'));
    this.add(failedRun('j_failed'));
  }
  add(r: ScriptedRun) { this.runs.set(r.jobId, r); return r; }
  run(id = 'j_run') { return this.runs.get(id)!; }

  advanceTo(tag: string, job = 'j_run') { this.run(job).advanceTo(tag); this.broadcast(job); }
  advance(n = 1, job = 'j_run') { this.run(job).advance(n); this.broadcast(job); }
  fail(job = 'j_run', message?: string) { this.run(job).fail(message); this.broadcast(job); }

  broadcast(job: string) {
    const r = this.runs.get(job); if (!r) return;
    for (const s of this.streams) if (s.job === job) this.send(s, r);
  }
  private send(s: Stream, r: ScriptedRun) {
    const d = r.inspect(s.last);
    const nxt = d.next_seq ?? 0;
    s.res.write(`id: ${nxt}\nevent: inspect\ndata: ${JSON.stringify(d)}\n\n`);
    if (d.events?.length) s.last = d.events[d.events.length - 1].seq;
  }
  sendStale(job = 'j_run', reason = 'The inference engine is not answering.') { for (const s of this.streams) if (s.job === job) s.res.write(`event: stale\ndata: ${JSON.stringify({ reason })}\n\n`); }
  dropStreams() { for (const s of [...this.streams]) { s.res.destroy(); this.streams.delete(s); } }
  openStreams(job = 'j_run') { return [...this.streams].filter((s) => s.job === job).length; }

  async listen(port: number) {
    this.server = http.createServer((req, res) => this.handle(req, res));
    await new Promise<void>((r) => this.server.listen(port, '127.0.0.1', r));
    this.port = port;
  }
  async close() { this.dropStreams(); await new Promise<void>((r) => { this.server.closeAllConnections?.(); this.server.close(() => r()); }); }

  private json(res: http.ServerResponse, code: number, body: unknown) { res.writeHead(code, { 'content-type': 'application/json', 'cache-control': 'no-store' }); res.end(JSON.stringify(body)); }
  private err(res: http.ServerResponse, code: number, c: string, message: string) { this.json(res, code, { detail: { code: c, message } }); }

  private file(req: http.IncomingMessage, res: http.ServerResponse, file: string, type: string) {
    const size = statSync(file).size;
    const range = /bytes=(\d*)-(\d*)/.exec(req.headers.range ?? '');
    if (range) {
      const start = range[1] ? Number(range[1]) : 0, end = range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
      res.writeHead(206, { 'content-type': type, 'accept-ranges': 'bytes', 'content-range': `bytes ${start}-${end}/${size}`, 'content-length': end - start + 1 });
      createReadStream(file, { start, end }).pipe(res);
    } else { res.writeHead(200, { 'content-type': type, 'accept-ranges': 'bytes', 'content-length': size }); createReadStream(file).pipe(res); }
  }

  private handle(req: http.IncomingMessage, res: http.ServerResponse) {
    const url = new URL(req.url ?? '/', 'http://x');
    const m = /^\/api\/studio\/jobs\/([^/]+)\/(inspect\/stream|inspect|graph|history|preview|segments\/(\d+))$/.exec(url.pathname);
    if (url.pathname.startsWith('/api/media/assets/')) return this.file(req, res, this.media.fin, 'video/mp4');
    if (!m && !url.pathname.startsWith('/api/') && this.fallback) return this.fallback(req, res);
    if (!m) return this.err(res, 404, 'not_found', 'Not found.');
    const job = decodeURIComponent(m![1]), what = m![2];
    if (this.forbidden.has(job)) return this.err(res, 403, 'forbidden', 'You do not have access to this job.');
    const r = this.runs.get(job);
    if (!r || this.missing.has(job)) return this.err(res, 404, 'not_found', 'Not found.');
    if (what === 'inspect/stream') {
      this.stats.streamOpens++; this.stats.lastEventIds.push(req.headers['last-event-id'] as string | undefined);
      if (!this.streamEnabled) { res.writeHead(503, { 'content-type': 'application/json' }); return res.end('{"detail":"down"}'); }
      res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-store', connection: 'keep-alive', 'x-accel-buffering': 'no' });
      res.write(`retry: ${this.retryMs}\n\n`);
      if (this.streamBreaks > 0) { this.streamBreaks--; return res.end(); }
      const lei = req.headers['last-event-id'];
      const s: Stream = { res, job, last: lei ? Math.max(0, Number(lei) - 1) : this.streamFirst === 'all' ? 0 : Math.max(0, r.seqNow() - 1) };
      this.streams.add(s);
      req.on('close', () => this.streams.delete(s));
      return this.send(s, r);
    }
    if (what === 'inspect') {
      this.stats.inspect++;
      const after = Number(url.searchParams.get('after') ?? 0) || 0; this.stats.afters.push(after);
      if (this.inspectFailures > 0) { this.inspectFailures--; return this.err(res, 503, 'engine_down', 'The engine is not answering.'); }
      if (this.noTrace.has(job)) return this.json(res, 200, noTraceDoc(r));
      if (this.staleNext) return this.json(res, 200, { ...noTraceDoc(r), stale: true, trace_available: null, stale_reason: 'The inference engine is not answering.', status: 'running', user_status: 'running' });
      return this.json(res, 200, { ...r.inspect(after), comfy: { available: !this.noGraph.has(job) }, user_status: r.doc.status, trace_available: true, stale: false });
    }
    if (what === 'graph') { this.stats.graph++; return this.noGraph.has(job) ? this.err(res, 404, 'no_graph', 'This model does not use ComfyUI.') : this.json(res, 200, r.graph()); }
    if (what === 'history') { this.stats.history++; return this.historyEnabled ? this.json(res, 200, r.history()) : this.err(res, 404, 'no_history', 'Gone.'); }
    if (what === 'preview') {
      this.stats.preview++;
      return this.previewEnabled && r.doc.scenes!.segments!.length ? this.file(req, res, this.media.prev, 'image/jpeg') : this.err(res, 404, 'no_preview', 'No preview yet.');
    }
    const n = Number(m![3]);
    this.stats.segments.push(`${job}/${n}`);
    const seg = r.doc.scenes!.segments!.find((s) => s.index === n && s.state === 'saved');
    if (!seg) return this.err(res, 404, 'not_found', 'That scene is not saved yet.');
    return this.file(req, res, this.media.seg, 'video/mp4');
  }
}

/** What Semechka answers when the engine no longer has a job's trace: its own record of the job and nothing invented. */
export function noTraceDoc(r: ScriptedRun) {
  return { job_id: r.jobId, backend: null, profile: null, operation: 'swap', status: 'completed', stage: 'done', stage_label: 'Done', prompt_id: null, started_at: 1_793_000_002, updated_at: 1_793_000_900,
    elapsed_s: 898, progress: { fraction: 1, indeterminate: false, basis: 'job_state' }, sampling: null, scenes: { total: null, completed: null, current: null, segments: [] }, loop: null, nodes: null, node_states: {},
    conditioning: { video: null, references: [], agree: null }, memory: null, output: { state: 'ready', file: { width: 736, height: 1312, duration_s: 41.1, bytes: 8_229_332, has_audio: null } }, error: null,
    events: [], next_seq: 0, trace_available: false, stale: false, comfy: { available: false }, user_status: 'completed' };
}
