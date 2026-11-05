// A scripted OpenJutsu swap run that follows contract 2.2 exactly, built on the real 70-node graph
// (deploy/openjutsu/workflows/simple.api.json, see graph.json). It is a simulator of the ENGINE's trace, used by the unit
// tests and by the mock Studio API of the browser tests: steps are applied by hand (advance / advanceTo) so every
// state of a run (queued, scene 3 sampling step 4, Loop End, output writing, completed ...) can be looked at and asserted.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { GraphDoc, InspectDoc, InspectEvent, NodeStateInfo, Segment } from '../../../src/lib/studio/inspector/types.ts';

export const GRAPH: GraphDoc = JSON.parse(readFileSync(fileURLToPath(new URL('./graph.json', import.meta.url)), 'utf8'));
export const T0 = 1_793_000_000;
export const LOOP_MEANING = "Each 'Loop End' means one clip (scene) was saved as a checkpoint and the next clip is being set up. It is not the end of the job: the final video exists only after 'Final Video' has run and the output file was verified.";

const isLinkTo = (v: unknown): v is [string, number] => Array.isArray(v) && v.length === 2 && typeof v[0] === 'string';
function reach(start: string, next: (id: string) => string[]) {
  const seen = new Set<string>(); const q = [start];
  while (q.length) { const n = q.pop()!; for (const m of next(n)) if (!seen.has(m)) { seen.add(m); q.push(m); } }
  return seen;
}
const succOf = (id: string) => Object.entries(GRAPH.nodes).filter(([, n]) => Object.values(n.inputs).some((v) => isLinkTo(v) && v[0] === id)).map(([k]) => k);
const predOf = (id: string) => Object.values(GRAPH.nodes[id].inputs).filter(isLinkTo).map((v) => v[0]).filter((k) => GRAPH.nodes[k]);
const byClass = (c: string) => Object.entries(GRAPH.nodes).find(([, n]) => n.class_type === c)![0];

export const IDS = {
  loopStart: byClass('MiniMaxH3ChainLoopStart'), loopEnd: byClass('MiniMaxH3ChainLoopEnd'), current: byClass('MiniMaxH3ChainCurrent'),
  sampler: byClass('SamplerCustomAdvanced'), save: byClass('MiniMaxH3ChainSegmentSave'), assemble: byClass('MiniMaxH3ChainAssemble'), plan: byClass('MiniMaxH3ChainPlanModern'),
  video: byClass('VHS_LoadVideo'), picture: byClass('LoadImage')
};
const afterStart = reach(IDS.loopStart, succOf);
const beforeEnd = reach(IDS.loopEnd, predOf);
export const LOOP_IDS = GRAPH.order!.filter((id) => id === IDS.loopStart || id === IDS.loopEnd || (afterStart.has(id) && beforeEnd.has(id)));
const afterEnd = reach(IDS.loopEnd, succOf);
export const POST_IDS = GRAPH.order!.filter((id) => afterEnd.has(id) && !LOOP_IDS.includes(id));
export const PRE_IDS = GRAPH.order!.filter((id) => !LOOP_IDS.includes(id) && !POST_IDS.includes(id));
const CACHED = new Set(Object.entries(GRAPH.nodes).filter(([, n]) => ['CLIPLoader', 'VAELoader', 'CheckpointLoaderSimple'].includes(n.class_type)).map(([k]) => k));

export interface Step { tag: string; apply: () => void }

export class ScriptedRun {
  readonly jobId: string;
  readonly scenes: number;
  clock = T0;
  doc: InspectDoc;
  events: InspectEvent[] = [];
  steps: Step[] = [];
  i = 0;
  private seq = 1;
  private segments: Segment[] = [];
  private states: Record<string, NodeStateInfo> = {};
  private appliedTags: string[] = [];
  /** extra node-state entries, like the ids ComfyUI makes when it expands a loop ("60.0.1") */
  extraStates: Record<string, NodeStateInfo> = {};

