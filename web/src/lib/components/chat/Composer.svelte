<script lang="ts">
  // The one place a turn starts: write, attach, choose what to make (reply, picture, video) and with which model.
  // Everything offered here is what /api/me says this person may do; the server checks each request again.
  import { tick, untrack } from 'svelte';
  import { app } from '$lib/session.svelte';
  import { get, del, qs, ApiError } from '$lib/api';
  import { loadCatalog, catalog, type Operation } from '$lib/chat/store.svelte';
  import { errText, type Thread, type Turn, type Attachment } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import ContextBar from '$lib/components/ContextBar.svelte';
  import ModelChooser from './ModelChooser.svelte';
  import ProfileForm from './ProfileForm.svelte';
  import MediaDropzone from '$lib/components/media/MediaDropzone.svelte';
  import MediaAttachmentTray from '$lib/components/media/MediaAttachmentTray.svelte';
  import MediaAssetPicker from '$lib/components/media/MediaAssetPicker.svelte';
  import AttachMenu from '$lib/components/media/AttachMenu.svelte';
  import PresetOptions from './PresetOptions.svelte';
  import { portraitSizes, tierOf, type PresetInfo } from '$lib/media/preset';
  import { trayFor, type AddContext } from '$lib/media/tray.svelte';
  import { autoAssign, currentOperation, evaluate, opLabel, plainSlots, profileSlots, type Slot } from '$lib/media/roles';
  import { isLibraryId, type Asset } from '$lib/media/assets';

  let { thread, workspace, assistantId = $bindable(null), collectionIds = $bindable([]), onsend, onstop, onctx, onprotect }:
    { thread: Thread; workspace: 'chat' | 'code'; assistantId: string | null; collectionIds: string[]; onsend: (t: Turn) => void; onstop: () => void;
      onctx: () => void; onprotect: () => void } = $props();

  const ui = $derived(app.me.ui);
  const flags = $derived(ui.ui ?? {});
  type Op = 'text' | 'image' | 'video';
  let op = $state<Op>('text');
  const textOp = $derived<Operation>(workspace === 'code' ? 'code' : 'text');
  const ops = $derived<{ id: Op; label: string; icon: string }[]>([
    { id: 'text' as Op, label: workspace === 'code' ? 'Code' : 'Chat', icon: workspace === 'code' ? 'code' : 'chat' },
    ...(workspace === 'chat' && ui.operations?.image?.enabled ? [{ id: 'image' as Op, label: 'Picture', icon: 'image' }] : []),
    ...(workspace === 'chat' && ui.operations?.video?.enabled ? [{ id: 'video' as Op, label: 'Video', icon: 'video' }] : [])
  ]);
  const showOps = $derived(flags.mode_selector !== false && ops.length > 1);
  $effect(() => { if (!ops.some((o) => o.id === op)) op = 'text'; });

  const catOp = $derived<Operation>(op === 'text' ? textOp : op);
  const cat = $derived(catalog(catOp));
  let chosen = $state<Record<string, string>>({});
  const wanted: Record<string, string> = {};
  $effect(() => { const o = catOp; untrack(() => loadCatalog(o)); });
  // fall back to the administrator's default (or the first allowed model) whenever the current choice is not offered
  $effect(() => {
    const c = cat; if (!c || c.error) return;
    const cur = untrack(() => chosen[catOp]);
    const want = wanted[catOp];  // the model of a saved draft wins over the default, once
    if (want && c.models.some((m: any) => m.id === want)) { chosen[catOp] = want; delete wanted[catOp]; return; }
    if (!cur || !c.models.some((m: any) => m.id === cur)) chosen[catOp] = c.default ?? c.models[0]?.id ?? '';
  });
  const modelId = $derived(chosen[catOp] ?? '');
  const model = $derived(cat?.models?.find((m: any) => m.id === modelId));

  /** A deep link may open the composer ready to make a picture or a video. */
  export function setOp(o: string) { if (ops.some((x) => x.id === o)) op = o as Op; }
  /** Called by the page: a conversation remembers its model; a deep link may ask for one. */
  export function prefer(id: string | null | undefined, o: Operation = textOp) {
    if (!id) return;
    loadCatalog(o).then((c) => { if (c?.models?.some((m: any) => m.id === id)) chosen[o] = id; });
  }

  // ---- text ---------------------------------------------------------------------------------------------------
  let text = $state(''), box: HTMLTextAreaElement | undefined = $state();
  export function focus() { box?.focus(); }
  export function setText(t: string) { text = t; autosize(); focus(); }
  function autosize() { tick().then(() => { if (!box) return; box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight, 240) + 'px'; }); }

  // ---- attachments --------------------------------------------------------------------------------------------
  // The tray (lib/media/tray.svelte.ts) holds what is attached; it outlives this component, so attachments and running uploads survive moving
  // to another page and back. Everything below only decides which part each file plays for the chosen operation.
  const canAttach = $derived(!!ui.composer.attachments);
  const canMedia = $derived(!!ui.composer.media_library);
  const maxMb = $derived<number>(ui.composer.media_upload_max_mb ?? 512);
  const tray = $derived(trayFor(`${workspace}:${thread.id ?? 'new'}`, app.me.user.id));
  const hasImage = $derived(tray.ready.some((i) => i.kind === 'image'));
  let attNote = $state(''), pickerOpen = $state(false), replacing = $state<string | null>(null), fileInput: HTMLInputElement | undefined = $state();
  const cx = $derived<AddContext>({
    maxMb, ephemeral: thread.ephemeral, media: canMedia, legacy: canAttach ? (f: File) => thread.attach(f) : undefined, limit: 12,
    // a no-save conversation reads its pictures from memory the older way
    legacyImages: thread.ephemeral && op === 'text', slots: () => slots
  });

  export function addFiles(list: FileList | File[]) {
    attNote = '';
    if (!canAttach && !canMedia) { attNote = 'Attachments are turned off for you.'; return; }
    const refused = tray.addFiles([...list], cx);
    if (refused.length) attNote = refused[0] + (refused.length > 1 ? ` (+${refused.length - 1} more)` : '');
  }
  /** A picture or video from the Library, or a picture generated earlier, joins the message. */
  export function addAsset(a: Asset) {
    if (op === 'text' && a.kind === 'video' && ops.some((o) => o.id === 'video')) op = 'video';
    tray.addAsset(a, slots);
    focus();
  }
  /** A picture or video generated (or edited) earlier becomes the source for an edit, an animation or an editing operation. */
  export function useSource(a: any) {
    const video = a.kind === 'video' || a.mime?.startsWith('video/');
    const asset = { origin: 'generated', name: video ? 'Generated video' : 'Generated picture', ...a, kind: video ? 'video' : 'image' } as Asset;
    if (!video) for (const it of [...tray.items]) if (it.role === 'source' && it.roleAuto) tray.remove(it.key);
    tray.addAsset(asset, slots);
    if (video && ops.some((o) => o.id === 'video')) op = 'video';
    else if (!video && op === 'text' && ops.some((o) => o.id === 'image')) op = 'image';
    focus();
  }
  function pick(e: Event) {
    const inp = e.currentTarget as HTMLInputElement;
    if (!inp.files?.length) return;
    if (replacing) { const k = replacing; replacing = null; const r = tray.replaceWithFile(k, inp.files[0], cx); if (r.length) attNote = r[0]; }
    else addFiles(inp.files);
    inp.value = '';
  }
  function replace(key: string) { replacing = key; fileInput?.click(); }
  function choose() { replacing = null; fileInput?.click(); }
  /** The add card of one slot: the file picker offers only that kind of file. */
  function chooseFor(sl: Slot) {
    replacing = null;
    if (!fileInput) return;
    const was = fileInput.accept;
    fileInput.accept = `${sl.kind}/*`;
    fileInput.click();
    setTimeout(() => { if (fileInput) fileInput.accept = was; }, 1000);
  }

  // ---- picture and video settings (from the engine's limits for the chosen model) ------------------------------
  const mediaOpts = $state<Record<string, any>>({});
  let mediaErr = $state(''), setOpen = $state(false);
  let mp = $state<{ size: string; seed: string; negative: string; steps: string | number; frames: number; fps: number }>({ size: '', seed: '', negative: '', steps: '', frames: 0, fps: 0 });
  $effect(() => {
    if (op === 'text' || mediaOpts[op]) return;
    const k = op;
    get(`/api/media/options${qs({ kind: k })}`).then((o) => (mediaOpts[k] = o)).catch((e) => (mediaErr = errText(e)));
  });
  const mopt = $derived(op !== 'text' ? mediaOpts[op]?.models?.find((m: any) => m.id === modelId) : null);
  // An editing profile (OpenJutsu ...) replaces the ordinary picture/video settings with a form built from the engine's own schema.
  const prof = $derived(op === 'video' && mopt?.profile ? mopt.profile : null);
  let pvals = $state<Record<string, any>>({}), pproblem = $state(''), profOpen = $state(false);
  $effect(() => { void modelId; pvals = {}; pproblem = ''; });
  const operation = $derived(prof ? currentOperation(prof, pvals) : null);
  const opChoices = $derived<string[]>(prof?.inputs_schema?.operation?.values ?? []);
  /** The words typed in the message box go to the profile's main text input for this operation: its prompt when the operation uses one, else the first short text input. */
  const textField = $derived.by<string | null>(() => {
    const sch: Record<string, any> = prof?.inputs_schema ?? {};
    const uses = (x: any) => !operation || !x.operations || x.operations.includes(operation);
    if (sch.prompt && uses(sch.prompt)) return 'prompt';
    return Object.entries(sch).find(([, x]) => x.type === 'string' && uses(x) && (x.max_length ?? 500) <= 1000)?.[0] ?? null;
  });
  const textSpec = $derived<any>(textField ? prof?.inputs_schema?.[textField] : null);
  /** Video models list their resolutions; picture models give a side range and a multiple, so offer common shapes. */
  const sizes = $derived.by<{ v: string; label: string }[]>(() => {
    const lim = mopt?.limits ?? {};
    if (op === 'video') return (lim.resolutions ?? []).map(([w, h]: number[]) => ({ v: `${w}x${h}`, label: `${w} × ${h}` }));
    const mult = lim.multiple || 8, lo = lim.min_side || 256, hi = lim.max_side || 1024, s0 = mopt?.defaults?.width || 512;
    const fit = (n: number) => Math.min(hi, Math.max(lo, Math.round(n / mult) * mult));
    const shapes: [string, number, number][] = [['Square', 1, 1], ['Landscape', 4, 3], ['Portrait', 3, 4], ['Wide', 16, 9], ['Tall', 9, 16]];
    const out: { v: string; label: string }[] = [];
    for (const [name, a, b] of shapes) {
      const long = fit(s0 * Math.sqrt(Math.max(a, b) / Math.min(a, b))), short = fit((long * Math.min(a, b)) / Math.max(a, b));
      const [w, h] = a >= b ? [long, short] : [short, long];
      if (!out.some((o) => o.v === `${w}x${h}`)) out.push({ v: `${w}x${h}`, label: `${name} (${w} × ${h})` });
    }
    return out;
  });
  const rng = (k: string): [number | undefined, number | undefined] => { const r = mopt?.limits?.[k]; return Array.isArray(r) ? [r[0], r[1]] : [undefined, undefined]; };
  let lastM = '';
  $effect(() => {
    if (!mopt || mopt.id === lastM) return;
    lastM = mopt.id; const d = mopt.defaults ?? {};
    mp = { size: d.width && d.height ? `${d.width}x${d.height}` : '', seed: '', negative: '', steps: d.steps ? String(d.steps) : '', frames: d.frames ?? 0, fps: d.fps ?? 0 };
  });
  const vmax = $derived(mediaOpts.video?.video_max_seconds ?? null);
  const seconds = $derived(op === 'video' && mp.fps ? mp.frames / mp.fps : 0);
  function mediaParams(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    // iPhone Photo is always 9:16: its own size list (the server checks the shape and the model's limits again)
    const size = presetOn ? preset.size : mp.size;
    if (size) { const [w, h] = size.split('x').map(Number); out.width = w; out.height = h; }
    if (String(mp.seed).trim() !== '' && Number.isInteger(Number(mp.seed))) out.seed = Number(mp.seed);
    if (mp.negative.trim() && mopt?.negative_prompt && !presetOn) out.negative_prompt = mp.negative.trim();
    if (mp.steps && ui.composer.edit_params && !presetOn) out.steps = Number(mp.steps);
    if (op === 'video' && mp.frames && mp.fps) { out.frames = mp.frames; out.fps = mp.fps; }
    return out;
  }
  // ---- the iPhone Photo option of the picture operation (state first: the slots below depend on it)
  let presetInfo = $state<PresetInfo | null>(null);
  let preset = $state<{ on: boolean; scenes: string[]; size: string; back: string }>({ on: false, scenes: [], size: '', back: '' });
  const presetOn = $derived(preset.on && op === 'image' && !prof && !!presetInfo);
  const needSource = $derived(op !== 'text' && !presetOn && mopt && mopt.modes?.length && !mopt.modes.some((x: string) => (op === 'image' ? x === 'txt2img' : x === 't2v' || x === 'ti2v')));
  const cannotUseSource = $derived(op !== 'text' && !presetOn && hasImage && mopt && mopt.modes?.length && !mopt.modes.some((x: string) => (op === 'image' ? x === 'edit' || x === 'img2img' : x === 'i2v' || x === 'ti2v')));

  // What the chosen model really does with pictures. All of it comes from the engine's own capabilities (modes, number of pictures), never from a model name.
  const alias = $derived<string>(model?.label ?? model?.alias ?? 'This model');
  const sees = $derived(!model || (model.capabilities ?? []).includes('vision'));
  const takes = $derived<number>(Number(mopt?.references) || 0);
  // ---- the iPhone Photo option of the picture operation ----------------------------------------------------------
  let presetTried = false, pendingPreset = $state(false);
  $effect(() => {
    if (op !== 'image' || presetInfo || presetTried) return;
    presetTried = true;
    get('/api/imajutsu/capabilities').then((c: any) => { const p = c?.presets?.find((x: any) => x.id === 'iphone_photo'); if (p) presetInfo = p; }).catch(() => {});
  });
  const sizeChoices = $derived(portraitSizes(mopt?.limits));
  $effect(() => { if (presetOn && sizeChoices.length && !sizeChoices.some((s) => `${s.w}x${s.h}` === preset.size)) preset.size = `${sizeChoices[0].w}x${sizeChoices[0].h}`; });
  const presetNoText = $derived(presetOn && !!mopt && !!mopt.modes?.length && !mopt.modes.includes('txt2img'));
  const tier = $derived(tierOf(presetInfo, modelId));
  /** Switch the preset on (choosing the model the routing table recommends for it) or off (the earlier model returns). */
  export function setPreset(on: boolean) {
    if (!presetInfo) { if (on) { pendingPreset = true; op = 'image'; } return; }
    if (on === preset.on) return;
    preset.on = on;
    if (on) {
      op = 'image';
      preset.back = chosen.image ?? '';
      // the model the routing table recommends for the preset (its default quality level); the quality choice in the options picks another
      const pick = presetInfo.tiers.find((x) => x.default && x.status === 'ready') ?? presetInfo.tiers.find((x) => x.status === 'ready');
      const rec = pick?.model_id ?? presetInfo.model_id;
      if (rec && catalog('image')?.models?.some((m: any) => m.id === rec)) chosen.image = rec;
    } else {
      if (preset.back && tierOf(presetInfo, chosen.image) && catalog('image')?.models?.some((m: any) => m.id === preset.back)) chosen.image = preset.back;
      preset.back = ''; setOpen = false;
    }
  }
  $effect(() => { if (presetInfo && pendingPreset) { pendingPreset = false; untrack(() => setPreset(true)); } });

  const slots = $derived<Slot[]>(prof ? profileSlots(prof, operation) : presetOn ? [] : plainSlots(op, { needSource: !!needSource, canUseSource: !cannotUseSource, vision: true, refs: takes, alias }));
  const noSlotWhy = $derived<{ image?: string }>(prof ? {} : presetOn ? { image: 'iPhone Photo makes a new picture from words, so it does not use pictures.' }
    : op === 'text' ? {} : op === 'image' ? { image: `${alias} makes pictures from words only; it cannot edit pictures.` } : { image: `${alias} cannot animate a picture.` });
  /** a chat model that cannot see pictures still gets the message with its attachments, but only their names: said on each thumbnail */
  const tags = $derived<Record<string, string>>(op === 'text' && !sees ? Object.fromEntries(tray.items.filter((i) => i.kind === 'image').map((i) => [i.key, 'Names only'])) : {});
  const verdict = $derived(evaluate(tray.items, slots, operation, { noSlot: noSlotWhy }));
  const slotKey = $derived(JSON.stringify(slots.map((x) => [x.input, x.kind, x.max])));
  // roles follow the operation: files whose role the operation does not use are kept (and shown as unused), never dropped
  $effect(() => { void slotKey; void tray; untrack(() => { autoAssign(tray.items, slots); tray.save(); }); });
  /** The order is the meaning (the first picture is the one to change, the rest are references): after a move, the roles the program chose follow it. */
  function moved() { for (const it of tray.items) if (it.roleAuto) it.role = null; autoAssign(tray.items, slots); tray.save(); }
  /** Places that still want a file, shown as visible add cards (an editing operation's source video and reference pictures). */
  const emptySlots = $derived(slots.filter((sl) => tray.items.filter((i) => i.role === sl.input && verdict.state[i.key] === 'used').length < sl.max));
  const usedItems = $derived(tray.ready.filter((i) => verdict.state[i.key] === 'used'));
  /** pictures attached that the chosen model cannot use at all, and pictures that will really be sent with a picture or video request */
  const usedPics = $derived(tray.ready.filter((i) => i.kind === 'image').length);
  const sentCount = $derived(op === 'text' || prof ? 0 : usedItems.filter((i) => i.kind === 'image' && (i.role === 'source' || (op === 'image' && i.role === 'ref'))).length);
  const attProblem = $derived(tray.busy ? 'Wait for the upload to finish.' : tray.failed ? 'Retry or remove the file that did not upload.' : verdict.problems[0] ?? '');

  // ---- the draft: what is being written survives moving to another page and a reload (per conversation, in this browser tab) -------------
  const dkey = $derived(`sem.composer.${app.me.user.id}.${workspace}:${thread.id ?? 'new'}`);
  let restored = false;
  function restoreDraft() {
    try {
      const d = JSON.parse(sessionStorage.getItem(dkey) ?? 'null');
      if (d) {
        if (d.op && ops.some((o) => o.id === d.op)) op = d.op;
        if (typeof d.text === 'string' && !text) { text = d.text; autosize(); }
        if (d.model) wanted[d.op === 'image' || d.op === 'video' ? d.op : textOp] = d.model;
        if (d.preset && d.op === 'image') { preset.scenes = Array.isArray(d.preset.scenes) ? d.preset.scenes : []; preset.size = d.preset.size ?? ''; preset.on = true; }
      }
    } catch { /* no saved draft */ }
    restored = true;
  }
  restoreDraft();
  let saveT: ReturnType<typeof setTimeout> | undefined;
  $effect(() => {
    const snapshot = JSON.stringify({ op, model: chosen[catOp] ?? null, text, preset: presetOn ? { scenes: preset.scenes, size: preset.size } : null });
    const key = dkey;
    if (!restored) return;
    clearTimeout(saveT);
    saveT = setTimeout(() => { try { if (op === 'text' && !text && !chosen[catOp] && !preset.on) sessionStorage.removeItem(key); else sessionStorage.setItem(key, snapshot); } catch { /* storage unavailable */ } }, 250);
    return () => clearTimeout(saveT);
  });

  // ---- sending -----------------------------------------------------------------------------------------------
  const busy = $derived(thread.busy);
  const tooLong = $derived(text.length > ui.composer.max_chars);
  const promptRequired = $derived(!!textSpec?.required);
  const textTooLong = $derived(!!textSpec?.max_length && text.length > textSpec.max_length);
  const blocked = $derived(!modelId || tray.busy || tray.failed || tooLong || textTooLong || (op !== 'text' && (prof ? (!!pproblem || !!verdict.problems.length || (promptRequired && !text.trim()))
    : (!text.trim() || (needSource && !hasImage) || presetNoText || (vmax && seconds > vmax + 1e-9) || !!verdict.problems.length))));
  const canSend = $derived(!busy && !blocked && (!!text.trim() || (op === 'text' && usedItems.length > 0) || (!!prof && usedItems.some((i) => i.kind === 'video'))));
  /** What the message carries, in the shape the thread sends. */
  const asAttachment = (i: (typeof tray.items)[number]): Attachment => i.legacy ?? { id: i.asset!.id, name: i.name, kind: i.kind === 'video' ? 'video' : 'image', mime: i.asset!.mime, size: i.asset!.size,
    width: i.asset!.width, height: i.asset!.height, duration: i.asset!.duration, role: i.role ?? undefined, generated: i.asset!.origin === 'generated' } as Attachment;
  function send() {
    if (!canSend) return;
    // only what the chosen operation uses goes with the message; the rest stays where it is until the operation changes back
    // in an ordinary picture request the picture to change comes first and the reference pictures follow in the order shown
    const sent = usedItems.filter((i) => (op === 'text' ? i.kind !== 'video' : prof ? true : i.role === 'source' || (op === 'image' && i.role === 'ref')))
      .sort((a, b) => (op === 'image' && !prof ? Number(b.role === 'source') - Number(a.role === 'source') : 0));
    const byRole: Record<string, string[]> = {};
    if (prof) for (const i of sent) if (i.asset && i.role) (byRole[i.role] ??= []).push(i.asset.id);
    const turn: Turn = { content: text.trim(), attachments: sent.map(asAttachment), operation: op, modelId,
      modelLabel: model?.label ?? model?.alias ?? 'Assistant', media: op !== 'text' ? mediaParams() : undefined,
      sourceId: op !== 'text' && !prof ? sent.find((i) => i.kind === 'image')?.asset?.id ?? sent.find((i) => i.kind === 'image')?.legacy?.id ?? null : null,
      label: prof ? `${operation ? opLabel(operation) : 'Edit'} · ${prof.name ?? model?.alias ?? 'editing'}` : undefined,
      preset: presetOn ? { id: 'iphone_photo', scenes: [...preset.scenes] } : undefined,
      profile: prof ? { inputs: { ...pvals, ...(operation ? { operation } : {}) }, assets: byRole, textField: textField ?? undefined } : undefined };
    if (prof) pvals = {};
    if (turn.content) mine = [turn.content, ...mine.filter((x) => x !== turn.content)].slice(0, 50);
    text = ''; tray.clear(); attNote = ''; autosize();
    try { sessionStorage.removeItem(dkey); } catch { /* storage unavailable */ }
    onsend(turn);
  }
  /** What the current choices would send, for regenerate and edit (same operation and model as shown). */
  export function current(): Pick<Turn, 'modelId' | 'modelLabel' | 'operation'> & { media?: Record<string, unknown> } {
    return { modelId, modelLabel: model?.label ?? model?.alias ?? 'Assistant', operation: op, media: op !== 'text' ? mediaParams() : undefined };
  }
  export function modelFor(o: Operation) { return chosen[o] ?? catalog(o)?.default ?? ''; }
  // Typing is never blocked while the model list loads; a send pressed meanwhile goes as soon as a model is known.
  let pending = $state(false);
  // Enter sends by default (Shift+Enter for a new line); with "Enter sends" off, Enter is a new line and Ctrl/Cmd+Enter
  // sends. The arrow keys always move the caret; earlier messages come back only with Alt+Up/Down, and only when on.
  const enterSends = $derived(ui.composer.enter_sends !== false);
  const historyKeys = $derived(!!ui.composer.prompt_history);
  const sendKeys = $derived(enterSends ? 'Enter' : navigator.platform?.startsWith('Mac') ? '⌘ Enter' : 'Ctrl+Enter');
  let hist = -1, draft = '';
  // newest first: what was sent from here (a turn joins the thread's messages only once its reply is saved), then the thread's
  let mine = $state<string[]>([]);
  const sent = $derived([...new Set([...mine, ...thread.messages.filter((m) => m.role === 'user' && m.content?.trim()).map((m) => m.content).reverse()])]);
  function recall(dir: 1 | -1) {
    const next = hist + dir;
    if (next < -1 || next >= sent.length) return;
    if (hist === -1) draft = text;
    hist = next; text = hist === -1 ? draft : sent[hist]; autosize();
    tick().then(() => box?.setSelectionRange(text.length, text.length));
  }
  function key(e: KeyboardEvent) {
    if (e.isComposing) return;
    if (historyKeys && e.altKey && (e.key === 'ArrowUp' || e.key === 'ArrowDown')) { e.preventDefault(); recall(e.key === 'ArrowUp' ? 1 : -1); return; }
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === 'Enter' && !e.shiftKey && !e.altKey && (enterSends || mod)) {
      e.preventDefault(); hist = -1;
      if (!cat && text.trim()) pending = true; else send();
    }
  }
  $effect(() => { if (pending && cat) untrack(() => { pending = false; send(); }); });
  const canEph = $derived(!!ui.composer.ephemeral);
  const fresh = $derived(!thread.id && !thread.messages.length && !busy);
  const placeholder = $derived(!cat ? 'Message' : cat.error ? cat.error : !cat.models.length ? 'No model is available to you for this'
    : prof ? (textSpec ? (textSpec.label ?? 'Describe what you want').replace(/\s*\([^)]*\)\s*$/, '') : 'Attach a video, then send')
    : presetOn ? 'Describe the photo' : op === 'image' ? (hasImage ? 'Describe the change to the picture' : 'Describe the picture') : op === 'video' ? (hasImage ? 'Describe the motion' : 'Describe the video')
    : workspace === 'code' ? 'Ask about code, or paste some' : 'Message');
  const retention = $derived(ui.retention?.show && ui.retention.place === 'composer' ? (thread.ephemeral ? 'This conversation is not saved.' : ui.retention.text) : '');
