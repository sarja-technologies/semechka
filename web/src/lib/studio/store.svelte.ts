// The state of the Studio page. The project (the editing decisions) lives on the server and is saved a moment after every change; this class holds the
// working copy, asks the server for the plan (the real numbers) and runs prepare / generate. Nothing is invented here: progress, capabilities and
// sizes all come from the server; when the server does not say, the page says it was not measured.
import { ApiError } from '$lib/api';
import { app } from '$lib/session.svelte';
import { errText } from '$lib/chat/thread.svelte';
import { fetchAsset, type Asset } from '$lib/media/assets';
import { Upload, isMedia, precheck } from '$lib/media/upload.svelte';
import * as api from './api';
import { canCheck, choicesFor, entryFor, modeById, needsSubject, needsVideo, refPolicy, runnable, maxSourceSeconds, type Choice } from './caps';
import { autoFill, fitActive, move, refStates, usedCount } from './refs';
import { defaultSpec, normalizeSpec, prepKey } from './spec';
import type { Caps, JobRow, Plan, Project, ProjectSummary, RefItem, Spec } from './types';

export type Step = 'video' | 'prepare' | 'references' | 'operation' | 'model' | 'generate';
export const STEPS: { id: Step; label: string }[] = [
  { id: 'video', label: 'Video' }, { id: 'prepare', label: 'Prepare' }, { id: 'references', label: 'References' },
  { id: 'operation', label: 'Operation' }, { id: 'model', label: 'Model & output' }, { id: 'generate', label: 'Generate' }
];

export type Blocker = { text: string; step: Step };
type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error' | 'conflict';

const LAST = 'sem.studio.last';
const remember = (id: string | null) => { try { if (id) localStorage.setItem(LAST, id); else localStorage.removeItem(LAST); } catch { /* storage unavailable */ } };
export const lastProject = () => { try { return localStorage.getItem(LAST); } catch { return null; } };

export class Studio {
  caps = $state<Caps | null>(null);
  capsError = $state('');
  loading = $state(true);
  error = $state('');
  step = $state<Step>('video');
  /** which Prepare tool is open: it decides what the stage shows (the timeline, the crop frame, or the finished framing) */
  tool = $state<'trim' | 'crop' | 'aspect' | 'resolution' | null>(null);
  /** how the subject is being marked on the first frame: words only, a box, or points */
  pick = $state<'none' | 'box' | 'points' | 'exclude'>('none');
  /** the stage's playhead, so small previews can show the same moment */
  cur = $state(0);

  project = $state<Project | null>(null);
  spec = $state<Spec>(defaultSpec());
  source = $state<Asset | null>(null);
  /** every picture the project mentions, by id, for thumbnails and the viewer */
  assets = $state<Record<string, Asset>>({});
  projects = $state<ProjectSummary[]>([]);
  jobs = $state<JobRow[]>([]);
  jobId = $state<string | null>(null);
  /** the latest "check selection" run, shown in the Operation step */
  checkJob = $state<string | null>(null);
  checking = $state(false);
  checkError = $state('');
  /** the schema the engine published for the chosen model, from /api/media/options */
  schemas = $state<Record<string, any>>({});

  plan = $state<Plan | null>(null);
  planBusy = $state(false);
  planError = $state('');

  saveState = $state<SaveState>('idle');
  saveError = $state('');

  prep = $state<{ state: 'idle' | 'running' | 'done' | 'error'; progress: number; reused: boolean; asset: Asset | null; error: string; key: string }>({ state: 'idle', progress: 0, reused: false, asset: null, error: '', key: '' });
  gen = $state<{ busy: boolean; error: string }>({ busy: false, error: '' });
  /** pictures and videos being uploaded right now */
  uploads = $state<Upload[]>([]);
  notice = $state('');

  private batchSeq = 0;
  private batchOf = new WeakMap<Upload, number>();
  private added = new WeakSet<Upload>();
  private saveTimer: ReturnType<typeof setTimeout> | undefined;
  private planTimer: ReturnType<typeof setTimeout> | undefined;
  private planSeq = 0;
  private lastPlanKey = '';
  private prepHandle: api.PrepHandle | null = null;

