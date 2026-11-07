// The state of the Imajutsu page. The project (the decisions) lives on the server and is saved a moment after every change; this class holds the
// working copy, asks the server for the plan (the real numbers) and runs generate. Nothing is invented here: capabilities, sizes, prompts and
// progress all come from the server; when the server does not say, the page says so.
import { ApiError } from '$lib/api';
import { errText } from '$lib/chat/thread.svelte';
import { fetchAsset, type Asset } from '$lib/media/assets';
import { Upload, isMedia, precheck } from '$lib/media/upload.svelte';
import { app } from '$lib/session.svelte';
import { move } from '$lib/studio/refs';
import * as api from './api.ts';
import { cleanRecipe, isNeutral } from './adjust.ts';
import { entryOfKey, modeState, modelEntry, needsOf, rolesFor, runnable } from './caps.ts';
import { addOp, asBox, emptyMask, fromBox, hasSelection, maskKey, readStrokes, renderPng } from './mask.ts';
import { defaultSpec, normalizeSpec } from './spec.ts';
import type { Caps, JobRow, MaskDoc, Plan, Project, ProjectSummary, Recipe, RefItem, Spec } from './types.ts';

export type Panel = 'prompt' | 'source' | 'selection' | 'references' | 'size' | 'model' | 'adjust' | 'generate';
export type StageView = 'picture' | 'selection' | 'adjust' | 'result';
export type Blocker = { text: string; panel: Panel };
export type RefView = { state: 'used' | 'unused' | 'pending'; order: number; note: string };
type SaveState = 'idle' | 'dirty' | 'saving' | 'saved' | 'error' | 'conflict';

const LAST = 'sem.imajutsu.last';
const remember = (id: string | null) => { try { if (id) localStorage.setItem(LAST, id); else localStorage.removeItem(LAST); } catch { /* storage unavailable */ } };
export const lastProject = () => { try { return localStorage.getItem(LAST); } catch { return null; } };

export class Imajutsu {
  caps = $state<Caps | null>(null);
  capsError = $state('');
  loading = $state(true);
  error = $state('');
  notice = $state('');

  project = $state<Project | null>(null);
  spec = $state<Spec>(defaultSpec());
  /** every picture the project mentions, by id, for thumbnails and the viewer */
  assets = $state<Record<string, Asset>>({});
  projects = $state<ProjectSummary[]>([]);
  jobs = $state<JobRow[]>([]);
  /** the runs shown in the live area: one per picture asked for in the last Generate (oldest first) */
  batch = $state<string[]>([]);
  /** the job documents (GET /api/media/jobs/{id}), filled in by the live cards */
  jobData = $state<Record<string, any>>({});
  /** the result open on the stage */
  picked = $state<string | null>(null);
  stage = $state<StageView>('picture');
  /** the panel the person is working in (the summary row highlights it) */
  panel = $state<Panel>('prompt');
  advanced = $state(false);
  /** the Results stage shows before / after instead of the picture alone */
  compare = $state(true);

  plan = $state<Plan | null>(null);
  planBusy = $state(false);
  planError = $state('');
  saveState = $state<SaveState>('idle');
  saveError = $state('');
  gen = $state<{ busy: boolean; error: string; accounting: any }>({ busy: false, error: '', accounting: null });
  uploads = $state<Upload[]>([]);

  // the selection: the strokes are kept here and travel inside the saved picture
  maskDoc = $state<MaskDoc | null>(null);
  maskBusy = $state(false);
  maskError = $state('');
  /** the key of what the saved selection (picture or box) was made from */
  maskSaved = $state('');

  // adjust
  adjustId = $state<string | null>(null);
  cropOn = $state(false);
  cropAspect = $state<number | null>(null);
  previewBusy = $state(false);
  previewError = $state('');
  adjustBusy = $state<{ busy: boolean; progress: number; error: string; done: Asset | null }>({ busy: false, progress: 0, error: '', done: null });
  /** pictures made on this page without a model (Apply, Scale): they live in the Library; this list is only for the session */
  copies = $state<string[]>([]);
  /** pictures a reopened run used that have since left the Library */
  dropped = $state<string[]>([]);