  constructor(jobId = 'j_run', scenes = 7) {
    this.jobId = jobId; this.scenes = scenes;
    for (const [id, n] of Object.entries(GRAPH.nodes)) this.states[id] = { state: 'pending', class_type: n.class_type, title: n.title ?? n.class_type, runs: 0, total_ms: 0, last_ms: null };
    this.doc = {
      job_id: jobId, backend: 'comfyui', profile: 'openjutsu-minimax-h3', operation: 'swap', status: 'queued', stage: 'queued', stage_label: 'Waiting in the queue',
      prompt_id: null, started_at: null, updated_at: T0, elapsed_s: null, progress: { fraction: null, indeterminate: true, basis: null }, sampling: null,
      scenes: { total: null, completed: 0, current: null, segments: [] }, loop: { iterations_ended: 0, meaning: LOOP_MEANING },
      nodes: null, node_states: this.states, conditioning: null, memory: { ram_available_mb: 98_000, comfyui_rss_mb: 1200, vram_mb: null, source: '/proc/meminfo + process RSS (unified memory: NVML reports no device memory)' },
      output: { state: 'none', file: null }, error: null, next_seq: 1
    };
    this.refresh();
    this.build();
  }

  // ---- bookkeeping ------------------------------------------------------------------------------------------------
  private emit(type: string, extra: Partial<InspectEvent> = {}) {
    const e: InspectEvent = { seq: this.seq++, t: this.clock, type, ...extra };
    this.events.push(e); this.doc.next_seq = this.seq;
  }
  private tick(dt = 0.4) { this.clock = Math.round((this.clock + dt) * 10) / 10; }
  private stage(stage: string, label: string) { this.doc.stage = stage; this.doc.stage_label = label; this.emit('stage', { message: label }); }
  private refresh() {
    const d = this.doc;
    const all = Object.values(this.states);
    const running = Object.entries(this.states).filter(([, s]) => s.state === 'running').map(([k]) => k);
    // like the engine, "done" includes the reused (cached) nodes
    if (d.status !== 'queued') d.nodes = { total: all.length, done: all.filter((s) => s.state === 'done' || s.state === 'cached').length, running, pending: all.filter((s) => s.state === 'pending').length, cached: all.filter((s) => s.state === 'cached').length, failed: all.filter((s) => s.state === 'failed').length };
    d.node_states = JSON.parse(JSON.stringify({ ...this.states, ...this.extraStates }));
    d.updated_at = this.clock;
    d.elapsed_s = d.started_at == null ? null : Math.round((this.clock - d.started_at) * 10) / 10;
    d.scenes = { ...d.scenes!, segments: this.segments.map((s) => ({ ...s })) };
    d.memory = { ...d.memory!, ram_available_mb: 98_000 - Math.round((this.clock - T0) * 40) };
    if (d.scenes!.total != null) {
      const saved = d.scenes!.completed ?? 0;
      d.progress = { fraction: saved / d.scenes!.total, indeterminate: false, basis: 'scenes_saved/total_scenes' };
    }
  }
  private startNode(id: string) {
    const s = this.states[id]; s.state = 'running'; this.emit('node_start', { node_id: id, title: s.title, scene: this.doc.scenes?.current ?? null });
  }
  private doneNode(id: string, ms = 600) {
    const s = this.states[id]; s.state = 'done'; s.runs = (s.runs ?? 0) + 1; s.last_ms = ms; s.total_ms = (s.total_ms ?? 0) + ms;
    this.emit('node_done', { node_id: id, title: s.title, scene: this.doc.scenes?.current ?? null });
  }
  private push(tag: string, apply: () => void) { this.steps.push({ tag, apply: () => { apply(); this.appliedTags.push(tag); this.refresh(); } }); }

