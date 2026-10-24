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
  $effect(() => { const o = catOp; untrack(() => loadCatalog(o)); });
  // fall back to the administrator's default (or the first allowed model) whenever the current choice is not offered
  $effect(() => {
    const c = cat; if (!c || c.error) return;
    const cur = untrack(() => chosen[catOp]);
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
  type Pending = Attachment & { uploading?: boolean; error?: string; local?: string };
  let atts = $state<Pending[]>([]), attNote = $state('');
  const canAttach = $derived(!!ui.composer.attachments);
  const maxMb = $derived<number>(ui.composer.attachment_max_mb ?? 10);
  const ready = $derived(atts.filter((a) => !a.uploading && !a.error));
  const hasImage = $derived(ready.some((a) => a.kind === 'image'));
  const uploading = $derived(atts.some((a) => a.uploading));

  export async function addFiles(list: FileList | File[]) {
    attNote = '';
    if (!canAttach) { attNote = 'Attachments are turned off for you.'; return; }
    for (const f of [...list]) {
      if (atts.length >= 10) { attNote = 'At most 10 files per message.'; break; }
      if (f.size > maxMb * 1048576) { attNote = `${f.name} is larger than ${maxMb} MB.`; continue; }
      const local = f.type.startsWith('image/') ? URL.createObjectURL(f) : undefined;
      const row: Pending = { id: `up_${Math.random().toString(36).slice(2)}`, name: f.name, kind: f.type.startsWith('image/') ? 'image' : 'text', mime: f.type, size: f.size, uploading: true, local };
      atts.push(row);
      const r = atts[atts.length - 1];
      try { const a = await thread.attach(f); Object.assign(r, a, { uploading: false }); }
      catch (e) { r.uploading = false; r.error = errText(e); }
    }
  }
  /** A picture generated earlier becomes the source for an edit or an animation. */
  export function useSource(a: any) {
    if (atts.some((x) => x.id === a.id)) return;
    atts = [...atts.filter((x) => !x.id.startsWith('ma_')), { id: a.id, name: 'Generated picture', kind: 'image', mime: a.mime, size: a.size, width: a.width, height: a.height, generated: true }];
    if (op === 'text' && ops.some((o) => o.id === 'image')) op = 'image';
    focus();
  }
  async function removeAtt(i: number) {
    const a = atts[i];
    atts.splice(i, 1);
    if (a.local) URL.revokeObjectURL(a.local);
    if (!a.uploading && !a.error && !a.id.startsWith('ma_')) { try { await del(`/api/chat/attachments/${a.id}`); } catch { /* removed after a day anyway */ } }
  }
  function pick(e: Event) { const inp = e.currentTarget as HTMLInputElement; if (inp.files) addFiles(inp.files); inp.value = ''; }
  function paste(e: ClipboardEvent) {
    const files = [...(e.clipboardData?.files ?? [])];
    if (files.length && canAttach) { e.preventDefault(); addFiles(files); }
  }
  const thumb = (a: Pending) => a.local ?? (a.id.startsWith('ma_') ? `/api/media/assets/${a.id}/content` : `/api/chat/attachments/${a.id}/content`);

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
    if (mp.size) { const [w, h] = mp.size.split('x').map(Number); out.width = w; out.height = h; }
    if (String(mp.seed).trim() !== '' && Number.isInteger(Number(mp.seed))) out.seed = Number(mp.seed);
    if (mp.negative.trim() && mopt?.negative_prompt) out.negative_prompt = mp.negative.trim();
    if (mp.steps && ui.composer.edit_params) out.steps = Number(mp.steps);
    if (op === 'video' && mp.frames && mp.fps) { out.frames = mp.frames; out.fps = mp.fps; }
    return out;
  }
  const needSource = $derived(op !== 'text' && mopt && mopt.modes?.length && !mopt.modes.some((x: string) => (op === 'image' ? x === 'txt2img' : x === 't2v' || x === 'ti2v')));
  const cannotUseSource = $derived(op !== 'text' && hasImage && mopt && mopt.modes?.length && !mopt.modes.some((x: string) => (op === 'image' ? x === 'edit' || x === 'img2img' : x === 'i2v' || x === 'ti2v')));

  // ---- sending -----------------------------------------------------------------------------------------------
  const busy = $derived(thread.busy);
  const tooLong = $derived(text.length > ui.composer.max_chars);
  const blocked = $derived(!modelId || uploading || tooLong || (op !== 'text' && (!text.trim() || needSource && !hasImage || cannotUseSource || (vmax && seconds > vmax + 1e-9))));
  const canSend = $derived(!busy && !blocked && (!!text.trim() || (op === 'text' && ready.length > 0)));
  function send() {
    if (!canSend) return;
    const turn: Turn = { content: text.trim(), attachments: ready.map(({ uploading: _u, error: _e, local: _l, ...a }) => a), operation: op, modelId,
      modelLabel: model?.alias ?? 'Assistant', media: op !== 'text' ? mediaParams() : undefined,
      sourceId: op !== 'text' ? ready.find((a) => a.kind === 'image')?.id ?? null : null };
    for (const a of atts) if (a.local) URL.revokeObjectURL(a.local);
    text = ''; atts = []; attNote = ''; autosize();
    onsend(turn);
  }
  /** What the current choices would send, for regenerate and edit (same operation and model as shown). */
  export function current(): Pick<Turn, 'modelId' | 'modelLabel' | 'operation'> & { media?: Record<string, unknown> } {
    return { modelId, modelLabel: model?.alias ?? 'Assistant', operation: op, media: op !== 'text' ? mediaParams() : undefined };
  }
  export function modelFor(o: Operation) { return chosen[o] ?? catalog(o)?.default ?? ''; }
  // Typing is never blocked while the model list loads; Enter pressed meanwhile sends as soon as a model is known.
  let pending = $state(false);
  function key(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!cat && text.trim()) pending = true; else send(); }
  }
  $effect(() => { if (pending && cat) untrack(() => { pending = false; send(); }); });
  const canEph = $derived(!!ui.composer.ephemeral);
  const fresh = $derived(!thread.id && !thread.messages.length && !busy);
  const placeholder = $derived(!cat ? 'Message' : cat.error ? cat.error : !cat.models.length ? 'No model is available to you for this'
    : op === 'image' ? (hasImage ? 'Describe the change to the picture' : 'Describe the picture') : op === 'video' ? (hasImage ? 'Describe the motion' : 'Describe the video')
    : workspace === 'code' ? 'Ask about code, or paste some' : 'Message');
  const retention = $derived(ui.retention?.show && ui.retention.place === 'composer' ? (thread.ephemeral ? 'This conversation is not saved.' : ui.retention.text) : '');