</script>

<div class="cwrap">
<MediaDropzone onfiles={addFiles} disabled={!canAttach && !canMedia} hint={prof ? 'Drop videos and reference pictures' : op === 'text' ? 'Drop pictures to attach them' : op === 'image' ? (presetOn ? 'Drop pictures' : takes > 1 ? 'Drop pictures to edit or use as references' : 'Drop a picture to edit') : 'Drop pictures or videos'}
  sub={prof ? 'They are added to the tray above your message' : `Up to ${maxMb} MB each`}>
<div class="composer" data-op={op}>
  <div class="box" data-focus-ring>
    {#if prof && opChoices.length}
      <div class="opsel" role="radiogroup" aria-label={`${prof.inputs_schema.operation.label ?? 'Operation'} (${prof.name ?? 'editing'})`}>
        {#each opChoices as o (o)}
          <button type="button" role="radio" aria-checked={operation === o} class="oc" class:on={operation === o} onclick={() => (pvals = { ...pvals, operation: o })} disabled={busy}>{opLabel(o)}</button>
        {/each}
      </div>
    {/if}
    {#if tray.items.length || (prof && emptySlots.length)}
      <div class="trayrow">
        <MediaAttachmentTray {tray} {slots} {verdict} {operation} onreplace={replace} onadd={prof && canMedia ? () => (pickerOpen = true) : undefined} empty={prof ? emptySlots : []} onslot={chooseFor}
          useLabel={prof ? 'Use as source' : 'Use edited version'} compact={!prof} {tags} onmoved={prof ? undefined : moved} />
      </div>
    {/if}
    {#if attNote || tray.notes.length || attProblem || verdict.notes.length}
      <div class="tnotes" role="status" aria-live="polite">
        {#if attNote}<span class="bad"><Icon name="warn" size={13} /> {attNote}</span>{/if}
        {#each tray.notes as n}<span class="muted"><Icon name="info" size={13} /> {n}</span>{/each}
        {#if tray.items.length && attProblem && (op !== 'text' || tray.busy || tray.failed)}<span class="warn"><Icon name="info" size={13} /> {attProblem}</span>{/if}
        {#each verdict.notes as n}<span class="muted"><Icon name="info" size={13} /> {n}</span>{/each}
      </div>
    {/if}
  {#if op === 'text' && hasImage && model && !sees}
    <p class="hint capnote" role="status"><Icon name="warn" size={14} /> <span>{alias} cannot read pictures. It receives only the file {usedPics > 1 ? 'names' : 'name'} of the attached {usedPics > 1 ? 'pictures' : 'picture'}. Choose a model that can see pictures.</span></p>
    {:else if presetOn && hasImage}
    <p class="hint capnote" role="status"><Icon name="info" size={14} /> <span>iPhone Photo makes a new picture from words, so attached pictures are not used. Switch it off to edit or reference them.</span></p>
    {:else if presetNoText}
    <p class="hint capnote" role="status"><Icon name="warn" size={14} /> <span>{alias} edits pictures and cannot make a new one from words. Choose another quality or model for iPhone Photo.</span></p>
    {:else if op !== 'text' && !prof && needSource && !hasImage}
    <p class="hint capnote" role="status"><Icon name="info" size={14} /> <span>{alias} {op === 'image' ? 'edits pictures: attach the picture to change.' : 'animates a picture: attach the starting picture.'}
      {#if canAttach || canMedia}<button type="button" class="linkish under" onclick={choose}>Add a picture</button>{/if}</span></p>
    {:else if !prof && cannotUseSource}
    <p class="hint capnote" role="status"><Icon name="warn" size={14} /> <span>{alias} {op === 'image' ? 'makes pictures from words only' : 'cannot animate a picture'}, so the attached {usedPics > 1 ? 'pictures are' : 'picture is'} not used. Choose a model that can {op === 'image' ? 'edit pictures' : 'animate'} or remove {usedPics > 1 ? 'them' : 'it'}.</span></p>
    {:else if op === 'image' && !prof && hasImage && takes > 1 && sentCount === 1}
    <p class="hint capnote soft" role="status"><Icon name="info" size={14} /> <span>{alias} edits the first picture. You can add up to {takes - 1} more as references and call them image 2, image 3 in your words.</span></p>
    {/if}
    <label class="sr-only" for="msg">Message</label>
    <textarea id="msg" rows="1" bind:this={box} bind:value={text} onkeydown={key} oninput={() => { hist = -1; autosize(); }} {placeholder}
      disabled={!!cat && (!!cat.error || !cat.models?.length)} aria-describedby="composer-help" aria-keyshortcuts={historyKeys ? 'Alt+ArrowUp Alt+ArrowDown' : undefined}></textarea>
    <div class="bar">
      {#if canAttach || canMedia}
        <AttachMenu onupload={choose} onlibrary={() => (pickerOpen = true)} withLibrary={canMedia} />
        <input type="file" multiple class="sr-only" tabindex="-1" aria-hidden="true" bind:this={fileInput} onchange={pick} accept={canMedia ? 'image/*,video/*,.txt,.md,.csv,.json,.pdf' : undefined} />
      {/if}
      {#if showOps}
        <div class="ops" role="radiogroup" aria-label="What to make">
          {#each ops as o (o.id)}
            <button type="button" role="radio" aria-checked={op === o.id} class="opb" class:on={op === o.id} disabled={busy} onclick={() => (op = o.id)} title={o.label}>
              <Icon name={o.icon} size={15} /><span class="ol">{o.label}</span>
            </button>
          {/each}
        </div>
      {/if}
      {#if op === 'image' && !prof && presetInfo}
        <button type="button" class="chipb" class:on={presetOn} aria-pressed={presetOn} disabled={busy} title={presetInfo.description} aria-label={`${presetInfo.label}, 9:16 phone photo look`}
          onclick={() => setPreset(!preset.on)}><Icon name="phone" size={15} /><span class="ol">{presetInfo.label}</span></button>
      {/if}
      {#if prof}
        <button type="button" class="round ghostr" class:on={profOpen} aria-expanded={profOpen} aria-controls="profile-settings" title="Settings for this operation" aria-label="Settings for this operation" onclick={() => (profOpen = !profOpen)}><Icon name="sliders" size={16} /></button>
      {:else if op !== 'text' && mopt}
        <button type="button" class="round ghostr" class:on={setOpen} aria-expanded={setOpen} aria-controls="media-settings" title="Picture and video settings" aria-label="Settings" onclick={() => (setOpen = !setOpen)}><Icon name="sliders" size={16} /></button>
      {/if}
      <span class="spacer"></span>
      {#if flags.model_chooser !== false}
        <ModelChooser catalog={cat} op={catOp} bind:value={chosen[catOp]} disabled={busy} label={op === 'text' ? 'Model' : op === 'image' ? 'Picture model' : 'Video model'} needVision={op === 'text' && hasImage} />
      {/if}
      {#if busy}
        <button class="round send stop" onclick={onstop} aria-label="Stop (Esc)" title="Stop (Esc)"><Icon name="stop" size={14} /></button>
      {:else}
        {@const verb = op === 'text' ? 'Send' : op === 'image' ? (presetOn ? 'Make iPhone Photo' : sentCount ? 'Edit picture' : 'Make picture') : sentCount && !prof ? 'Animate picture' : 'Make video'}
        <button class="round send" class:wide={op !== 'text' && !prof} onclick={send} disabled={!canSend} aria-label={`${verb} (${sendKeys})`} title={`${verb} (${sendKeys})`}>
          <Icon name="arrowup" size={18} />{#if op !== 'text' && !prof}<span class="sl">{presetOn ? 'Make photo' : verb}</span>{/if}
        </button>
      {/if}
    </div>
  </div>

  {#if presetOn && presetInfo}
    <div class="presetline" role="status">
      <span class="sum"><Icon name="phone" size={13} /> <strong>{presetInfo.label}</strong> · 9:16 · {preset.size.replace('x', ' × ')}{tier ? ` · ${tier.label}` : ''} · {alias}</span>
      <span class="acts"><button type="button" class="linkish under" onclick={() => (setOpen = !setOpen)} aria-expanded={setOpen} aria-controls="media-settings">{setOpen ? 'Hide options' : 'Options'}</button>
        <button type="button" class="linkish under" onclick={() => setPreset(false)}>Turn off</button></span>
    </div>
  {/if}
  {#if prof}
    <div id="profile-settings" hidden={!profOpen}><ProfileForm profile={prof} {operation} exclude={textField} bind:values={pvals} bind:problem={pproblem} /></div>
    {#if !profOpen && pproblem && prof.ready}<p class="tiny err" role="status">{pproblem} <button type="button" class="linkish" onclick={() => (profOpen = true)}>Open settings</button></p>{/if}
    {#if !prof.ready}<p class="tiny err" role="status">This can’t run yet: {prof.reason ?? 'the server has not finished setting it up.'}</p>{/if}
  {/if}
  {#if setOpen && op !== 'text' && mopt && !prof}
    <div class="settings" id="media-settings">
      {#if presetOn && presetInfo}
        <PresetOptions info={presetInfo} modelId={modelId} sizes={sizeChoices} bind:size={preset.size} bind:scenes={preset.scenes} onpick={(id) => (chosen.image = id)} disabled={busy} />
      {:else if sizes.length}
        <label class="fld"><span>Size</span><select class="input" bind:value={mp.size}>{#each sizes as s}<option value={s.v}>{s.label}</option>{/each}</select></label>
      {/if}
      {#if op === 'video'}
        <label class="fld"><span>Frames</span><input class="input" type="number" min={rng('frames')[0] ?? 1} max={rng('frames')[1]} step={mopt.limits?.frame_step ?? 4} bind:value={mp.frames} /></label>
        <label class="fld"><span>Frames per second</span><input class="input" type="number" min={rng('fps')[0] ?? 1} max={rng('fps')[1] ?? 60} bind:value={mp.fps} /></label>
        <span class="tiny" class:err={vmax && seconds > vmax} style="align-self:end">{seconds ? `${Math.round(seconds * 10) / 10} s` : ''}{vmax ? ` (at most ${vmax} s)` : ''}</span>
      {/if}
      {#if ui.composer.edit_params && !presetOn}
        <label class="fld"><span>Steps</span><input class="input" type="number" min={rng('steps')[0] ?? 1} max={rng('steps')[1]} bind:value={mp.steps} /></label>
      {/if}
      <label class="fld"><span>Seed</span><input class="input" inputmode="numeric" placeholder="Random" bind:value={mp.seed} /></label>
      {#if mopt.negative_prompt && !presetOn}<label class="fld wide"><span>Leave out</span><input class="input" placeholder="Things the result should not contain" bind:value={mp.negative} /></label>{/if}
      {#if mediaErr}<span class="err">{mediaErr}</span>{/if}
    </div>
  {/if}

  <div class="foot" id="composer-help">
    {#if op === 'text'}<ContextBar bind:assistantId bind:collectionIds disabled={busy} onchange={onctx} />{/if}
    {#if model?.external}<span class="chip warn">External service</span>{/if}
    {#if canEph && fresh && !tray.items.length}
      <label class="small row eph"><input type="checkbox" checked={thread.ephemeral} onchange={(e) => thread.reset(e.currentTarget.checked)} /> Don't save this conversation</label>
    {:else if thread.ephemeral}<span class="chip">Not saved</span>{/if}
    <span class="spacer"></span>
    {#if retention}<span class="tiny muted">{retention}</span>{/if}
    <button class="linkish tiny" onclick={onprotect}>How is this handled?</button>
    {#if text.length > ui.composer.max_chars * 0.8}<span class="tiny" class:err={tooLong}>{text.length.toLocaleString()} / {ui.composer.max_chars.toLocaleString()}</span>{/if}
  </div>
</div>
</MediaDropzone>

<MediaAssetPicker bind:open={pickerOpen} accept={prof ? 'any' : op === 'text' ? 'image' : 'image'} onpick={(list) => { for (const a of list) addAsset(a); }} />
</div>

<style>
  .cwrap { width: 100%; min-width: 0; }
  .composer { width: 100%; min-width: 0; display: grid; gap: 6px; }
  .box { min-width: 0; grid-template-columns: minmax(0, 1fr); background: var(--composer-bg); border: 1px solid var(--border); border-radius: var(--radius-composer); padding: 10px 10px 8px 14px; display: grid; gap: 6px;
    box-shadow: var(--shadow-sm); transition: border-color var(--fast) var(--ease); }
  /* the field has no ring of its own; the whole box carries a 2px ring (border plus spread) while it has focus */
  .box:focus-within { border-color: var(--border-strong); box-shadow: var(--shadow-sm), 0 0 0 1px var(--border-strong); }
  textarea { all: unset; box-sizing: border-box; width: 100%; min-height: 44px; max-height: 240px; overflow-y: auto; padding: 4px 0; line-height: 1.5; color: var(--fg); white-space: pre-wrap; overflow-wrap: anywhere; }
  textarea::placeholder { color: var(--fg-muted); }
  textarea:disabled { cursor: not-allowed; }
  .bar { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; margin-left: -6px; }
  .round { all: unset; box-sizing: border-box; display: inline-grid; place-items: center; width: 32px; height: 32px; border-radius: 999px; cursor: pointer; flex: none;
    transition: background var(--fast) var(--ease), color var(--fast) var(--ease), opacity var(--fast) var(--ease); }
  .round:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .ghostr { color: var(--fg-muted); } .ghostr:hover, .ghostr.on { background: var(--hover); color: var(--fg); }
  .send { background: var(--btn-primary-bg); color: var(--btn-primary-fg); }
  .send:hover:not(:disabled) { opacity: 0.88; }
  .send:disabled { background: var(--selected); color: var(--fg-muted); cursor: default; }
  .ops { display: inline-flex; border-radius: 999px; padding: 2px; gap: 2px; background: var(--bg-2); }
  .opb { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 10px; border-radius: 999px; font-size: 13px; color: var(--fg-muted); cursor: pointer; }
  .opb:hover:not(:disabled) { color: var(--fg); background: var(--hover); }
  .opb.on { background: var(--composer-bg); color: var(--fg); font-weight: 500; box-shadow: var(--shadow-sm); }
  .opb:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .opb:disabled { cursor: default; }
  .opsel { display: flex; gap: 2px; padding: 2px; margin: 0 0 2px; border-radius: 999px; background: var(--bg-2); width: max-content; max-width: 100%; overflow-x: auto; }
  .oc { all: unset; box-sizing: border-box; height: 26px; padding: 0 11px; border-radius: 999px; cursor: pointer; font-size: 12.5px; color: var(--fg-muted); white-space: nowrap;
    transition: background var(--fast) var(--ease), color var(--fast) var(--ease); }
  .oc:hover:not(:disabled) { color: var(--fg); background: var(--hover); } .oc.on { background: var(--composer-bg); color: var(--fg); font-weight: 600; box-shadow: var(--shadow-sm); }
  .oc:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 1px; } .oc:disabled { cursor: default; }
  .trayrow { min-width: 0; margin: 2px -4px 0 -4px; padding: 0 4px; border-bottom: 1px solid var(--border); padding-bottom: 2px; }
  .tnotes { display: grid; gap: 2px; font-size: 12px; } .tnotes span { display: flex; gap: 6px; align-items: flex-start; } .tnotes .bad { color: var(--danger); } .tnotes .warn { color: var(--warn); } .tnotes .muted { color: var(--fg-muted); }
  .tnotes :global(svg) { flex: none; margin-top: 2px; }
  .capnote { display: flex; gap: 6px; align-items: flex-start; margin: 0; font-size: 12px; color: var(--fg-muted); } .capnote :global(svg) { flex: none; margin-top: 2px; }
  .capnote span { min-width: 0; } .capnote:not(.soft) :global(svg) { color: var(--warn); }
  .under { text-decoration: underline; }
  .presetline { display: flex; align-items: center; justify-content: space-between; gap: 4px 12px; flex-wrap: wrap; margin: 0; padding: 0 4px; font-size: 12.5px; color: var(--fg-muted); }
  .presetline strong { color: var(--fg); font-weight: 600; } .presetline .sum { display: block; min-width: 0; } .presetline .sum :global(svg) { vertical-align: -2px; margin-right: 3px; } .presetline .acts { display: inline-flex; gap: 12px; }
  .chipb { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 10px; border-radius: 999px; font-size: 13px; color: var(--fg-muted); cursor: pointer; border: 1px solid var(--border); flex: none; }
  .chipb:hover:not(:disabled) { color: var(--fg); background: var(--hover); } .chipb.on { color: var(--fg); background: var(--m-ring-soft); border-color: var(--m-ring); }
  .chipb:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; } .chipb:disabled { cursor: default; opacity: 0.6; }
  .send.wide { width: auto; padding: 0 12px 0 9px; gap: 6px; display: inline-flex; align-items: center; } .sl { font-size: 13px; font-weight: 500; white-space: nowrap; }
  .settings { display: flex; flex-wrap: wrap; gap: 8px 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); }
  .fld { display: grid; gap: 2px; font-size: 12px; color: var(--fg-muted); width: 130px; }
  .fld.wide { width: min(100%, 320px); }
  .fld .input { min-height: 32px; padding: 4px 8px; font-size: 13px; color: var(--fg); }
  .foot { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 24px; padding: 0 4px; }
  .eph { gap: 6px; color: var(--fg-muted); font-size: 13px; }
  .linkish { all: unset; cursor: pointer; color: var(--fg-muted); text-underline-offset: 2px; }
  .linkish:hover { color: var(--fg); text-decoration: underline; } .linkish:focus-visible { outline: 2px solid var(--accent); }
  @media (max-width: 719px) {
    .ol, .sl { display: none; } .opb { padding: 0 7px; } .chipb { padding: 0 7px; } .send.wide { width: 32px; padding: 0; display: inline-grid; }
    .bar { flex-wrap: nowrap; } .bar .spacer { display: none; } .composer .bar :global(.mc) { flex: 1 1 0; min-width: 0; display: flex; justify-content: flex-end; } .composer .bar :global(.mc .trigger) { max-width: 100%; min-width: 0; }
    .opsel { flex-wrap: wrap; border-radius: 16px; }
    .foot :global(.ctx) { width: 100%; }
  }
  @media (pointer: coarse) and (max-width: 719px) { .send.wide { width: 40px; } }
  @media (pointer: coarse) {
    textarea { min-height: 40px; padding: 8px 0; }
    .round { width: 40px; height: 40px; } .opb, .chipb { height: 40px; }
    .linkish { display: inline-flex; align-items: center; min-height: 40px; }
  }
</style>