  // ---- the script -------------------------------------------------------------------------------------------------
  private build() {
    const d = this.doc;
    this.push('started', () => {
      this.tick(2); d.status = 'running'; d.started_at = this.clock; d.prompt_id = '1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f'; this.stage('starting_backend', 'Starting ComfyUI');
      d.conditioning = {
        video: { node_id: IDS.video, file: 'src_6s_736p.mp4' },
        references: [{ position: 1, node_id: IDS.picture, file: 'ref_woman_yellow_coat.png', used: true, name: 'yellow_coat.png' }, { position: 2, file: 'ref_man_grey_beard.png', used: false, name: 'grey_beard.png', reason: 'swap conditions on 1 picture' }],
        agree: true
      };
    });
    this.push('loading', () => { this.tick(3); this.stage('loading_models', 'Loading models'); });
    PRE_IDS.forEach((id, n) => {
      this.push(`pre:${id}:start`, () => {
        this.tick(0.3);
        if (n === Math.floor(PRE_IDS.length * 0.45)) this.stage('tracking_subject', 'Tracking the subject');
        if (CACHED.has(id)) { const s = this.states[id]; s.state = 'cached'; s.runs = 0; this.emit('node_cached', { node_id: id, title: s.title }); }
        else this.startNode(id);
      });
      if (!CACHED.has(id)) this.push(`pre:${id}:done`, () => { this.tick(0.9); this.doneNode(id, 900); if (id === IDS.plan) { d.scenes!.total = this.scenes; } });
    });
    for (let sc = 1; sc <= this.scenes; sc++) {
      LOOP_IDS.forEach((id) => {
        this.push(`scene:${sc}:${id}:start`, () => {
          this.tick(0.3);
          if (id === IDS.loopStart) { d.scenes!.current = sc; this.stage('generating_scene', `Generating scene ${sc} of ${this.scenes}`); }
          this.startNode(id);
        });
        if (id === IDS.sampler) {
          for (let k = 1; k <= 6; k++) this.push(`scene:${sc}:step:${k}`, () => {
            this.tick(4.2); d.sampling = { node_id: id, title: this.states[id].title, step: k, max: 6, scene: sc };
            this.emit('progress', { node_id: id, title: this.states[id].title, scene: sc, value: k, max: 6 });
          });
        }
        this.push(`scene:${sc}:${id}:done`, () => {
          this.tick(0.8); if (id === IDS.sampler) d.sampling = null;
          this.doneNode(id, id === IDS.sampler ? 25_000 : 700);
          if (id === IDS.save) {
            this.segments.push({ index: sc, state: 'saved', frames: 141, duration_s: 5.875, width: 736, height: 1312, bytes: 1_234_567 + sc, seconds_to_make: 38.1 + sc });
            d.scenes!.completed = sc;
            this.emit('scene_saved', { node_id: id, title: this.states[id].title, scene: sc });
          }
          if (id === IDS.loopEnd) {
            d.loop!.iterations_ended = sc; if (sc === this.scenes) d.scenes!.current = null;
            this.emit('loop_end', { node_id: id, title: this.states[id].title, scene: sc });
          }
        });
        if (id === IDS.save) this.push(`scene:${sc}:saved`, () => { this.tick(0.1); });
        if (id === IDS.loopEnd) this.push(`scene:${sc}:loop_end`, () => { this.tick(0.1); });
      });
    }
    this.push('assembling', () => { this.tick(0.5); this.stage('assembling', 'Putting the scenes together'); });
    POST_IDS.forEach((id) => {
      this.push(`post:${id}:start`, () => { this.tick(0.3); this.startNode(id); });
      this.push(`post:${id}:done`, () => { this.tick(id === IDS.assemble ? 6 : 0.5); this.doneNode(id, id === IDS.assemble ? 6000 : 400); });
    });
    this.push('output_writing', () => { this.tick(1); this.stage('saving_output', 'Saving the final video'); d.output = { state: 'writing', file: null }; this.emit('output', { message: 'Writing the final video' }); });
    this.push('completed', () => {
      this.tick(1.5); d.output = { state: 'ready', file: { width: 736, height: 1312, duration_s: 41.1, bytes: 8_229_332, has_audio: true } };
      d.status = 'completed'; this.stage('done', 'Finished'); this.emit('output', { message: 'Final video written and verified' });
      d.progress = { fraction: 1, indeterminate: false, basis: 'scenes_saved/total_scenes' };
    });
  }