  // ---- what the capabilities say about the current choices ----------------------------------------------------------------
  get mode() { return modeById(this.caps, this.spec.mode); }
  get entry() { return entryFor(this.mode, this.spec.model); }
  get policy() { return refPolicy(this.mode, this.entry); }
  get choices(): Choice[] { return choicesFor(this.caps, this.mode); }
  get states() { return refStates(this.spec.references, this.policy.active_max); }
  get used() { return usedCount(this.spec.references, this.policy.active_max); }
  get usesVideo() { return !this.mode || needsVideo(this.mode); }
  get maxSeconds() { return maxSourceSeconds(this.caps, this.entry); }
  get checkable() { return canCheck(this.mode, this.entry, this.caps); }
  get sw() { return this.source?.width || this.plan?.input?.width || 0; }
  get sh() { return this.source?.height || this.plan?.input?.height || 0; }
  get duration() { return this.source?.duration || this.plan?.input?.duration || 0; }
  get trimStart() { return this.spec.trim?.start ?? 0; }
  get trimEnd() { return this.spec.trim?.end ?? this.duration; }
  get keptSeconds() { return Math.max(0, this.trimEnd - this.trimStart); }
  get prepStale() { return this.prep.state === 'done' && this.prep.key !== prepKey(this.spec); }
  get schema(): Record<string, any> | null { return (this.spec.model && this.schemas[this.spec.model]?.profile?.inputs_schema) || null; }
  get profileInfo(): any { return (this.spec.model && this.schemas[this.spec.model]?.profile) || null; }
  refAsset = (r: RefItem): Asset | null => this.assets[r.asset] ?? null;

  /** Everything that stops Generate, in plain words, with the step where it is fixed. */
  get blockers(): Blocker[] {
    const b: Blocker[] = [], s = this.spec, m = this.mode;
    if (!this.project) return [{ text: 'Add a video first.', step: 'video' }];
    if (!m) b.push({ text: 'Choose what you want to do with the video.', step: 'operation' });
    else if (!runnable(m.status)) b.push({ text: `${m.label} is not available${m.reason ? `: ${m.reason}` : '.'}`, step: 'operation' });
    else if (m.status === 'experimental' && !s.accept_experimental) b.push({ text: `${m.label} is experimental. Tick the box that says you want to try it.`, step: 'operation' });
    if (m && !s.model) b.push({ text: 'Choose a model.', step: 'model' });
    else if (m && s.model && !this.entry) b.push({ text: 'The chosen model does not do this operation. Choose another model.', step: 'model' });
    else if (m && this.entry && !runnable(this.entry.status)) b.push({ text: `This model cannot run it yet${this.entry.reason ? `: ${this.entry.reason}` : '.'}`, step: 'model' });
    if (m && this.usesVideo && this.maxSeconds && this.keptSeconds > this.maxSeconds + 0.01 && !(this.plan?.errors ?? []).some((x) => /takes up to/.test(x))) b.push({ text: `The video part is ${this.keptSeconds.toFixed(1)} s and this model takes up to ${this.maxSeconds} s. Trim it shorter.`, step: 'prepare' });
    if (m && this.used < this.policy.min) b.push({ text: this.policy.min === 1 ? 'Add a reference picture and mark it Active.' : `Use at least ${this.policy.min} reference pictures (${this.used} in use).`, step: 'references' });
    if (m && needsSubject(m) && !s.subject.text.trim()) b.push({ text: 'Say what in the video should be replaced, in a few words.', step: 'operation' });
    if (this.prep.state === 'running') b.push({ text: 'The video is still being prepared.', step: 'prepare' });
    for (const e of this.plan?.errors ?? []) b.push({ text: e, step: 'prepare' });
    return b;
  }

  // ---- loading -----------------------------------------------------------------------------------------------------------
  async init(wanted: string | null, fresh: boolean) {
    this.loading = true; this.error = '';
    try {
      this.caps = await api.loadCaps();
    } catch (e) { this.capsError = errText(e); }
    try { this.projects = await api.listProjects(); } catch { this.projects = []; }
    try { this.jobs = await api.listJobs(); } catch { this.jobs = []; }
    const id = wanted ?? (fresh ? null : lastProject() ?? this.projects[0]?.id ?? null);
    if (id) await this.open(id, wanted === null);
    this.loading = false;
  }

