// Pure logic of the job inspector: merging the event stream, deciding what "completed" means, deriving the scene track,
// node lists and plain-language event lines. No DOM, no Svelte: covered by logic.test.ts (node's built-in runner).
import type { GraphDoc, InspectEvent, InspectSummary, NodeStateInfo, Segment } from './types.ts';

export const KEEP_EVENTS = 1000;
/** The engine returns at most this many events per request; a full page means there may be more. */
export const EVENT_PAGE = 500;

// ---- events -----------------------------------------------------------------------------------------------------

/** Merge `incoming` into `current` by `seq` (the later copy of a seq wins), ascending, keeping only the newest `keep`.
 *  Returns `current` itself when nothing changed, so reactive consumers do no work. */
export function mergeEvents(current: InspectEvent[], incoming: InspectEvent[] | null | undefined, keep = KEEP_EVENTS): InspectEvent[] {
  if (!incoming || !incoming.length) return current;
  const bySeq = new Map<number, InspectEvent>();
  for (const e of current) bySeq.set(e.seq, e);
  let changed = false;
  for (const e of incoming) {
    if (!e || typeof e.seq !== 'number' || !Number.isFinite(e.seq)) continue;
    const old = bySeq.get(e.seq);
    if (old === undefined || JSON.stringify(old) !== JSON.stringify(e)) { bySeq.set(e.seq, e); changed = true; }
  }
  if (!changed) return current;
  let out = [...bySeq.values()].sort((a, b) => a.seq - b.seq);
  if (out.length > keep) out = out.slice(out.length - keep);
  return out;
}

export const maxSeq = (events: InspectEvent[]): number => (events.length ? events[events.length - 1].seq : 0);

// ---- status and completion --------------------------------------------------------------------------------------

export const isTerminalStatus = (s: string | null | undefined) => s === 'completed' || s === 'failed' || s === 'cancelled';
export const outputReady = (d: InspectSummary | null | undefined) => d?.output?.state === 'ready';

export type Phase = 'unknown' | 'queued' | 'running' | 'finishing' | 'completed' | 'failed' | 'cancelled';

/** What to tell the person. "Completed" only when the engine says completed AND the output file is verified ready.
 *  A "Loop End" or a saved scene never changes this: only `status` and `output` do. */
export function phaseOf(d: InspectSummary | null | undefined): Phase {
  if (!d) return 'unknown';
  // The engine's trace is gone and this is Semechka's own record of a job it has already collected: "completed" is its word.
  if (d.trace_available === false && d.status === 'completed') return 'completed';
  switch (d.status) {
    case 'queued': return 'queued';
    case 'running': return 'running';
    case 'failed': return 'failed';
    case 'cancelled': return 'cancelled';
    case 'completed': return outputReady(d) ? 'completed' : 'finishing';
    default: return 'unknown';
  }
}

/** The final document was received: nothing more will change, so the live connection can stop.
 *  A completed job is final only once its output is ready; failed and cancelled jobs are final at once. */
export function isFinal(d: InspectSummary | null | undefined): boolean {
  if (!d || d.stale) return false;
  if (d.trace_available === false) return isTerminalStatus(d.status);
  if (d.status === 'failed' || d.status === 'cancelled') return true;
  return d.status === 'completed' && outputReady(d);
}

export const PHASE_LABEL: Record<Phase, string> = {
  unknown: 'Unknown', queued: 'Queued', running: 'Running', finishing: 'Finishing', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled'
};
export const phaseTone = (p: Phase): 'ok' | 'bad' | 'warn' | 'info' | 'plain' =>
  p === 'completed' ? 'ok' : p === 'failed' ? 'bad' : p === 'cancelled' ? 'warn' : p === 'running' || p === 'finishing' ? 'info' : 'plain';

// ---- clock ------------------------------------------------------------------------------------------------------

/** Seconds the job has been running. Terminal jobs keep the engine's final value; running jobs tick from the last
 *  elapsed value the server sent, measured on this browser's clock since it arrived (no server clock needed). */