  // ---- driving it -------------------------------------------------------------------------------------------------
  /** next seq to be assigned */
  seqNow() { return this.seq; }
  get finished() { return this.i >= this.steps.length; }
  get lastTag() { return this.appliedTags[this.appliedTags.length - 1] ?? null; }
  advance(n = 1) { for (let k = 0; k < n && this.i < this.steps.length; k++) this.steps[this.i++].apply(); return this.lastTag; }
  /** Apply steps up to and including the one tagged `tag`. */
  advanceTo(tag: string) {
    const at = this.steps.findIndex((s, ix) => ix >= this.i && s.tag === tag);
    if (at < 0) throw new Error(`no step tagged ${tag} ahead of ${this.lastTag}`);
    while (this.i <= at) this.steps[this.i++].apply();
  }
  /** The engine reports a failure at the current position (the running node fails). */
  fail(message = 'CUDA out of memory while allocating 4.1 GiB', status: 'failed' | 'cancelled' = 'failed') {
    const d = this.doc; this.tick(1);
    const run = Object.entries(this.states).filter(([, s]) => s.state === 'running').map(([k]) => k);
    const id = run[run.length - 1] ?? IDS.sampler;
    if (status === 'failed') {
      const s = this.states[id]; s.state = 'failed';
      d.error = { message, node_id: id, node_title: s.title ?? null, class_type: s.class_type ?? null };
      this.emit('error', { node_id: id, title: s.title, scene: d.scenes?.current ?? null, message });
      this.stage('failed', 'Failed');
    } else { this.stage('failed', 'Cancelled'); }
    d.status = status; d.sampling = null; this.refresh(); this.i = this.steps.length;
  }
  advanceAll() { while (!this.finished) this.advance(); }

  /** GET /v1/jobs/{id}/inspect?after=N */
  inspect(after = 0): InspectDoc {
    const evs = this.events.filter((e) => e.seq > after).slice(0, 500);
    const page = evs.length ? evs[evs.length - 1].seq + 1 : Math.max(after + 1, this.seq);
    return { ...JSON.parse(JSON.stringify(this.doc)), events: evs, next_seq: page, last_seq: this.seq - 1, has_preview: this.segments.length > 0 };
  }
  /** The graph as ComfyUI was given it. */
  graph(): GraphDoc { return { ...GRAPH, prompt_id: this.doc.prompt_id ?? GRAPH.prompt_id }; }
  /** ComfyUI's /history entry reduced to what the contract shows. */
  history() {
    const outputs: Record<string, unknown> = {};
    if (this.doc.output?.state === 'ready') outputs[IDS.assemble] = { videos: [{ filename: 'Swap_00007.mp4', subfolder: 'swap', type: 'output' }] };
    for (const s of this.segments) outputs[IDS.save] = { videos: this.segments.map((x) => ({ filename: `clip_${x.index}.mp4`, subfolder: 'swap', type: 'temp' })) };
    return { status: { status_str: this.doc.status === 'completed' ? 'success' : this.doc.status === 'failed' ? 'error' : 'running', completed: this.doc.status === 'completed', messages: [] }, messages: [['execution_start', { prompt_id: this.doc.prompt_id }]], outputs, node_errors: this.doc.error ? { [this.doc.error.node_id!]: { errors: [{ message: this.doc.error.message }] } } : {} };
  }
}

/** A finished (completed) run: 7 scenes, output ready. */
export function completedRun(jobId = 'j_done') { const r = new ScriptedRun(jobId); r.advanceAll(); return r; }
/** A failed run: scene 3, sampler step 2. */
export function failedRun(jobId = 'j_failed') { const r = new ScriptedRun(jobId); r.advanceTo('scene:3:step:2'); r.fail(); return r; }
