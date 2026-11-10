<script lang="ts">
  // One Experimental group (Audio, Picture tools, 3D or Video tools): the tools the server offers this person, a form built from the engine's own input
  // schema, and the result. Nothing here grants access: a tool appears only when the server lists it, and the server checks every request again.
  import { onMount } from 'svelte';
  import { app } from '$lib/session.svelte';
  import { get } from '$lib/api';
  import { errText } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import ExpBack from '$lib/components/ExpBack.svelte';
  import MediaAssetPicker from './MediaAssetPicker.svelte';
  import MediaThumb from './MediaThumb.svelte';
  import ToolResult from './ToolResult.svelte';
  import { Upload, precheck } from '$lib/media/upload.svelte';
  import { fmtSize, type Asset } from '$lib/media/assets';
  import { GROUPS, fmtSeconds, initialValues, loadTools, missing, startTool, type Group, type Listing, type Spec, type Tool } from '$lib/media/extras';

  let { group }: { group: Group } = $props();
  const g = $derived(GROUPS[group]);
  const ui = $derived(app.me.ui);
  let listing = $state<Listing | null>(null), loading = $state(true), loadErr = $state('');
  let toolId = $state(''), values = $state<Record<string, any>>({}), files = $state<Record<string, Asset | null>>({}), ups = $state<Record<string, Upload | null>>({});
  let note = $state(''), save = $state(true), starting = $state(false), err = $state(''), job = $state<any>(null), recent = $state<any[]>([]);
  let pickFor = $state<{ name: string; kind: 'image' | 'video' } | null>(null), pickerOpen = $state(false);
  const tool = $derived<Tool | null>(listing?.tools.find((t) => t.id === toolId) ?? null);
  const fileSpecs = $derived(tool ? Object.entries(tool.inputs_schema).filter(([, s]) => s.type === 'file') : []);
  const plainSpecs = $derived(tool ? Object.entries(tool.inputs_schema).filter(([, s]) => s.type !== 'file' && s.type !== 'json') : []);
  const uploading = $derived(Object.values(ups).some((u) => u?.active));
  const running = $derived(starting || (!!job && (job.status === 'queued' || job.status === 'running')));
  const need = $derived(tool ? missing(tool.inputs_schema, values, Object.fromEntries(Object.entries(files).map(([k, a]) => [k, a?.id ?? null]))) : '');
  const maxMb = $derived<number>(Math.min(listing?.caps?.upload_max_mb ?? 512, ui.composer.media_upload_max_mb ?? 512));
  const allowed = $derived(group === 'video' ? !!ui.operations?.video?.enabled : !!ui.operations?.extras?.enabled);

  onMount(async () => {
    if (!allowed) { loading = false; return; }
    try {
      listing = await loadTools(group);
      if (listing.tools.length) choose(listing.tools[0].id);
      if (group !== 'video') loadRecent();
    } catch (e) { loadErr = errText(e); }
    loading = false;
  });
  async function loadRecent() {
    try { const ids = new Set(listing?.tools.map((t) => t.id)); recent = ((await get('/api/media/extras/jobs')) as any[]).filter((j) => ids.has(j.model_id)).slice(0, 8); } catch { recent = []; }
  }
  function choose(id: string) {
    toolId = id; const t = listing?.tools.find((x) => x.id === id); if (!t) return;
    values = initialValues(t.inputs_schema); err = ''; note = '';
    for (const u of Object.values(ups)) u?.cancel();
    ups = {}; files = Object.fromEntries(Object.entries(t.inputs_schema).filter(([, s]) => s.type === 'file').map(([k]) => [k, null]));
  }
  function pickFile(name: string, s: Spec, e: Event) {
    const i = e.currentTarget as HTMLInputElement, f = i.files?.[0]; i.value = ''; if (!f) return;
    note = '';
    const kind = s.kind ?? 'image';
    if (!f.type.startsWith(kind + '/')) { note = `${f.name} is not a ${kind === 'image' ? 'picture' : 'video'}.`; return; }
    const why = precheck(f, maxMb); if (why) { note = why; return; }
    ups[name]?.cancel();
    const u = new Upload(f, false); ups = { ...ups, [name]: u }; files = { ...files, [name]: null };
    u.start().then((a) => { if (ups[name] === u) { if (a) files = { ...files, [name]: a }; else if (u.status === 'error') note = u.error; } });
  }
  function clearFile(name: string) { ups[name]?.cancel(); ups = { ...ups, [name]: null }; files = { ...files, [name]: null }; }
  async function go() {
    if (!tool || running || uploading || need) return;
    err = ''; starting = true;
    try {
      job = await startTool(tool, values, Object.fromEntries(Object.entries(files).map(([k, a]) => [k, a?.id ?? null])), save || !listing?.ephemeral);
      if (job.status !== 'queued' && job.status !== 'running') changed(job);
    } catch (e) { err = errText(e); } finally { starting = false; }
  }
  function changed(j: any) { job = j; if (j.status !== 'queued' && j.status !== 'running' && group !== 'video') loadRecent(); }
  const optLabel = (s: Spec, v: any) => s.labels?.[v] ?? String(v);
  /** 'optional' only where leaving it out is a real choice (a text or a file); a setting with a default always has a value. */
  const optional = (s: Spec) => !s.required && (s.type === 'string' || s.type === 'file');
  const toolName = (mid: string, fallback: string) => listing?.tools.find((t) => t.id === mid)?.alias ?? fallback;
  const when = (t?: number) => (t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '');