  private saveTimer: ReturnType<typeof setTimeout> | undefined;
  private planTimer: ReturnType<typeof setTimeout> | undefined;
  private maskTimer: ReturnType<typeof setTimeout> | undefined;
  private planSeq = 0;
  private lastPlanKey = $state('');
  private target = new WeakMap<Upload, 'source' | 'ref'>();
  private batchOf = new WeakMap<Upload, number>();
  private batchSeq = 0;
  private added = new WeakSet<Upload>();
  private handled = new Set<string>();
  private chain: Promise<unknown> = Promise.resolve();

  // ---- what the capabilities say about the current choices ------------------------------------------------------------------
  get key() { return this.project ? this.spec.mode : ''; }
  get mode() { return entryOfKey(this.caps, this.spec.mode); }
  get needs() { return needsOf(this.spec.mode, this.mode); }
  /** the model that runs: the one chosen by hand, else the recommended one */
  get modelId(): string | null { return this.spec.model ?? this.mode?.recommended?.model_id ?? null; }
  get entry() { return modelEntry(this.mode, this.modelId); }
  get source(): Asset | null { return this.spec.source_asset ? this.assets[this.spec.source_asset] ?? null : null; }
  get roles() { return rolesFor(this.caps); }
  asset = (id: string | null | undefined): Asset | null => (id ? this.assets[id] ?? null : null);
  refAsset = (r: RefItem): Asset | null => this.assets[r.asset] ?? null;

  /** how many pictures the model takes in all, and how many of them can be references (the source takes one when the mode edits it) */
  get total(): number { return this.plan?.references?.limit ?? this.entry?.limits?.references ?? 0; }
  get slots(): number {
    const p = this.plan?.references;
    if (p && p.limit != null) return Math.max(0, p.limit - (p.source_counts ? 1 : 0));
    return this.needs.refs === 'none' ? 0 : Math.max(0, (this.entry?.limits?.references ?? 0) - (this.needs.source ? 1 : 0));
  }
  get planFresh() { return !!this.plan && this.lastPlanKey === this.planKey(); }
  /** Used / Not used for every reference picture, straight from the plan (which the server works out); "pending" until the plan has caught up. */
  refView = (i: number): RefView => {
    const rp = this.plan?.references, n = this.spec.references.length;
    if (!rp || rp.used.length + rp.not_used.length !== n || (this.planBusy && !this.planFresh)) return { state: 'pending', order: 0, note: 'Working it out…' };
    const u = rp.used.findIndex((x) => x.position === i + 1);
    if (u >= 0) return { state: 'used', order: u + 1, note: '' };
    return { state: 'unused', order: 0, note: rp.not_used.find((x) => x.position === i + 1)?.reason ?? 'Not used.' };
  };
  get used(): number { return this.plan?.references?.used.length ?? 0; }

  get maskKeyNow() { return this.maskDoc ? maskKey(this.maskDoc) : ''; }
  get hasSel(): boolean { return this.maskDoc ? hasSelection(this.maskDoc) : !!this.spec.mask_asset || !!this.spec.selection.box; }
  /** the selection on screen is not yet what the server holds */
  get maskStale(): boolean { return !!this.maskDoc && this.maskKeyNow !== this.maskSaved; }
  /** keep-outside, with the mode's default when the person has not said */
  get keepOutside(): boolean { const k = this.spec.selection.keep_outside; return k ?? (this.spec.mode === 'replace_subject'); }
  get aspects() { return this.caps?.aspects?.length ? this.caps.aspects : null; }
  get upscaleOnly() { return this.spec.mode === 'upscale' && !this.spec.upscale.refine; }

  /** the pictures the runs on show have made, oldest run first */
  get resultIds(): string[] { return this.batch.flatMap((j) => (this.jobs.find((r) => r.job === j)?.outputs ?? []).filter((o) => o.kind === 'image').map((o) => o.id)); }