</script>

<div class="composer" data-op={op}>
  {#if atts.length || attNote}
    <div class="atts">
      {#each atts as a, i (a.id)}
        <div class="att" class:bad={a.error}>
          {#if a.kind === 'image'}<img src={thumb(a)} alt="" />{:else}<Icon name="log" size={16} />{/if}
          <span class="an">{a.error ? `${a.name}: ${a.error}` : a.name}</span>
          {#if a.uploading}<span class="tiny muted" role="status">Uploading…</span>{/if}
          <button class="btn ghost icon sm" aria-label={`Remove ${a.name}`} onclick={() => removeAtt(i)}><Icon name="x" size={14} /></button>
        </div>
      {/each}
      {#if attNote}<span class="err" role="status">{attNote}</span>{/if}
    </div>
  {/if}

  {#if op === 'text' && hasImage && model && !(model.capabilities ?? []).includes('vision')}
    <p class="hint warnline" role="status"><Icon name="warn" size={14} /> {model.alias} cannot read images. It will receive only the file names. Choose a model that can see pictures.</p>
  {:else if op !== 'text' && needSource && !hasImage}
    <p class="hint warnline" role="status"><Icon name="info" size={14} /> {model?.alias} {op === 'image' ? 'edits pictures: attach the picture to change.' : 'animates a picture: attach the starting picture.'}</p>
  {:else if cannotUseSource}
    <p class="hint warnline" role="status"><Icon name="warn" size={14} /> {model?.alias} {op === 'image' ? 'cannot edit pictures' : 'cannot animate a picture'}. Remove the picture or choose another model.</p>
  {/if}

  <div class="box" data-focus-ring>
    <label class="sr-only" for="msg">Message</label>
    <textarea id="msg" rows="1" bind:this={box} bind:value={text} onkeydown={key} oninput={autosize} onpaste={paste} {placeholder}
      disabled={!!cat && (!!cat.error || !cat.models?.length)} aria-describedby="composer-help"></textarea>
    <div class="bar">
      {#if canAttach}
        <label class="btn ghost icon sm" title="Attach files or pictures" aria-label="Attach files or pictures">
          <Icon name="attach" size={17} /><input type="file" multiple class="sr-only" onchange={pick} accept={op === 'text' ? undefined : 'image/*'} />
        </label>
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
      {#if flags.model_chooser !== false}
        <ModelChooser catalog={cat} bind:value={chosen[catOp]} disabled={busy} label={op === 'text' ? 'Model' : op === 'image' ? 'Picture model' : 'Video model'} needVision={op === 'text' && hasImage} />
      {/if}
      {#if op !== 'text' && mopt}
        <button type="button" class="btn ghost icon sm" class:on={setOpen} aria-expanded={setOpen} aria-controls="media-settings" title="Picture and video settings" aria-label="Settings" onclick={() => (setOpen = !setOpen)}><Icon name="sliders" size={16} /></button>
      {/if}
      <span class="spacer"></span>
      {#if busy}
        <button class="btn danger solid send" onclick={onstop} aria-label="Stop (Esc)" title="Stop (Esc)"><Icon name="stop" size={15} /><span class="sl">Stop</span></button>
      {:else}
        <button class="btn primary send" onclick={send} disabled={!canSend} aria-label={op === 'text' ? 'Send (Enter)' : op === 'image' ? 'Make picture (Enter)' : 'Make video (Enter)'}>
          <Icon name={op === 'text' ? 'send' : op} size={15} /><span class="sl">{op === 'text' ? 'Send' : 'Make'}</span>
        </button>
      {/if}
    </div>
  </div>

  {#if setOpen && op !== 'text' && mopt}
    <div class="settings" id="media-settings">
      {#if sizes.length}
        <label class="fld"><span>Size</span><select class="input" bind:value={mp.size}>{#each sizes as s}<option value={s.v}>{s.label}</option>{/each}</select></label>
      {/if}
      {#if op === 'video'}
        <label class="fld"><span>Frames</span><input class="input" type="number" min={rng('frames')[0] ?? 1} max={rng('frames')[1]} step={mopt.limits?.frame_step ?? 4} bind:value={mp.frames} /></label>
        <label class="fld"><span>Frames per second</span><input class="input" type="number" min={rng('fps')[0] ?? 1} max={rng('fps')[1] ?? 60} bind:value={mp.fps} /></label>
        <span class="tiny" class:err={vmax && seconds > vmax} style="align-self:end">{seconds ? `${Math.round(seconds * 10) / 10} s` : ''}{vmax ? ` (at most ${vmax} s)` : ''}</span>
      {/if}
      {#if ui.composer.edit_params}
        <label class="fld"><span>Steps</span><input class="input" type="number" min={rng('steps')[0] ?? 1} max={rng('steps')[1]} bind:value={mp.steps} /></label>
      {/if}
      <label class="fld"><span>Seed</span><input class="input" inputmode="numeric" placeholder="Random" bind:value={mp.seed} /></label>
      {#if mopt.negative_prompt}<label class="fld wide"><span>Leave out</span><input class="input" placeholder="Things the result should not contain" bind:value={mp.negative} /></label>{/if}
      {#if mediaErr}<span class="err">{mediaErr}</span>{/if}
    </div>
  {/if}

  <div class="foot" id="composer-help">
    {#if op === 'text'}<ContextBar bind:assistantId bind:collectionIds disabled={busy} onchange={onctx} />{/if}
    {#if model?.external}<span class="chip warn">External service</span>{/if}
    {#if canEph && fresh && !atts.length}
      <label class="small row eph"><input type="checkbox" checked={thread.ephemeral} onchange={(e) => thread.reset(e.currentTarget.checked)} /> Don't save this conversation</label>
    {:else if thread.ephemeral}<span class="chip">Not saved</span>{/if}
    <span class="spacer"></span>
    {#if retention}<span class="tiny muted">{retention}</span>{/if}
    <button class="linkish tiny" onclick={onprotect}>How is this handled?</button>
    {#if text.length > ui.composer.max_chars * 0.8}<span class="tiny" class:err={tooLong}>{text.length.toLocaleString()} / {ui.composer.max_chars.toLocaleString()}</span>{/if}
  </div>
</div>

<style>
  .composer { width: 100%; display: grid; gap: 6px; }
  .box { background: var(--composer-bg); border: 1px solid var(--border-strong); border-radius: calc(var(--radius) + 4px); padding: 8px 8px 6px 12px; display: grid; gap: 4px;
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04); }
  /* the field has no ring of its own; the whole box carries a 2px accent ring while it has focus */
  .box:focus-within { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
  textarea { all: unset; box-sizing: border-box; width: 100%; min-height: 28px; max-height: 240px; overflow-y: auto; padding: 4px 0; line-height: 1.5; color: var(--fg); white-space: pre-wrap; overflow-wrap: anywhere; }
  textarea::placeholder { color: var(--fg-muted); }
  textarea:disabled { cursor: not-allowed; }
  .bar { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; margin-left: -6px; }
  .ops { display: inline-flex; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 2px; gap: 2px; }
  .opb { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 5px; height: 26px; padding: 0 8px; border-radius: calc(var(--radius-sm) - 2px); font-size: 13px; color: var(--fg-muted); cursor: pointer; }
  .opb:hover:not(:disabled) { color: var(--fg); background: var(--hover); }
  .opb.on { background: var(--selected); color: var(--fg); font-weight: 600; }
  .opb:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .opb:disabled { cursor: default; }
  .btn.on { background: var(--selected); }
  .send { min-height: 32px; padding: 0 12px; border-radius: var(--radius); }
  .atts { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .att { display: inline-flex; align-items: center; gap: 6px; padding: 3px 3px 3px 6px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--card-bg); font-size: 13px; max-width: 260px; }
  .att.bad { border-color: var(--danger); color: var(--danger); }
  .att img { width: 32px; height: 32px; object-fit: cover; border-radius: 4px; }
  .an { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .warnline { display: flex; gap: 6px; align-items: center; margin: 0; }
  .settings { display: flex; flex-wrap: wrap; gap: 8px 12px; padding: 10px 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); }
  .fld { display: grid; gap: 2px; font-size: 12px; color: var(--fg-muted); width: 130px; }
  .fld.wide { width: min(100%, 320px); }
  .fld .input { min-height: 32px; padding: 4px 8px; font-size: 13px; color: var(--fg); }
  .foot { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 24px; padding: 0 4px; }
  .eph { gap: 6px; }
  .linkish { all: unset; cursor: pointer; color: var(--fg-muted); text-decoration: underline; text-underline-offset: 2px; }
  .linkish:hover { color: var(--fg); } .linkish:focus-visible { outline: 2px solid var(--accent); }
  @media (max-width: 719px) {
    .ol { display: none; } .opb { padding: 0 7px; }
    .sl { display: none; } .send { width: 36px; padding: 0; }
    .foot :global(.ctx) { width: 100%; }
  }
  @media (pointer: coarse) {
    textarea { min-height: 40px; padding: 8px 0; }
    .send { min-height: 40px; } .opb { height: 40px; }
    .linkish { display: inline-flex; align-items: center; min-height: 40px; }
  }
  @media (pointer: coarse) and (max-width: 719px) { .send { width: 40px; } }
</style>