  async open(id: string, quiet = false) {
    try {
      const p = await api.getProject(id);
      this.adopt(p);
    } catch (e) {
      if (quiet && e instanceof ApiError && e.status === 404) { remember(null); this.project = null; return; }
      this.error = e instanceof ApiError && e.status === 404 ? 'This project does not exist or is not yours.' : errText(e);
    }
  }

  /** Make a server project the working copy. */
  private adopt(p: Project) {
    clearTimeout(this.saveTimer); clearTimeout(this.planTimer);
    this.project = p; this.spec = normalizeSpec(p.spec);
    this.spec.source_asset ||= p.source_asset ?? null;
    this.plan = null; this.planError = ''; this.lastPlanKey = '';
    this.saveState = 'saved'; this.saveError = '';
    this.prep = { state: 'idle', progress: 0, reused: false, asset: null, error: '', key: '' };
    const ids = (p.jobs ?? []).map((j: any) => j.job ?? j.id).filter(Boolean) as string[];
    const mine = this.jobs.filter((j) => (j.project_id === p.id || ids.includes(j.job)) && j.operation !== 'check_mask');
    this.jobId = mine.length ? mine[0].job : ids[0] ?? null;
    remember(p.id);
    this.step = this.jobId ? 'generate' : this.spec.mode ? 'operation' : 'prepare';
    this.loadAssets();
    const prepared = p.prepared_asset;
    if (p.source && typeof p.source === 'object') this.assets[(p.source as Asset).id] = p.source as Asset;
    if (prepared && p.prepared_current !== false && p.source?.id !== (prepared as Asset).id) {
      if (typeof prepared === 'object') this.setPrepared(prepared as Asset, true);
      else fetchAsset(prepared).then((a) => this.setPrepared(a, true)).catch(() => {});
    }
    this.ensureSchema();
    this.schedulePlan(0);
  }

  private setPrepared(a: Asset, assumeCurrent: boolean) {
    this.prep = { state: 'done', progress: 1, reused: false, asset: a, error: '', key: assumeCurrent ? prepKey(this.spec) : '' };
  }

  /** Fetch the source video and every picture the spec mentions. */
  async loadAssets() {
    const need = [this.spec.source_asset, ...this.spec.references.map((r) => r.asset)].filter((x): x is string => !!x && !this.assets[x]);
    await Promise.all(need.map((id) => fetchAsset(id).then((a) => { this.assets[id] = a; }).catch(() => {})));
    const sid = this.spec.source_asset;
    this.source = sid ? this.assets[sid] ?? null : null;
  }

  async refreshProjects() { try { this.projects = await api.listProjects(); } catch { /* keep the old list */ } }
  async refreshJobs() { try { this.jobs = await api.listJobs(); } catch { /* keep the old list */ } }

  async ensureSchema() {
    const id = this.spec.model;
    if (!id || this.schemas[id]) return;
    try {
      const o = await api.mediaOptions();
      for (const m of o.models ?? []) this.schemas[m.id] = m;
      if (!this.schemas[id]) this.schemas[id] = {};
    } catch { this.schemas[id] = {}; }
  }

