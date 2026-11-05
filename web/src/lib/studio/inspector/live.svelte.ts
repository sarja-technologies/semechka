// The live view of one job, shared by the Process inspector and the ComfyUI view (one subscription per job id).
//
// Source of truth is the server: GET /api/studio/jobs/{id}/inspect?after=<seq> is a full summary plus the events after
// `after`, so refreshing the page, reconnecting or switching to polling all rebuild exactly the same state. Nothing
// important lives only in memory. Transport: EventSource on .../inspect/stream (event "inspect"); when it keeps failing
// or does not exist, poll every 1.5 s. While the stream is down we keep polling, and say so.
import { api, ApiError } from '../../api';
import { EVENT_PAGE, isFinal, isTerminalStatus, maxSeq, mergeEvents, outputReady } from './logic.ts';
import type { GraphDoc, HistoryDoc, InspectDoc, InspectEvent, InspectSummary } from './types.ts';

export type LivePhase = 'loading' | 'live' | 'polling' | 'reconnecting' | 'ended' | 'denied' | 'notfound' | 'error' | 'paused';
export type Loadable = 'idle' | 'loading' | 'ready' | 'none' | 'denied' | 'error';

const POLL_MS = 1500, SLOW_POLL_MS = 3000, STREAM_ERRORS_BEFORE_POLLING = 3, POLL_FOR_MS = 45_000, HIDDEN_PAUSE_MS = 20_000, MAX_SETTLE_POLLS = 40;
const enc = encodeURIComponent;
const sleep = (ms: number, s: { wake?: () => void }) => new Promise<void>((res) => { const t = setTimeout(res, ms); s.wake = () => { clearTimeout(t); res(); }; });

export class LiveInspect {
  readonly jobId: string;
  /** the newest summary (the document minus its events) */
  doc = $state.raw<InspectSummary | null>(null);
  events = $state.raw<InspectEvent[]>([]);
  phase = $state<LivePhase>('loading');
  transport = $state<'stream' | 'poll' | 'none'>('none');
  message = $state('');
  /** Date.now() when `doc` last changed: the origin the live clock ticks from */
  receivedAt = $state(0);
  /** the last time any answer from the server arrived (stream message or poll) */
  lastContact = $state(0);
  /** the final document (terminal and, for completed, output ready) was received: nothing more will change */
  final = $state(false);
  /** completed by the engine but the output file was never reported ready within the patience window */
  unsettled = $state(false);
  fails = $state(0);
  /** the engine is not answering; the panel shows the last good state and this reason */
  stale = $state.raw<{ reason: string; at: number } | null>(null);

  graph = $state.raw<GraphDoc | null>(null);
  graphState = $state<Loadable>('idle');
  graphMessage = $state('');
  history = $state.raw<HistoryDoc | null>(null);
  historyState = $state<Loadable>('idle');
  historyMessage = $state('');

  private gen = 0;
  private es: EventSource | null = null;
  private knownNext = 0;
  private wakeObj: { wake?: () => void } = {};
  private endStream: (() => void) | null = null;
  private pollUntil = 0;
  private settlePolls = 0;
  private hiddenTimer: ReturnType<typeof setTimeout> | null = null;
  private running = false;
  private catching: Promise<void> | null = null;
  private onVis = () => {
    if (typeof document === 'undefined') return;
    if (document.hidden) { if (!this.hiddenTimer && !this.final) this.hiddenTimer = setTimeout(() => { this.hiddenTimer = null; this.suspend(); }, HIDDEN_PAUSE_MS); }
    else { if (this.hiddenTimer) { clearTimeout(this.hiddenTimer); this.hiddenTimer = null; } if (this.phase === 'paused') this.start(); }
  };

  constructor(jobId: string) { this.jobId = jobId; }

  get url() { return `/api/studio/jobs/${enc(this.jobId)}`; }

  start() {
    if (this.running || this.final) return;
    this.running = true;
    const gen = ++this.gen;
    if (typeof document !== 'undefined') document.addEventListener('visibilitychange', this.onVis);
    if (this.phase === 'paused') this.phase = this.doc ? 'live' : 'loading';
    void this.run(gen);
  }