export function elapsedNow(d: InspectSummary | null | undefined, receivedAtMs: number, nowMs: number): number | null {
  if (!d || d.status === 'queued') return null;
  const base = d.elapsed_s ?? (d.started_at != null && d.updated_at != null ? Math.max(0, d.updated_at - d.started_at) : null);
  if (base == null) return null;
  if (isTerminalStatus(d.status)) return base;
  return base + Math.max(0, nowMs - receivedAtMs) / 1000;
}

/** How long the engine has been silent while the job runs (seconds), or null when unknown. */
export function silentFor(d: InspectSummary | null | undefined, receivedAtMs: number, nowMs: number): number | null {
  if (!d || d.status !== 'running' || d.started_at == null || d.updated_at == null) return null;
  const el = elapsedNow(d, receivedAtMs, nowMs);
  return el == null ? null : Math.max(0, el - (d.updated_at - d.started_at));
}

export function fmtElapsed(s: number | null | undefined): string {
  if (s == null || !Number.isFinite(s) || s < 0) return 'not reported';
  const t = Math.floor(s);
  const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), r = t % 60;
  if (h) return `${h} h ${String(m).padStart(2, '0')} m ${String(r).padStart(2, '0')} s`;
  if (m) return `${m} m ${String(r).padStart(2, '0')} s`;
  return `${s < 10 ? s.toFixed(1) : t} s`;
}

/** "+12.3 s" / "+1 m 05 s" relative to the start of the job. */
export function relTime(t: number | null | undefined, startedAt: number | null | undefined): string {
  if (t == null || startedAt == null) return '';
  const s = Math.max(0, t - startedAt);
  if (s < 60) return `+${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return `+${m} m ${String(Math.floor(s % 60)).padStart(2, '0')} s`;
}

export function fmtMb(mb: number | null | undefined): string {
  if (mb == null || !Number.isFinite(mb)) return 'not reported';
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}
export function fmtBytes(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n) || n < 0) return 'not reported';
  if (n < 1024) return `${n} B`;
  const u = ['KB', 'MB', 'GB']; let v = n / 1024, i = 0;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v >= 100 ? Math.round(v) : v.toFixed(1)} ${u[i]}`;
}

// ---- progress ---------------------------------------------------------------------------------------------------

export interface ProgressView {
  /** true only when the engine gave a real fraction and did not call it indeterminate */
  determinate: boolean;
  /** 0..100, never 100 before the output file is ready; null when indeterminate */
  percent: number | null;
  /** plain-language basis ("2 of 7 scenes saved") or null */
  basis: string | null;
  /** the engine reported 100% but the output is not ready yet */
  held: boolean;
}

export function progressView(d: InspectSummary | null | undefined): ProgressView {
  const p = d?.progress;
  const ready = outputReady(d);
  if (!p || p.fraction == null || p.indeterminate || !Number.isFinite(p.fraction)) return { determinate: false, percent: null, basis: basisText(d), held: false };
  let pct = Math.max(0, Math.min(100, p.fraction * 100));
  let held = false;
  if (pct >= 100 && !ready) { pct = 99; held = true; }
  return { determinate: true, percent: pct, basis: basisText(d), held };
}

export function basisText(d: InspectSummary | null | undefined): string | null {
  const p = d?.progress;
  if (!p?.basis) return null;
  if (p.basis === 'scenes_saved/total_scenes') {
    const s = d?.scenes;
    if (s?.completed != null && s.total != null) return `${s.completed} of ${s.total} scenes saved`;
    return 'Scenes saved out of the total';
  }
  const plain: Record<string, string> = { output_ready: 'Output file checked', job_state: 'Job status', job_record: 'Job record' };
  return plain[p.basis] ?? `Based on ${p.basis.replace(/_/g, ' ')}`;
}

// ---- scenes -----------------------------------------------------------------------------------------------------

export type CellState = 'saved' | 'current' | 'pending' | 'writing' | 'interrupted';
export interface SceneCell { index: number; state: CellState; segment: Segment | null; unknownTotal?: boolean }

/** One cell per scene. A scene is playable only when its segment is `saved`. When the total is unknown the track shows
 *  what is known plus one open cell for "more to come" while the job runs. */