  /** Everything that stops Generate, in plain words, with the panel where it is fixed. Local checks come first so the button is right before the plan arrives. */
  get blockers(): Blocker[] {
    const b: Blocker[] = [], s = this.spec, n = this.needs, m = this.mode;
    if (!this.project) return [{ text: 'Choose what you want to do first.', panel: 'prompt' }];
    if (!m) return [{ text: 'Choose what you want to do.', panel: 'prompt' }];
    const st = modeState(this.caps, s.mode);
    if (!st.ok) b.push({ text: `${m.label} is not available${st.reason ? `: ${st.reason}` : '.'}`, panel: 'prompt' });
    if (n.model && !this.entry) b.push({ text: 'Choose a model.', panel: 'model' });
    else if (n.model && this.entry && !runnable(this.entry.status)) b.push({ text: `This model cannot do it${this.entry.reason ? `: ${this.entry.reason}` : '.'}`, panel: 'model' });
    if (n.source && !s.source_asset) b.push({ text: 'Add the picture you want to work on.', panel: 'source' });
    if (n.selection === 'required' && !this.hasSel) b.push({ text: 'Paint or box the part of the picture to change.', panel: 'selection' });
    if (n.refs === 'required' && !s.references.some((r) => r.active)) b.push({ text: s.references.length ? 'Switch on at least one reference picture.' : 'Add a reference picture.', panel: 'references' });
    if (n.prompt && n.promptRequired && !s.prompt.trim()) b.push({ text: s.mode === 'replace_subject' ? 'Say what to replace in a few words.' : s.mode === 'inpaint' ? 'Say what should appear in the painted part.' : s.mode === 'iphone_photo' ? 'Describe what is in the photo.' : 'Say what you want in a few words.', panel: 'prompt' });
    if (s.mode === 'upscale' && !s.size.output) b.push({ text: 'Choose the size you want.', panel: 'size' });
    if (this.uploads.some((u) => u.active)) b.push({ text: 'A picture is still uploading.', panel: 'source' });
    if (b.length) return b;
    const ignore = this.maskStale ? /selection/i : null;   // the selection is saved when you press Generate
    for (const e of this.plan?.errors ?? []) if (!(ignore && ignore.test(e))) b.push({ text: e, panel: 'size' });
    return b;
  }

  // ---- loading -------------------------------------------------------------------------------------------------------------
  async init(wanted: string | null, fresh: boolean) {
    this.loading = true; this.error = '';
    try { this.caps = await api.loadCaps(); } catch (e) { this.capsError = errText(e); }
    try { this.projects = await api.listProjects(); } catch { this.projects = []; }
    await this.refreshJobs();
    const id = wanted ?? (fresh ? null : lastProject() ?? this.projects[0]?.id ?? null);
    if (id) await this.open(id, wanted === null);
    this.loading = false;
  }

  async open(id: string, quiet = false) {
    try { await this.adopt(await api.getProject(id)); }
    catch (e) {
      if (quiet && e instanceof ApiError && e.status === 404) { remember(null); this.project = null; return; }
      this.error = e instanceof ApiError && e.status === 404 ? 'This project does not exist or is not yours.' : errText(e);
    }
  }

  private async adopt(p: Project, job?: string) {
    clearTimeout(this.saveTimer); clearTimeout(this.planTimer); clearTimeout(this.maskTimer);
    this.project = p; this.spec = normalizeSpec(p.spec);
    this.plan = null; this.planError = ''; this.lastPlanKey = '';
    this.saveState = 'saved'; this.saveError = '';
    this.maskDoc = null; this.maskSaved = ''; this.maskError = '';
    this.cropOn = false; this.copies = []; this.adjustBusy = { busy: false, progress: 0, error: '', done: null };
    for (const a of [p.source, p.mask]) if (a && typeof a === 'object' && (a as Asset).id) this.assets[(a as Asset).id] = a as Asset;
    const ids = (p.jobs ?? []).map((j) => j.id).filter(Boolean);
    const mine = this.jobs.filter((j) => j.project_id === p.id || ids.includes(j.job));
    const last = job ?? mine[0]?.job ?? ids[0] ?? null;
    this.batch = last ? this.batchFor(last) : [];
    this.picked = null; this.stage = 'picture';
    this.adjustId = this.spec.source_asset;
    remember(p.id);
    await this.loadAssets();
    await this.restoreMask();
    this.schedulePlan(0);
  }