  stop() {
    this.gen++;
    this.running = false;
    this.teardownStream();
    this.wakeObj.wake?.();
    if (this.hiddenTimer) { clearTimeout(this.hiddenTimer); this.hiddenTimer = null; }
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', this.onVis);
  }

  /** Tab hidden for a while: stop talking to the server; state stays, and start() catches up when the tab returns. */
  private suspend() {
    this.gen++; this.running = false; this.teardownStream(); this.wakeObj.wake?.();
    this.phase = 'paused'; this.transport = 'none';
  }

  /** Ask for news right now (e.g. the network came back). */
  poke() { this.wakeObj.wake?.(); }

  private teardownStream() { this.endStream?.(); this.endStream = null; this.es?.close(); this.es = null; }

  // ---- ingest -----------------------------------------------------------------------------------------------------

  /** Take one inspect document. Returns the seq to catch up from when events are missing between what we held and what arrived. */
  private ingest(raw: InspectDoc): number | null {
    if (!raw || typeof raw !== 'object') return null;
    // The engine is not answering: Semechka sent what it knows. Keep the last good picture and say so.
    if (raw.stale) {
      this.stale = { reason: raw.stale_reason || 'The inference engine is not answering.', at: Date.now() };
      this.lastContact = Date.now();
      if (!this.doc) { const { events: _e, ...summary } = raw; this.doc = summary; this.receivedAt = Date.now(); }
      return null;
    }
    this.stale = null;
    const { events, ...summary } = raw;
    const cur = this.doc;
    // never let an older answer (a slow poll overtaken by the stream) overwrite a newer summary
    if (!cur || cur.stale || (summary.updated_at ?? 0) >= (cur.updated_at ?? 0)) { this.doc = summary; this.receivedAt = Date.now(); }
    this.lastContact = Date.now();
    const held = maxSeq(this.events);
    let gap: number | null = null;
    if (events?.length) {
      if (held > 0 && events[0].seq > held + 1) gap = held;            // events between what we hold and this page never arrived
      this.events = mergeEvents(this.events, events);
    }
    const lastKnown = raw.last_seq ?? (raw.next_seq != null ? raw.next_seq - 1 : null);
    if (gap == null && lastKnown != null && lastKnown > maxSeq(this.events) && (raw.next_seq ?? 0) > this.knownNext) gap = maxSeq(this.events);
    const full = (events?.length ?? 0) >= EVENT_PAGE;
    if (this.doc && isFinal(this.doc) && !full && gap == null) this.final = true;
    return full && gap == null ? maxSeq(this.events) : gap;
  }

  private async fetchDoc(after: number, signal?: AbortSignal): Promise<InspectDoc> {
    return api<InspectDoc>('GET', `${this.url}/inspect?after=${after}`, undefined, signal);
  }

  /** Read the events after `from` (default: everything we hold) in pages of 500 until the server has nothing more. */
  private async catchUp(from?: number): Promise<void> {
    if (this.catching) return this.catching;
    this.catching = (async () => {
      let after = from ?? maxSeq(this.events);
      for (let i = 0; i < 12; i++) {
        const d = await this.fetchDoc(after);
        this.knownNext = Math.max(this.knownNext, d.next_seq ?? 0);
        this.ingest(d);
        this.fails = 0;
        const evs = d.events ?? [];
        if (evs.length < EVENT_PAGE) break;
        after = evs[evs.length - 1].seq;
      }
    })().finally(() => { this.catching = null; });
    return this.catching;
  }

  /** First load: from a cold start take the newest ~1000 events, not the oldest. */
  private async initial(gen: number) {
    for (let attempt = 0; gen === this.gen; attempt++) {
      try {
        let d = await this.fetchDoc(maxSeq(this.events));
        if (gen !== this.gen) return;
        if (!this.events.length && (d.events?.length ?? 0) >= EVENT_PAGE && d.next_seq != null && d.next_seq - 1 > maxSeq(d.events ?? [])) {
          const jump = Math.max(0, d.next_seq - 1 - 1000);
          d = await this.fetchDoc(jump);
          if (gen !== this.gen) return;
        }
        this.knownNext = Math.max(this.knownNext, d.next_seq ?? 0);
        const more = this.ingest(d);
        this.fails = 0; this.message = '';
        if (more != null) await this.catchUp(more);
        if (this.final) { this.phase = 'ended'; this.transport = 'none'; }
        else this.phase = 'live';
        return;
      } catch (e) {
        if (gen !== this.gen) return;
        if (this.fatal(e)) return;
        this.fails++;
        this.phase = this.doc ? 'reconnecting' : attempt >= 2 ? 'error' : 'loading';
        if (attempt >= 2) this.message = e instanceof ApiError ? e.message : 'Cannot reach the server.';
        await sleep(Math.min(15_000, 1000 * 2 ** Math.min(attempt, 4)), this.wakeObj);
      }
    }
  }