export function sceneCells(d: InspectSummary | null | undefined): SceneCell[] {
  const sc = d?.scenes;
  if (!sc) return [];
  const segs = new Map<number, Segment>();
  for (const s of sc.segments ?? []) segs.set(s.index, s);
  const terminal = isTerminalStatus(d?.status);
  const known = Math.max(sc.total ?? 0, sc.current ?? 0, ...segs.keys(), 0);
  const n = known;
  const cells: SceneCell[] = [];
  for (let i = 1; i <= n; i++) {
    const seg = segs.get(i) ?? null;
    let state: CellState;
    if (seg && seg.state === 'saved') state = 'saved';
    else if (seg) state = 'writing';
    else if (sc.current === i) state = terminal && d?.status !== 'completed' ? 'interrupted' : 'current';
    else state = 'pending';
    cells.push({ index: i, state, segment: seg });
  }
  if (sc.total == null && !terminal && cells.length) cells.push({ index: n + 1, state: 'pending', segment: null, unknownTotal: true });
  return cells;
}

export function sceneHeadline(d: InspectSummary | null | undefined): string {
  const sc = d?.scenes;
  if (!sc) return 'Scenes are not reported for this job';
  const total = sc.total, cur = sc.current, done = sc.completed ?? sc.segments?.filter((s) => s.state === 'saved').length ?? 0;
  const ph = phaseOf(d);
  if ((ph === 'completed' || ph === 'finishing') && !total && !done) return 'Finished';   // a picture job has no scenes: do not say "0 scenes saved"
  if (ph === 'completed' || ph === 'finishing') return total != null ? `All ${total} scenes saved` : `${done} scenes saved`;
  if (cur != null) return total != null ? `Scene ${cur} of ${total}` : `Scene ${cur}, total not known yet`;
  if (done > 0) return total != null ? `${done} of ${total} scenes saved` : `${done} scenes saved, total not known yet`;
  return total != null ? `${total} scenes planned, none started yet` : 'Waiting for the first scene';
}

export const segmentSummary = (s: Segment): string => {
  const bits: string[] = [];
  if (s.frames != null) bits.push(`${s.frames} frames`);
  if (s.duration_s != null) bits.push(`${s.duration_s.toFixed(1)} s`);
  if (s.width != null && s.height != null) bits.push(`${s.width}×${s.height}`);
  if (s.seconds_to_make != null) bits.push(`made in ${fmtElapsed(s.seconds_to_make)}`);
  if (s.bytes != null) bits.push(fmtBytes(s.bytes));
  return bits.join(' · ') || 'no details reported';
};

// ---- nodes ------------------------------------------------------------------------------------------------------

export type NodeFilter = 'all' | 'running' | 'failed' | 'done' | 'cached' | 'pending';
export const NODE_ORDER = ['done', 'running', 'cached', 'failed', 'pending'] as const;

export interface NodeCounts { total: number; done: number; running: number; cached: number; failed: number; pending: number }
export function nodeCounts(d: InspectSummary | null | undefined): NodeCounts | null {
  const n = d?.nodes;
  const st = d?.node_states;
  // Count from the per-node states when we have them (the engine's "done" total may already include the reused ones);
  // fall back to the summary counts otherwise.
  if (st && Object.keys(st).length) {
    const c: NodeCounts = { total: 0, done: 0, running: 0, cached: 0, failed: 0, pending: 0 };
    for (const k in st) {
      const s = st[k].state;
      if (s === 'done' || s === 'running' || s === 'cached' || s === 'failed' || s === 'pending') c[s]++; else c.pending++;
      c.total++;
    }
    c.total = Math.max(c.total, n?.total ?? 0);
    return c;
  }
  if (!n) return null;
  return { total: n.total, done: n.done, running: n.running?.length ?? 0, cached: n.cached, failed: n.failed, pending: n.pending };
}

/** Segments of the stacked bar, as shares of the total (never more than 100% in sum). */
export function nodeBar(c: NodeCounts): { key: keyof NodeCounts; count: number; pct: number }[] {
  const sum = c.done + c.running + c.cached + c.failed + c.pending;
  const den = Math.max(c.total, sum, 1);
  return NODE_ORDER.map((key) => ({ key, count: c[key], pct: (c[key] / den) * 100 })).filter((s) => s.count > 0);
}

export interface NodeRow { id: string; state: string; title: string; class_type: string; runs: number | null; last_ms: number | null; total_ms: number | null; rank: number; seen: number }