  /** Runs that were asked for together (same Generate) are shown together, oldest first. */
  private batchFor(job: string): string[] {
    const row = this.jobs.find((r) => r.job === job), g = row?.batch?.id;
    if (!g) return [job];
    return this.jobs.filter((r) => r.batch?.id === g).sort((a, b) => (a.batch?.index ?? 0) - (b.batch?.index ?? 0)).map((r) => r.job);
  }

  async loadAssets() {
    const s = this.spec;
    const need = [s.source_asset, s.mask_asset, ...s.references.map((r) => r.asset)].filter((x): x is string => !!x && !this.assets[x]);
    await Promise.all(need.map((id) => fetchAsset(id).then((a) => { this.assets[id] = a; }).catch(() => {})));
    this.loadResults();
  }
  private loadResults() {
    for (const j of this.batch) for (const o of this.jobs.find((r) => r.job === j)?.outputs ?? []) for (const id of [o.id, ...(o.derived ?? []).map((d) => d.id)])
      if (!this.assets[id]) fetchAsset(id).then((a) => { this.assets[id] = a; }).catch(() => {});
  }
  async refreshProjects() { try { this.projects = await api.listProjects(); } catch { /* keep the old list */ } }
  async refreshJobs() {
    try { this.jobs = await api.listJobs(); } catch { /* keep the old list */ }
    this.loadResults();
  }