  /** 403/404 are answers, not outages: say what they mean and stop. */
  private fatal(e: unknown): boolean {
    if (e instanceof ApiError && (e.status === 403 || e.status === 404)) {
      this.teardownStream();
      this.phase = e.status === 403 ? 'denied' : 'notfound';
      this.message = e.status === 403 ? 'You do not have access to this job.' : 'This job was not found. It may belong to someone else, or it has been removed.';
      this.running = false; this.transport = 'none';
      return true;
    }
    if (e instanceof ApiError && e.status === 401) { this.phase = 'denied'; this.message = 'Your session has ended. Sign in again to see this job.'; this.running = false; return true; }
    return false;
  }

  // ---- transports -------------------------------------------------------------------------------------------------

  private async run(gen: number) {
    await this.initial(gen);
    while (gen === this.gen && this.running && !this.final) {
      if (typeof EventSource !== 'undefined' && Date.now() >= this.pollUntil) await this.streamSession(gen);
      else await this.pollSession(gen);
    }
    if (gen === this.gen && this.final) { this.phase = 'ended'; this.transport = 'none'; this.running = false; this.teardownStream(); if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', this.onVis); }
  }

  private streamSession(gen: number): Promise<void> {
    return new Promise<void>((resolve) => {
      const es = new EventSource(`${this.url}/inspect/stream`);
      this.es = es;
      let errors = 0;
      let catchTimer: ReturnType<typeof setInterval> | null = null;
      const done = () => { if (catchTimer) clearInterval(catchTimer); catchTimer = null; es.close(); if (this.es === es) this.es = null; this.endStream = null; resolve(); };
      this.endStream = done;
      es.addEventListener('open', () => {
        if (gen !== this.gen) return done();
        errors = 0; if (catchTimer) { clearInterval(catchTimer); catchTimer = null; }
        this.phase = 'live'; this.transport = 'stream'; this.message = '';
      });
      es.addEventListener('inspect', (ev) => {
        if (gen !== this.gen) return done();
        let d: InspectDoc;
        try { d = JSON.parse((ev as MessageEvent).data); } catch { return; }
        errors = 0; this.fails = 0;
        this.phase = 'live'; this.transport = 'stream'; this.message = '';
        const more = this.ingest(d);
        if (!this.final && more != null) void this.catchUp(more).catch(() => {});
        else if (!this.final) this.knownNext = Math.max(this.knownNext, d.next_seq ?? 0);
        if (this.final) done();
        else if (isTerminalStatus(d.status) && !d.stale) { this.pollUntil = Date.now() + POLL_FOR_MS; done(); } // terminal but the output is not ready yet: wait by polling
      });
      // the engine stopped answering: the server keeps the stream open and says so
      es.addEventListener('stale', (ev) => {
        if (gen !== this.gen) return done();
        let reason = 'The inference engine is not answering.';
        try { reason = JSON.parse((ev as MessageEvent).data)?.reason || reason; } catch { /* keep the default */ }
        this.stale = { reason, at: Date.now() }; this.lastContact = Date.now();
      });
      es.addEventListener('gone', () => {
        if (gen !== this.gen) return;
        this.teardownStream();
        this.phase = 'notfound'; this.message = 'This job was not found. It may belong to someone else, or it has been removed.'; this.running = false; this.transport = 'none';
      });
      es.onerror = () => {
        if (gen !== this.gen) return done();
        errors++; this.fails++;
        this.phase = 'reconnecting';
        this.message = 'The live connection dropped. Trying again; checking for news meanwhile.';
        // while the browser retries the stream (it resends Last-Event-ID itself) keep the picture fresh by polling
        if (!catchTimer) catchTimer = setInterval(() => { void this.catchUp().catch((e) => { this.fatal(e); }); }, 3000);
        if (errors >= STREAM_ERRORS_BEFORE_POLLING || es.readyState === 2) { this.pollUntil = Date.now() + POLL_FOR_MS; done(); }
      };
    });
  }

  private async pollSession(gen: number) {
    this.transport = 'poll';
    const forever = typeof EventSource === 'undefined';
    while (gen === this.gen && this.running && !this.final && (forever || Date.now() < this.pollUntil)) {
      try {
        const d = await this.fetchDoc(maxSeq(this.events));
        if (gen !== this.gen) return;
        this.knownNext = Math.max(this.knownNext, d.next_seq ?? 0);
        const more = this.ingest(d);
        if (more != null) await this.catchUp(more);
        this.fails = 0; this.message = '';
        this.phase = 'polling';
        if (this.doc && !this.doc.stale && isTerminalStatus(this.doc.status) && !outputReady(this.doc) && !this.final) {
          if (++this.settlePolls > MAX_SETTLE_POLLS) { this.unsettled = true; this.final = true; break; }
          await sleep(SLOW_POLL_MS, this.wakeObj);
          continue;
        }
      } catch (e) {
        if (gen !== this.gen) return;
        if (this.fatal(e)) return;
        this.fails++; this.phase = 'reconnecting';
        this.message = 'Cannot reach the server. Trying again.';
        await sleep(Math.min(15_000, 1000 * 2 ** Math.min(this.fails, 4)), this.wakeObj);
        continue;
      }
      if (!this.final) await sleep(POLL_MS, this.wakeObj);
    }
  }

  // ---- graph and history (on demand, shared) ----------------------------------------------------------------------

  async ensureGraph(force = false) {
    if (!force && (this.graphState === 'loading' || this.graphState === 'ready' || this.graphState === 'none')) return;
    this.graphState = 'loading'; this.graphMessage = '';
    try {
      this.graph = await api<GraphDoc>('GET', `${this.url}/graph`);
      this.graphState = 'ready';
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        this.graphState = 'none';
        this.graphMessage = e.code === 'no_graph' ? 'This model does not run on ComfyUI, so there is no workflow graph to show.' : 'The engine no longer has the workflow graph for this job.';
      } else if (e instanceof ApiError && (e.status === 403 || e.status === 401)) { this.graphState = 'denied'; this.graphMessage = 'You do not have access to this job.'; }
      else { this.graphState = 'error'; this.graphMessage = e instanceof ApiError ? e.message : 'Could not load the graph.'; }
    }
  }