  // ---- editing + autosave -----------------------------------------------------------------------------------------------
  /** Change the spec; saved 600 ms after the last change. */
  edit(fn: (s: Spec) => void) {
    fn(this.spec);
    this.dirty();
  }
  private dirty() {
    if (!this.project) return;
    this.saveState = 'dirty';
    clearTimeout(this.saveTimer);
    this.saveTimer = setTimeout(() => this.saveNow(), 600);
    this.schedulePlan(280);
  }
  async saveNow(): Promise<boolean> {
    clearTimeout(this.saveTimer);
    const p = this.project;
    if (!p || this.saveState === 'conflict') return this.saveState !== 'conflict';
    this.saveState = 'saving';
    const sent = $state.snapshot(this.spec) as Spec;
    try {
      const r = await api.saveProject(p.id, sent, p.updated_at);
      if (this.project?.id === p.id) { this.project = { ...this.project, updated_at: r.updated_at ?? this.project.updated_at, title: r.title ?? this.project.title }; }
      if (this.saveState === 'saving') this.saveState = 'saved';
      this.saveError = '';
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // The server also changes a project by itself (a started job is added to it). When the stored decisions are the same as ours there is
        // nothing to choose between: take the new time stamp and carry on.
        const theirs: Project | undefined = e.detail?.project;
        if (theirs && JSON.stringify(normalizeSpec(theirs.spec)) === JSON.stringify(normalizeSpec(sent))) {
          this.project = { ...p, updated_at: theirs.updated_at, title: theirs.title ?? p.title };
          if (this.saveState === 'saving') this.saveState = 'saved';
          return true;
        }
        this.saveState = 'conflict'; this.saveError = 'This project was changed somewhere else (another window or device).';
      }
      else { this.saveState = 'error'; this.saveError = errText(e); }
      return false;
    }
  }
  /** After a conflict: take the other window's version, or keep this one and save over it. */
  async resolveConflict(keepMine: boolean) {
    const p = this.project; if (!p) return;
    const theirs = await api.getProject(p.id);
    if (keepMine) { this.project = { ...p, updated_at: theirs.updated_at }; this.saveState = 'dirty'; await this.saveNow(); }
    else this.adopt(theirs);
  }
  /** The page is closing: send what has not been saved yet. */
  flush() {
    const p = this.project;
    if (!p || (this.saveState !== 'dirty')) return;
    clearTimeout(this.saveTimer);
    api.saveOnExit(p.id, $state.snapshot(this.spec) as Spec, p.updated_at);
  }
  retrySave() { this.saveState = 'dirty'; return this.saveNow(); }

  // ---- the plan (real numbers from the server) --------------------------------------------------------------------------
  private planKey() {
    const s: any = $state.snapshot(this.spec);
    return JSON.stringify({ ...s, references: s.references.map((r: RefItem) => [r.asset, r.active, r.subject]), subject: { ...s.subject, text: s.subject.text.trim() } });
  }
  schedulePlan(ms: number) {
    clearTimeout(this.planTimer);
    if (!this.project) return;
    this.planTimer = setTimeout(() => this.refreshPlan(), ms);
  }
  async refreshPlan(force = false) {
    const p = this.project; if (!p) return;
    const key = this.planKey();
    if (!force && key === this.lastPlanKey) return;
    this.lastPlanKey = key;
    const mine = ++this.planSeq;
    this.planBusy = true;
    try {
      const plan = await api.planProject(p.id, $state.snapshot(this.spec) as Spec);
      if (mine === this.planSeq && this.project?.id === p.id) { this.plan = plan; this.planError = ''; }
    } catch (e) {
      if (mine === this.planSeq) { this.planError = errText(e); this.lastPlanKey = ''; }
    } finally { if (mine === this.planSeq) this.planBusy = false; }
  }

  // ---- starting a project / adding media ------------------------------------------------------------------------------
  async startProject(asset: Asset): Promise<boolean> {
    try {
      const p = await api.createProject(asset.id, asset.name?.replace(/\.[^.]+$/, ''));
      this.assets[asset.id] = asset;
      this.adopt({ ...p, spec: { ...(p.spec ?? {}), source_asset: asset.id } });
      await this.loadAssets();
      this.refreshProjects();
      this.step = 'prepare';
      return true;
    } catch (e) { this.notice = errText(e); return false; }
  }

  private maxMb() { return app.me?.ui?.composer?.media_upload_max_mb ?? 512; }

  /** Route dropped, pasted or chosen files: videos start a project, pictures become references. */
  async addFiles(files: File[]) {
    this.notice = '';
    const batch = ++this.batchSeq;
    for (const f of files) {
      if (!isMedia(f)) { this.notice = `${f.name} is not a picture or a video.`; continue; }
      const why = precheck(f, this.maxMb());
      if (why) { this.notice = why; continue; }
      const isVideo = /^video\//.test(f.type) || /\.(mp4|m4v|mov|webm|mkv|avi)$/i.test(f.name);
      if (!isVideo && !this.project) { this.notice = 'Add the video first. Pictures go in the References step.'; continue; }
      const u = new Upload(f, false);
      this.batchOf.set(u, batch);
      this.uploads = [...this.uploads, u];
      u.start().then((a) => this.finish(u, a));
    }
  }
  retryUpload(u: Upload) { u.retry().then((a) => this.finish(u, a)); }
  private async finish(u: Upload, a: Asset | null) {
    if (a) {
      if (a.kind === 'video') {
        const had = !!this.project;
        if (await this.startProject(a) && had) this.notice = 'Started a new project with this video. The earlier one is still in Projects.';
        this.uploads = this.uploads.filter((x) => x !== u);
      } else this.landed(u);
    } else if (u.status === 'cancelled') { this.uploads = this.uploads.filter((x) => x !== u); this.landed(u); }
    else this.landed(u);
  }
  /** Pictures chosen together are added together, in the order they were chosen (uploads finish in any order), so the first ones start as used. */
  private landed(u: Upload) {
    const batch = this.batchOf.get(u);
    const mates = this.uploads.filter((x) => this.batchOf.get(x) === batch);
    if (mates.some((x) => x.active)) return;
    const ready = mates.filter((x) => x.status === 'ready' && x.asset && !this.added.has(x));
    for (const x of ready) this.added.add(x);
    if (ready.length) this.addRefs(ready.map((x) => x.asset!));
    this.uploads = this.uploads.filter((x) => !ready.includes(x));
  }
  dismissUpload(u: Upload) { u.cancel(); this.uploads = this.uploads.filter((x) => x !== u); }

  /** Add pictures chosen from the Library or uploaded. The first ones up to the operation's limit start as Active. */
  addRefs(list: Asset[]) {
    const pics = list.filter((a) => a.kind === 'image');
    if (pics.length < list.length) this.notice = 'Only pictures can be references.';
    if (!pics.length) return;
    this.edit((s) => {
      let used = this.used;
      for (const a of pics) {
        this.assets[a.id] = a;
        s.references.push({ asset: a.id, active: used < this.policy.active_max, subject: null, label: '' });
        if (used < this.policy.active_max) used++;
      }
    });
  }
  removeRef(i: number) { this.edit((s) => { s.references.splice(i, 1); }); }
  moveRef(i: number, to: number) { this.edit((s) => { s.references = move(s.references, i, to); }); }
  /** Switch a picture on or off. At a limit of one, switching another on takes the place of the one in use (said out loud); otherwise the person must free a place. */
  setActive(i: number, on: boolean): string {
    const max = this.policy.active_max;
    if (on && this.used >= max) {
      if (max === 1) { this.edit((s) => { s.references.forEach((r, k) => { r.active = k === i; }); }); return 'This operation takes 1 picture, so the other one is now off.'; }
      return `This operation takes ${max} pictures. Turn another one off first.`;
    }
    this.edit((s) => { s.references[i].active = on; });
    return '';
  }
  autoFillRefs() { this.edit((s) => { s.references = autoFill(s.references, this.policy.active_max); }); }

  // ---- choosing the operation and model -----------------------------------------------------------------------------------
  chooseMode(id: string) {
    const m = modeById(this.caps, id);
    if (!m || !runnable(m.status)) return;
    this.edit((s) => {
      if (s.mode !== id) s.accept_experimental = false;
      s.mode = id;
      const ch = choicesFor(this.caps, m);
      const keep = ch.find((c) => c.model.model_id === s.model && c.supported);
      s.model = keep ? keep.model.model_id : (ch.find((c) => c.supported && c.status === 'ready') ?? ch.find((c) => c.supported))?.model.model_id ?? null;
      const kinds = m.subject_kinds ?? [];
      if (kinds.length && !kinds.includes(s.subject.kind)) s.subject.kind = kinds[0] as any;
      s.references = fitActive(s.references, refPolicy(m, entryFor(m, s.model)).active_max);
    });
    this.ensureSchema();
  }
  chooseModel(id: string) {
    this.edit((s) => { s.model = id; s.references = fitActive(s.references, refPolicy(this.mode, entryFor(this.mode, id)).active_max); });
    this.ensureSchema();
  }

  // ---- prepare ------------------------------------------------------------------------------------------------------------
  async runPrepare() {
    const p = this.project; if (!p || this.prep.state === 'running') return;
    if (!(await this.saveNow())) return;
    const key = prepKey(this.spec);
    this.prep = { state: 'running', progress: 0, reused: false, asset: null, error: '', key };
    this.prepHandle = api.prepare(p.id, $state.snapshot(this.spec) as Spec, (pr) => { this.prep.progress = pr; });
    try {
      const r = await this.prepHandle.done;
      this.assets[r.asset.id] = r.asset;
      this.prep = { state: 'done', progress: 1, reused: r.reused, asset: r.asset, error: '', key };
    } catch (e) {
      this.prep = { state: e instanceof ApiError && e.code === 'cancelled' ? 'idle' : 'error', progress: 0, reused: false, asset: null, error: e instanceof ApiError && e.code === 'cancelled' ? '' : errText(e), key: '' };
    } finally { this.prepHandle = null; this.refreshPlan(true); }
  }
  cancelPrepare() { this.prepHandle?.cancel(); }

  // ---- generate -----------------------------------------------------------------------------------------------------------
  async runGenerate(extra: Record<string, unknown> = {}): Promise<string | null> {
    const p = this.project; if (!p || this.gen.busy) return null;
    this.gen = { busy: true, error: '' };
    try {
      if (!(await this.saveNow())) { this.gen = { busy: false, error: this.saveError || 'The project could not be saved first.' }; return null; }
      const r = await api.generate(p.id, $state.snapshot(this.spec) as Spec, extra);
      if (extra.operation === 'check_mask') this.checkJob = r.job; else this.jobId = r.job;
      await this.refreshJobs();
      if (!this.jobs.some((j) => j.job === r.job)) this.jobs = [{ job: r.job, project_id: p.id, status: 'queued', mode: this.spec.mode ?? undefined, created_at: Date.now() / 1000 }, ...this.jobs];
      this.gen = { busy: false, error: '' };
      api.getProject(p.id).then((fresh) => { if (this.project?.id === p.id) this.project = { ...this.project, updated_at: fresh.updated_at }; }).catch(() => {});   // generating saves the project on the server
      return r.job;
    } catch (e) {
      this.gen = { busy: false, error: errText(e) };
      return null;
    }
  }

  /** Ask the engine what a selection covers, as an ordinary job, before the full run. */
  async runCheck() {
    this.checking = true; this.checkError = '';
    const j = await this.runGenerate({ operation: 'check_mask' });
    if (!j) this.checkError = this.gen.error;
    this.checking = false;
  }

  /** Open an earlier run: its project comes back with it. */
  async openJob(row: JobRow) {
    if (row.project_id && row.project_id !== this.project?.id) await this.open(row.project_id);
    this.jobId = row.job; this.step = 'generate';
  }

  /** Put the references, subject, mode and model of a finished run back into the project (it may have been changed since). */
  async restoreFromJob(job: string) {
    const j = await api.mediaJob(job);
    const st = j?.params?.studio;
    if (!st) { this.notice = 'This run did not keep its settings.'; return; }
    this.edit((s) => {
      if (Array.isArray(st.references)) s.references = st.references.map((r: any) => ({ asset: String(r.asset), active: r.active !== false, subject: r.subject ?? null, label: r.label ?? '' }));
      if (st.subject) s.subject = { ...s.subject, ...st.subject };
      if (st.mode) s.mode = st.mode;
    });
    await this.loadAssets();
  }

  /** Leave this project and show the empty start screen. */
  startOver() {
    clearTimeout(this.saveTimer); clearTimeout(this.planTimer);
    this.project = null; this.spec = defaultSpec(); this.source = null; this.plan = null; this.jobId = null; this.step = 'video'; this.saveState = 'idle';
    this.prep = { state: 'idle', progress: 0, reused: false, asset: null, error: '', key: '' };
    remember(null);
  }
  async removeProject(id: string) {
    await api.deleteProject(id);
    this.projects = this.projects.filter((p) => p.id !== id);
    if (this.project?.id === id) this.startOver();
  }
}