const idCompare = (a: string, b: string) => { const na = Number(a), nb = Number(b); return Number.isFinite(na) && Number.isFinite(nb) && na !== nb ? na - nb : a < b ? -1 : a > b ? 1 : 0; };

/** Running first, then failed, then recently finished (by the newest event that mentions the node), then the rest. */
export function nodeRows(d: InspectSummary | null | undefined, events: InspectEvent[], filter: NodeFilter = 'all', query = ''): NodeRow[] {
  const states = d?.node_states;
  if (!states) return [];
  const seen = new Map<string, number>();
  for (const e of events) if (e.node_id && (e.type === 'node_start' || e.type === 'node_done' || e.type === 'node_cached' || e.type === 'progress')) seen.set(e.node_id, e.seq);
  const q = query.trim().toLowerCase();
  const rows: NodeRow[] = [];
  for (const [id, s] of Object.entries(states)) {
    if (filter !== 'all' && s.state !== filter) continue;
    const title = s.title || s.class_type || `Node ${id}`;
    if (q && !(`${id} ${title} ${s.class_type ?? ''}`.toLowerCase().includes(q))) continue;
    const rank = s.state === 'running' ? 0 : s.state === 'failed' ? 1 : s.state === 'done' || s.state === 'cached' ? 2 : 3;
    rows.push({ id, state: s.state, title, class_type: s.class_type ?? '', runs: s.runs ?? null, last_ms: s.last_ms ?? null, total_ms: s.total_ms ?? null, rank, seen: seen.get(id) ?? 0 });
  }
  rows.sort((a, b) => a.rank - b.rank || (a.rank === 2 ? b.seen - a.seen : 0) || idCompare(a.id, b.id));
  return rows;
}

// ---- mapping live states onto the submitted graph (loop expansion) ---------------------------------------------

/** ComfyUI expands loops into extra node ids like "73.0.2" or "73:5"; they belong to the graph node "73". */
export const baseId = (id: string): string => id.split(/[.:]/, 1)[0];

export interface GraphNodeState { state: string; runs: number | null; total_ms: number | null; last_ms: number | null; expanded: number; known: boolean }

const UNKNOWN: GraphNodeState = { state: 'unknown', runs: null, total_ms: null, last_ms: null, expanded: 0, known: false };

function combine(direct: NodeStateInfo | undefined, kids: NodeStateInfo[]): GraphNodeState {
  if (!direct && !kids.length) return UNKNOWN;
  let state = direct?.state ?? 'pending';
  if (kids.length) {
    const ks = kids.map((k) => k.state);
    if (ks.includes('running')) state = 'running';
    else if (ks.includes('failed')) state = 'failed';
    else if (!direct) state = ks.every((s) => s === 'cached') ? 'cached' : ks.every((s) => s === 'done' || s === 'cached') ? 'done' : ks.some((s) => s === 'done') ? 'running' : 'pending';
  }
  const all = direct ? [direct, ...kids] : kids;
  const sum = (f: (n: NodeStateInfo) => number | null | undefined) => { let t = 0, any = false; for (const n of all) { const v = f(n); if (v != null) { t += v; any = true; } } return any ? t : null; };
  return { state, runs: sum((n) => n.runs), total_ms: sum((n) => n.total_ms), last_ms: all[all.length - 1].last_ms ?? null, expanded: kids.length, known: true };
}

export function stateOfGraphNode(id: string, states: Record<string, NodeStateInfo> | null | undefined): GraphNodeState {
  if (!states) return UNKNOWN;
  const kids: NodeStateInfo[] = [];
  const p1 = `${id}.`, p2 = `${id}:`;
  for (const k in states) if (k.startsWith(p1) || k.startsWith(p2)) kids.push(states[k]);
  return combine(states[id], kids);
}

/** stateOfGraphNode for every graph node at once (one pass over the states instead of one per node). */
export function rollupStates(states: Record<string, NodeStateInfo> | null | undefined, ids: string[]): Map<string, GraphNodeState> {
  const out = new Map<string, GraphNodeState>();
  if (!states) { for (const id of ids) out.set(id, UNKNOWN); return out; }
  const kids = new Map<string, NodeStateInfo[]>();
  for (const k in states) { const b = baseId(k); if (b !== k) { const l = kids.get(b); if (l) l.push(states[k]); else kids.set(b, [states[k]]); } }
  for (const id of ids) out.set(id, combine(states[id], kids.get(id) ?? []));
  return out;
}