  async loadHistory(force = false) {
    if (!force && (this.historyState === 'loading' || this.historyState === 'ready')) return;
    this.historyState = 'loading'; this.historyMessage = '';
    try {
      this.history = await api<HistoryDoc>('GET', `${this.url}/history`);
      this.historyState = 'ready';
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) { this.historyState = 'none'; this.historyMessage = 'ComfyUI no longer has a history entry for this job.'; }
      else { this.historyState = 'error'; this.historyMessage = e instanceof ApiError ? e.message : 'Could not load the history.'; }
    }
  }
}

// ---- one shared subscription per job ------------------------------------------------------------------------------

interface Entry { live: LiveInspect; refs: number; timer: ReturnType<typeof setTimeout> | null }
const registry = new Map<string, Entry>();

export interface LiveHandle { live: LiveInspect; release: () => void }

/** Get the shared live view of a job and start it. Call release() when the component goes away; the connection closes
 *  a few seconds later unless another component took it over (switching between the two panels keeps one stream). */
export function acquireLive(jobId: string): LiveHandle {
  let e = registry.get(jobId);
  if (!e) { e = { live: new LiveInspect(jobId), refs: 0, timer: null }; registry.set(jobId, e); }
  if (e.timer) { clearTimeout(e.timer); e.timer = null; }
  e.refs++;
  e.live.start();
  const entry = e;
  let released = false;
  return {
    live: entry.live,
    release() {
      if (released) return; released = true;
      entry.refs--;
      if (entry.refs <= 0) entry.timer = setTimeout(() => { if (entry.refs <= 0) { entry.live.stop(); registry.delete(jobId); } }, 4000);
    }
  };
}