</script>

<svelte:head><title>{g.title} · Experimental</title></svelte:head>
<div class="wrap" data-group={group}>
  <div class="head"><ExpBack /><h1>{g.title}</h1></div>
  <p class="muted lead">{g.lead}</p>

  {#if loading}<p class="muted" role="status">Checking what this server offers…</p>
  {:else if !allowed}<div class="notice warn" role="status"><div><strong>Not available to you.</strong> {group === 'video' ? ui.operations?.video?.reason : ui.operations?.extras?.reason || 'No Experimental tool is enabled for you.'}</div></div>
  {:else if loadErr}<div class="notice bad" role="alert"><div>{loadErr}</div></div>
  {:else if !listing?.tools.length}
    <div class="notice info" role="status" data-testid="extras-empty"><div><strong>No {g.title.toLowerCase()} tool is enabled for you.</strong> An administrator turns these on per organisation (Administration, Models).</div></div>
  {:else}
    {#if listing.tools.length > 1}
      <div class="tools" role="radiogroup" aria-label="Tool">
        {#each listing.tools as t (t.id)}
          <button type="button" role="radio" aria-checked={toolId === t.id} class="tcard" class:on={toolId === t.id} onclick={() => choose(t.id)} disabled={running}>
            <strong>{t.alias}</strong>{#if t.description}<span class="small muted">{t.description}</span>{/if}
          </button>
        {/each}
      </div>
    {/if}
    {#if tool}
      <section class="form" aria-label={tool.alias}>
        {#if listing.tools.length === 1}<div class="one"><strong>{tool.alias}</strong>{#if tool.description}<span class="small muted">{tool.description}</span>{/if}</div>{/if}
        {#if !tool.ready}<div class="notice warn" role="status"><div><strong>Set up but cannot run yet.</strong> {tool.reason ?? 'The server did not say why.'}</div></div>{/if}
        {#if tool.caution}<p class="tiny muted">{tool.caution}</p>{/if}

        {#each fileSpecs as [name, s] (name)}
          {@const a = files[name]}{@const u = ups[name]}
          <div class="field">
            <span class="fl">{s.label ?? name}{optional(s) ? ' (optional)' : ''}</span>
            {#if a || u}
              <div class="fcard">
                <span class="ft">{#if a}<MediaThumb id={a.id} kind={a.kind} name="" ratio="1 / 1" />{:else if u?.local}{#if (s.kind ?? 'image') === 'image'}<img src={u.local} alt="" />{:else}<!-- svelte-ignore a11y_media_has_caption --><video src={u.local} muted preload="metadata"></video>{/if}{/if}</span>
                <span class="fn"><strong>{a?.name ?? u?.file.name ?? 'File'}</strong>
                  {#if u?.active}<span class="tiny muted" role="status">{u.status === 'processing' ? 'Processing…' : `Uploading ${Math.round(u.fraction * 100)}%`}</span>
                  {:else if a}<span class="tiny muted">{[a.width && a.height ? `${a.width}×${a.height}` : '', fmtSize(a.size)].filter(Boolean).join(' · ')}</span>{/if}</span>
                <button type="button" class="btn ghost sm" onclick={() => clearFile(name)} disabled={running}><Icon name="x" size={14} /> Remove</button>
              </div>
            {:else}
              <div class="adds">
                <label class="btn" class:disabled={!ui.composer.media_library}><Icon name="upload" size={15} /> Upload {(s.kind ?? 'image') === 'image' ? 'a picture' : 'a video'}
                  <input type="file" accept={`${s.kind ?? 'image'}/*`} class="sr-only" onchange={(e) => pickFile(name, s, e)} disabled={!ui.composer.media_library} /></label>
                <button type="button" class="btn" onclick={() => { pickFor = { name, kind: s.kind ?? 'image' }; pickerOpen = true; }} disabled={!ui.composer.media_library}><Icon name="library" size={15} /> From Library</button>
              </div>
            {/if}
            {#if s.description}<span class="tiny muted">{s.description}</span>{/if}
          </div>
        {/each}
        {#if note}<p class="err small" role="alert">{note}</p>{/if}

        {#each plainSpecs as [name, s] (name)}
          <div class="field">
            {#if s.type === 'bool'}
              <label class="chk"><input type="checkbox" bind:checked={values[name]} /> {s.label ?? name}</label>
            {:else}
              <label class="fl" for={`x-${name}`}>{s.label ?? name}{optional(s) ? ' (optional)' : ''}</label>
              {#if s.type === 'enum'}
                <select class="input" id={`x-${name}`} bind:value={values[name]}>{#each s.values ?? [] as v (v)}<option value={v}>{optLabel(s, v)}</option>{/each}</select>
              {:else if s.type === 'int' || s.type === 'float'}
                <div class="num" class:solo={!(s.min != null && s.max != null && s.max - s.min <= 1000)}>
                  {#if s.min != null && s.max != null && s.max - s.min <= 1000}<input type="range" min={s.min} max={s.max} step={s.step ?? (s.type === 'int' ? 1 : 0.1)} bind:value={values[name]} aria-label={s.label ?? name} />{/if}
                  <input class="input" id={`x-${name}`} type="number" min={s.min} max={s.max} step={s.step ?? (s.type === 'int' ? 1 : 0.1)} bind:value={values[name]} />
                </div>
              {:else if s.multiline || (s.max_length ?? 0) > 300}
                <textarea class="input" id={`x-${name}`} rows={name === 'text' || name === 'lyrics' ? 5 : 3} maxlength={s.max_length} bind:value={values[name]}></textarea>
              {:else}
                <input class="input" id={`x-${name}`} type="text" maxlength={s.max_length} bind:value={values[name]} />
              {/if}
            {/if}
            {#if s.description}<span class="tiny muted">{s.description}</span>{/if}
          </div>
        {/each}

        <div class="go">
          <button type="button" class="btn primary" disabled={!tool.ready || running || uploading || !!need} onclick={go} data-testid="extras-go">{running ? 'Working…' : 'Start'}</button>
          {#if listing.ephemeral}<label class="chk small"><input type="checkbox" bind:checked={save} /> Save to Library</label>{/if}
          <span class="tiny muted" role="status">{need || (uploading ? 'Wait for the upload to finish.' : tool.typical_seconds ? `About ${fmtSeconds(tool.typical_seconds)} when the server is free.` : '')}</span>
        </div>
        {#if err}<div class="notice bad" role="alert"><div>{err}</div></div>{/if}
      </section>
    {/if}

    {#if job}<section class="res" aria-label="Result">{#key job.id}<ToolResult {job} outputs={tool?.id === job.model_id ? tool?.outputs : []} typical={tool?.typical_seconds} onchange={changed} />{/key}</section>{/if}

    {#if recent.filter((j) => j.id !== job?.id).length}
      <details class="recent"><summary>Earlier results</summary>
        {#each recent.filter((j) => j.id !== job?.id) as j (j.id)}
          <div class="ritem"><p class="tiny muted">{[toolName(j.model_id, j.model), when(j.created_at), j.prompt ? `“${j.prompt.slice(0, 80)}${j.prompt.length > 80 ? '…' : ''}”` : ''].filter(Boolean).join(' · ')}</p>
            <ToolResult job={j} outputs={listing?.tools.find((t) => t.id === j.model_id)?.outputs ?? []} /></div>
        {/each}
      </details>
    {/if}
    <MediaAssetPicker bind:open={pickerOpen} accept={pickFor?.kind ?? 'image'} title={pickFor?.kind === 'video' ? 'Choose a video' : 'Choose a picture'}
      onpick={(a) => { if (pickFor) { ups[pickFor.name]?.cancel(); ups = { ...ups, [pickFor.name]: null }; files = { ...files, [pickFor.name]: a[0] }; } }} />
  {/if}
</div>

<style>
  .wrap { max-width: 900px; width: 100%; margin: 0 auto; padding: 16px 24px 48px; display: grid; gap: 14px; align-content: start; }
  .head { display: flex; align-items: center; } .lead { max-width: 66ch; margin: 0; }
  .tools { display: grid; gap: 8px; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); }
  .tcard { all: unset; box-sizing: border-box; display: grid; gap: 4px; align-content: start; padding: 12px 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); cursor: pointer; }
  .tcard:hover { border-color: var(--border-strong); } .tcard.on { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .tcard:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; } .tcard[disabled] { cursor: default; opacity: 0.7; }
  .tcard strong, .one strong { font-weight: 600; font-size: 15px; } .one { display: grid; gap: 2px; }
  .form { display: grid; gap: 14px; padding: 16px; border: 1px solid var(--border); border-radius: var(--radius-menu); background: var(--card-bg); }
  .field { display: grid; gap: 6px; } .fl { font-size: 13px; font-weight: 500; }
  .field input[type='text'], .field textarea, .field select { width: 100%; box-sizing: border-box; }
  .num { display: grid; grid-template-columns: minmax(0, 1fr) 96px; gap: 10px; align-items: center; } .num.solo { grid-template-columns: minmax(0, 200px); } .num input[type='number'] { width: 100%; box-sizing: border-box; }
  .num input[type='range'] { width: 100%; accent-color: var(--accent); }
  .chk { display: inline-flex; gap: 6px; align-items: center; }
  .adds { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; } label.btn.disabled { opacity: 0.5; pointer-events: none; }
  .fcard { display: grid; grid-template-columns: 72px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--bg); }
  .ft { display: block; width: 72px; height: 72px; border-radius: var(--radius-sm); overflow: hidden; background: var(--bg-2); } .ft img, .ft video { width: 100%; height: 100%; object-fit: cover; display: block; }
  .fn { display: grid; gap: 2px; min-width: 0; } .fn strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
  .go { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .res { display: grid; gap: 12px; }
  .recent summary { cursor: pointer; font-size: 14px; color: var(--fg-muted); padding: 4px 0; } .recent[open] summary { margin-bottom: 8px; }
  .ritem { display: grid; gap: 6px; padding: 12px 0; border-top: 1px solid var(--border); } .ritem p { margin: 0; }
  @media (max-width: 899px) { .wrap { padding: 12px 16px 32px; } .form { padding: 12px; } .fcard { grid-template-columns: 56px minmax(0, 1fr); } .ft { width: 56px; height: 56px; } .fcard :global(.btn) { grid-column: 1 / -1; justify-self: start; } }
</style>