/** The graph node that is executing now: the sampler if one is sampling, else the newest running node that exists in the graph. */
export function activeNodeId(d: InspectSummary | null | undefined, graph?: GraphDoc | null): string | null {
  if (!d) return null;
  const cands: string[] = [];
  if (d.sampling?.node_id) cands.push(d.sampling.node_id);
  const run = d.nodes?.running ?? [];
  for (let i = run.length - 1; i >= 0; i--) cands.push(run[i]);
  for (const c of cands) {
    if (!graph) return c;
    if (graph.nodes[c]) return c;
    const b = baseId(c);
    if (graph.nodes[b]) return b;
  }
  return null;
}

// ---- event lines ------------------------------------------------------------------------------------------------

export type EventTone = 'plain' | 'ok' | 'bad' | 'info' | 'loop';
export interface EventLine { label: string; text: string; tone: EventTone }

export function describeEvent(e: InspectEvent): EventLine {
  const title = e.title || (e.node_id ? `node ${e.node_id}` : 'a step');
  switch (e.type) {
    case 'stage': return { label: 'Stage', text: e.message || 'Stage changed', tone: 'info' };
    case 'node_start': return { label: 'Node', text: `Started ${title}`, tone: 'plain' };
    case 'node_done': return { label: 'Node', text: `Finished ${title}`, tone: 'plain' };
    case 'node_cached': return { label: 'Node', text: `Reused ${title} from cache`, tone: 'plain' };
    case 'progress': return { label: 'Step', text: `${title}: step ${e.value ?? '?'}${e.max != null ? ` of ${e.max}` : ''}${e.scene != null ? ` (scene ${e.scene})` : ''}`, tone: 'plain' };
    case 'scene_saved': return { label: 'Scene', text: e.scene != null ? `Scene ${e.scene} saved and verified` : 'A scene was saved and verified', tone: 'ok' };
    case 'loop_end': return { label: 'Loop End', text: e.scene != null ? `Scene ${e.scene} saved, starting next scene` : 'A scene was saved, starting next scene', tone: 'loop' };
    case 'error': return { label: 'Error', text: e.message || `Error in ${title}`, tone: 'bad' };
    case 'output': return { label: 'Output', text: e.message || (e.state === 'writing' ? 'Writing the output file' : e.state === 'ready' ? 'Output file written and checked' : 'Output file update'), tone: 'ok' };
    case 'log': return { label: 'Log', text: e.message || '', tone: 'plain' };
    default: return { label: e.type, text: e.message || e.title || '', tone: 'plain' };
  }
}

export type LogFilter = 'milestones' | 'all' | 'errors';
const MILESTONES = new Set(['stage', 'scene_saved', 'loop_end', 'error', 'output']);
export function filterEvents(events: InspectEvent[], f: LogFilter): InspectEvent[] {
  if (f === 'all') return events;
  if (f === 'errors') return events.filter((e) => e.type === 'error');
  return events.filter((e) => MILESTONES.has(e.type));
}

export function eventsToText(events: InspectEvent[], startedAt: number | null | undefined): string {
  return events.map((e) => { const l = describeEvent(e); return `${String(e.seq).padStart(5)}  ${relTime(e.t, startedAt).padEnd(10)}  ${l.label.padEnd(8)}  ${l.text}`; }).join('\n');
}

// ---- graph inputs -----------------------------------------------------------------------------------------------

export const isLink = (v: unknown): v is [string, number] => Array.isArray(v) && v.length === 2 && typeof v[0] === 'string' && typeof v[1] === 'number';

export function shortValue(v: unknown, max = 60): string {
  let s: string;
  if (typeof v === 'string') s = v;
  else if (v === null) s = 'null';
  else if (typeof v === 'object') { try { s = JSON.stringify(v); } catch { s = String(v); } }
  else s = String(v);
  s = s.replace(/\s+/g, ' ').trim();
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}