  // ---- editing + autosave --------------------------------------------------------------------------------------------------
  /** Change the spec; saved 600 ms after the last change. */
  edit(fn: (s: Spec) => void) { fn(this.spec); this.dirty(); }
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
      if (this.project?.id === p.id) this.project = { ...this.project, updated_at: r.updated_at ?? this.project.updated_at, title: r.title ?? this.project.title };
      if (this.saveState === 'saving') this.saveState = 'saved';
      this.saveError = '';
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        // the server also changes a project by itself (a started job is added to it): when the stored decisions are the same as ours there is nothing to choose
        const theirs: Project | undefined = e.detail?.project;
        if (theirs && JSON.stringify(normalizeSpec(theirs.spec)) === JSON.stringify(normalizeSpec(sent))) {
          this.project = { ...p, updated_at: theirs.updated_at, title: theirs.title ?? p.title };
          if (this.saveState === 'saving') this.saveState = 'saved';
          return true;
        }
        this.saveState = 'conflict'; this.saveError = 'This project was changed somewhere else (another window or device).';
      } else { this.saveState = 'error'; this.saveError = errText(e); }
      return false;
    }
  }
  async resolveConflict(keepMine: boolean) {
    const p = this.project; if (!p) return;
    const theirs = await api.getProject(p.id);
    if (keepMine) { this.project = { ...p, updated_at: theirs.updated_at }; this.saveState = 'dirty'; await this.saveNow(); } else await this.adopt(theirs);
  }
  flush() {
    const p = this.project;
    if (!p || this.saveState !== 'dirty') return;
    clearTimeout(this.saveTimer);
    api.saveOnExit(p.id, $state.snapshot(this.spec) as Spec, p.updated_at);
  }
  retrySave() { this.saveState = 'dirty'; return this.saveNow(); }

  // ---- the plan (real numbers from the server) -----------------------------------------------------------------------------
  private planKey() { return JSON.stringify($state.snapshot(this.spec)); }
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

  // ---- starting a project / choosing the mode -----------------------------------------------------------------------------------
  /** Start (or switch) to a mode. With no project yet this creates one on the server. */
  async chooseMode(key: string, source?: Asset) {
    const st = modeState(this.caps, key);
    if (!st.ok || !st.entry) return;
    if (!this.project) {
      try {
        const p = await api.createProject({ mode: key, ...(source ? { source_asset: source.id } : {}) });
        if (source) this.assets[source.id] = source;
        await this.adopt(p);
        this.refreshProjects();
        this.edit(() => this.applyMode(key, true));
      } catch (e) { this.error = errText(e); }
      return;
    }
    this.edit(() => this.applyMode(key));
  }

  private applyMode(key: string, fresh = false) {
    const e = entryOfKey(this.caps, key); if (!e) return;
    const s = this.spec, before = s.mode;
    s.mode = key;
    // a model chosen by hand stays only while it can still do the mode
    if (s.model && !runnable(modelEntry(e, s.model)?.status)) s.model = null;
    const n = needsOf(key, e);
    if (before !== key || fresh) {
      s.aspect = n.source ? { id: 'source', w: null, h: null } : key === 'iphone_photo' ? { id: '9:16', w: 9, h: 16 } : s.aspect.id === 'source' ? { id: '9:16', w: 9, h: 16 } : s.aspect;
      s.size = { native: null, output: null };
      s.preset = key === 'iphone_photo' ? { id: 'iphone_photo', scenes: s.preset.scenes, finish: true } : { id: null, scenes: [], finish: false };
      s.outpaint = { ...s.outpaint, target: null, pad: null };
      if (key === 'upscale') s.upscale = { ...s.upscale, refine: false };
    }
    this.stage = 'picture';
  }

  chooseModel(id: string | null) { this.edit((s) => { s.model = id; }); }
  toggleChip(id: string) { this.edit((s) => { s.preset.scenes = s.preset.scenes.includes(id) ? s.preset.scenes.filter((x) => x !== id) : [...s.preset.scenes, id]; }); }

  // ---- pictures -----------------------------------------------------------------------------------------------------------------
  private maxMb() { return app.me?.ui?.composer?.media_upload_max_mb ?? 512; }

  /** Route dropped, pasted or chosen files. `as` says where they go; 'auto' = the source when the mode needs one and has none, else references. */
  async addFiles(files: File[], as: 'source' | 'ref' | 'auto' = 'auto') {
    this.notice = '';
    const batch = ++this.batchSeq;
    let first = true;
    for (const f of files) {
      if (!isMedia(f)) { this.notice = `${f.name} is not a picture.`; continue; }
      if (/^video\//.test(f.type) || /\.(mp4|m4v|mov|webm|mkv|avi)$/i.test(f.name)) { this.notice = 'Imajutsu works with pictures. For video, use OpenJutsu Studio.'; continue; }
      const why = precheck(f, this.maxMb());
      if (why) { this.notice = why; continue; }
      const to: 'source' | 'ref' = as === 'auto' ? (!this.project || (this.needs.source && !this.spec.source_asset && first) ? 'source' : this.needs.refs !== 'none' ? 'ref' : 'source') : as;
      first = false;
      const u = new Upload(f, false);
      this.target.set(u, to); this.batchOf.set(u, batch);
      this.uploads = [...this.uploads, u];
      u.start().then((a) => this.finish(u, a));
    }
  }
  targetOf = (u: Upload) => this.target.get(u);
  retryUpload(u: Upload) { u.retry().then((a) => this.finish(u, a)); }
  dismissUpload(u: Upload) { u.cancel(); this.uploads = this.uploads.filter((x) => x !== u); }
  private async finish(u: Upload, a: Asset | null) {
    if (a && this.target.get(u) === 'source') { this.uploads = this.uploads.filter((x) => x !== u); await this.setSource(a); return; }
    this.landed(u);
  }
  /** Reference pictures chosen together are added together, in the order chosen (uploads finish in any order). */
  private landed(u: Upload) {
    const batch = this.batchOf.get(u);
    const mates = this.uploads.filter((x) => this.batchOf.get(x) === batch && this.target.get(x) === 'ref');
    if (u.status === 'cancelled') this.uploads = this.uploads.filter((x) => x !== u);
    if (mates.some((x) => x.active)) return;
    const ready = mates.filter((x) => x.status === 'ready' && x.asset && !this.added.has(x));
    for (const x of ready) this.added.add(x);
    if (ready.length) this.addRefs(ready.map((x) => x.asset!));
    this.uploads = this.uploads.filter((x) => !ready.includes(x));
  }

  /** Make a picture the source. A selection drawn on a picture of another size is dropped. */
  async setSource(a: Asset) {
    if (a.kind !== 'image') { this.notice = 'Only pictures can be the source.'; return; }
    this.assets[a.id] = a;
    if (!this.project) { await this.chooseMode(modeState(this.caps, 'edit').ok ? 'edit' : 'restyle', a); return; }
    const same = !!this.maskDoc && a.width === this.maskDoc.w && a.height === this.maskDoc.h;
    this.edit((s) => { s.source_asset = a.id; if (!same) { s.mask_asset = null; s.selection.box = null; } });
    if (!same) { this.maskDoc = null; this.maskSaved = ''; }
    this.adjustId = a.id;
    this.stage = 'picture';
  }
  clearSource() { this.edit((s) => { s.source_asset = null; s.mask_asset = null; s.selection.box = null; }); this.maskDoc = null; this.maskSaved = ''; }

  addRefs(list: Asset[]) {
    const pics = list.filter((a) => a.kind === 'image');
    if (pics.length < list.length) this.notice = 'Only pictures can be references.';
    if (!pics.length) return;
    this.edit((s) => { for (const a of pics) { this.assets[a.id] = a; s.references.push({ asset: a.id, active: true, role: null, preserve: '', label: '' }); } });
  }
  removeRef(i: number) { this.edit((s) => { s.references.splice(i, 1); }); }
  moveRef(i: number, to: number) { this.edit((s) => { s.references = move(s.references, i, to); }); }
  setActive(i: number, on: boolean) { this.edit((s) => { s.references[i].active = on; }); }
  /** Switch on the first pictures and leave the rest off, so the list matches what the model takes. */
  autoFillRefs() { const k = this.slots; this.edit((s) => { s.references.forEach((r, i) => { r.active = i < k; }); }); }

  // ---- the selection --------------------------------------------------------------------------------------------------------------
  /** Strokes from the editor. They are saved as a picture (or a box) a moment after the last change. */
  setMaskDoc(doc: MaskDoc | null) {
    this.maskDoc = doc;
    clearTimeout(this.maskTimer);
    if (!doc || !this.project) return;
    this.maskTimer = setTimeout(() => { this.saveMask(); }, 1100);
  }

  /** Bring back the strokes of the saved selection: from the picture's text chunk, or from the box. */
  private async restoreMask() {
    const s = this.spec, src = this.source;
    try {
      if (s.mask_asset) {
        const r = await fetch(`/api/media/assets/${s.mask_asset}/content`, { credentials: 'same-origin' });
        if (r.ok) {
          const doc = readStrokes(new Uint8Array(await r.arrayBuffer()));
          if (doc && (!src?.width || (doc.w === src.width && doc.h === src.height))) { this.maskDoc = doc; this.maskSaved = maskKey(doc); }
        }
      } else if (s.selection.box && src?.width && src.height) {
        const doc = fromBox(s.selection.box, src.width, src.height);
        this.maskDoc = doc; this.maskSaved = maskKey(doc);
      }
    } catch { /* the selection stays as the server has it */ }
  }

  /** Make the selection the server's: one box needs nothing uploaded; anything else becomes a picture in the Library. */
  async saveMask(): Promise<boolean> {
    clearTimeout(this.maskTimer);
    const m = this.maskDoc;
    if (!m) return true;
    const key = maskKey(m);
    if (key === this.maskSaved) return true;
    this.maskBusy = true; this.maskError = '';
    try {
      if (!hasSelection(m)) {
        this.edit((s) => { s.mask_asset = null; s.selection.box = null; });
      } else {
        const box = asBox(m);
        if (box) {
          this.edit((s) => { s.mask_asset = null; s.selection.box = box; });
        } else {
          const blob = await renderPng(m);
          const up = new Upload(new File([blob], `selection-${new Date().toISOString().slice(11, 19).replace(/:/g, '')}.png`, { type: 'image/png' }), false);
          const a = await up.start();
          if (!a) throw new Error(up.error || 'The selection could not be saved.');
          this.assets[a.id] = a;
          this.edit((s) => { s.mask_asset = a.id; s.selection.box = null; });
        }
      }
      this.maskSaved = key;
      return true;
    } catch (e) { this.maskError = errText(e); return false; }
    finally { this.maskBusy = false; }
  }
  newMask(): MaskDoc | null {
    const src = this.source; if (!src?.width || !src?.height) return null;
    return emptyMask(src.width, src.height);
  }
  addOpToMask(op: Parameters<typeof addOp>[1]) { const d = this.maskDoc ?? this.newMask(); if (d) this.setMaskDoc(addOp(d, op)); }

  // ---- generate -----------------------------------------------------------------------------------------------------------------------
  async runGenerate(): Promise<boolean> {
    const p = this.project; if (!p || this.gen.busy) return false;
    this.gen = { busy: true, error: '', accounting: null };
    try {
      if (this.maskDoc && !(await this.saveMask())) { this.gen = { busy: false, error: this.maskError || 'The selection could not be saved first.', accounting: null }; return false; }
      if (!(await this.saveNow())) { this.gen = { busy: false, error: this.saveError || 'The project could not be saved first.', accounting: null }; return false; }
      if (this.upscaleOnly) return await this.runUpscale();
      const r = await api.generate(p.id, $state.snapshot(this.spec) as Spec, this.spec.count);
      const ids = (r.jobs?.length ? r.jobs : [r.job]).filter(Boolean);
      this.batch = ids; this.picked = null; this.stage = 'picture';
      await this.refreshJobs();
      this.gen = { busy: false, error: '', accounting: r.accounting ?? null };
      api.getProject(p.id).then((fresh) => { if (this.project?.id === p.id) this.project = { ...this.project, updated_at: fresh.updated_at }; }).catch(() => {});
      return true;
    } catch (e) { this.gen = { busy: false, error: errText(e), accounting: null }; return false; }
  }

  /** Upscale is scaling, not a model: it makes a copy at the wanted size. */
  private async runUpscale(): Promise<boolean> {
    const src = this.source, out = this.spec.size.output;
    if (!src || !out) { this.gen = { busy: false, error: 'Choose the size you want first.', accounting: null }; return false; }
    try {
      const { task } = await api.upscale(src.id, out.width, out.height);
      const a = await api.followTask(task);
      this.assets[a.id] = a; this.copies = [a.id, ...this.copies.filter((x) => x !== a.id)];
      this.picked = a.id; this.stage = 'result';
      this.gen = { busy: false, error: '', accounting: { scaled: true } };
      return true;
    } catch (e) { this.gen = { busy: false, error: errText(e), accounting: null }; return false; }
  }

  // ---- adjust ----------------------------------------------------------------------------------------------------------------------------
  get recipe(): Recipe { return this.spec.adjustments; }
  get adjustTarget(): Asset | null { return this.asset(this.adjustId) ?? this.source; }
  setRecipe(fn: (r: Recipe) => void) { this.edit((s) => { const r = { ...s.adjustments }; fn(r); s.adjustments = cleanRecipe(r); }); }
  resetRecipe() { this.edit((s) => { s.adjustments = {}; }); }
  adjustPicture(a: Asset) { this.assets[a.id] = a; this.adjustId = a.id; this.cropOn = false; this.panel = 'adjust'; this.stage = 'adjust'; }

  async applyAdjust() {
    const t = this.adjustTarget; if (!t || isNeutral(this.recipe) || this.adjustBusy.busy) return;
    this.adjustBusy = { busy: true, progress: 0, error: '', done: null };
    try {
      const { task } = await api.adjustApply(t.id, cleanRecipe(this.recipe));
      const a = await api.followTask(task, (p) => (this.adjustBusy.progress = p));
      this.assets[a.id] = a; this.copies = [a.id, ...this.copies.filter((x) => x !== a.id)];
      this.adjustBusy = { busy: false, progress: 1, error: '', done: a };
      this.refreshJobs();
    } catch (e) { this.adjustBusy = { busy: false, progress: 0, error: errText(e), done: null }; }
  }

  // ---- results ------------------------------------------------------------------------------------------------------------------------------
  /** Put a result to work: as the source of the next edit. */
  async useAsSource(a: Asset) {
    this.assets[a.id] = a;
    if (!this.needs.source) {
      const next = ['edit', 'restyle', 'inpaint'].find((k) => modeState(this.caps, k).ok);
      if (next) await this.chooseMode(next);
    }
    await this.setSource(a);
    this.panel = 'source';
  }
  sendAsReference(a: Asset) {
    this.assets[a.id] = a;
    if (this.needs.refs === 'none') {
      const next = ['reference', 'edit'].find((k) => modeState(this.caps, k).ok);
      if (next) this.edit(() => this.applyMode(next));
    }
    this.addRefs([a]);
    this.panel = 'references';
  }

  /** A run finished while the page was watching: make what the run's own settings ask for (phone finish, original outside the selection). */
  jobFinished(job: string) {
    if (this.handled.has(job)) return;
    this.handled.add(job);
    this.chain = this.chain.then(async () => {
      await this.refreshJobs();
      const row = this.jobs.find((r) => r.job === job), blk = row?.imajutsu, spec: Spec | undefined = blk?.spec;
      if (!row || !spec) return;
      const finish = spec.preset?.finish ? blk?.plan?.prompt?.preset?.finish_recipe : null;
      const hasSel = !!(blk?.mask_asset || spec.selection?.box || spec.mode === 'outpaint');
      const keep = hasSel && ['edit', 'replace_subject', 'inpaint', 'outpaint'].includes(spec.mode) && (spec.selection?.keep_outside ?? spec.mode === 'replace_subject');
      for (const o of row.outputs ?? []) {
        if (o.kind !== 'image' || (o.derived ?? []).length) continue;
        try {
          if (keep) { const { task } = await api.keepOutside(o.id); await api.followTask(task); }
          if (finish) { const { task } = await api.adjustApply(o.id, finish); await api.followTask(task); }
        } catch (e) { this.notice = errText(e); }
      }
      await this.refreshJobs();
    }).catch(() => {});
  }

  /** Make the "original kept outside the selection" copy of a result by hand. Returns a sentence when it failed. */
  async keepOutsideOf(a: Asset): Promise<string> {
    try { const { task } = await api.keepOutside(a.id); const d = await api.followTask(task); this.assets[d.id] = d; await this.refreshJobs(); return ''; } catch (e) { return errText(e); }
  }

  /** Open an earlier run: the server sets the project back to exactly what the run was made from. */
  async openJob(row: JobRow) {
    try {
      const r = await api.reopenJob(row.job);
      this.dropped = r.dropped ?? [];
      await this.adopt(r.project, row.job);
      this.refreshProjects();
    } catch (e) { this.error = errText(e); }
  }

  startOver() {
    clearTimeout(this.saveTimer); clearTimeout(this.planTimer); clearTimeout(this.maskTimer);
    this.project = null; this.spec = defaultSpec(); this.plan = null; this.batch = []; this.picked = null; this.saveState = 'idle'; this.stage = 'picture'; this.panel = 'prompt';
    this.maskDoc = null; this.maskSaved = ''; this.adjustId = null; this.copies = [];
    remember(null);
  }
  async removeProject(id: string) {
    await api.deleteProject(id);
    this.projects = this.projects.filter((p) => p.id !== id);
    if (this.project?.id === id) this.startOver();
  }
}
