<script lang="ts">
  // Create, edit or read one skill. Checks the same limits the server does so mistakes show beside the field.
  import { untrack } from 'svelte';
  import { createSkill, updateSkill, deleteSkill, problem } from '$lib/work/client';
  import { CATEGORIES, categoryLabel, validateDraft, serializeSkill, slugify, BODY_MAX, FILES_MAX } from '$lib/work/skill';
  import { formatBytes } from '$lib/work/format';
  import type { WorkSkill } from '$lib/work/types';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Markdown from '$lib/components/Markdown.svelte';
  import Modal from '$lib/components/Modal.svelte';

  let { skill = null, readonly = false, canOrg, oncancel, onsaved, ondeleted }:
    { skill?: WorkSkill | null; readonly?: boolean; canOrg: boolean; oncancel: () => void; onsaved: (s: WorkSkill) => void; ondeleted: (id: string) => void } = $props();

  type F = { name: string; content: string; key: number };
  let n = 0;
  let scope = $state<'org' | 'user'>('user'), name = $state(''), title = $state(''), description = $state(''), category = $state('general'), enabled = $state(true), body = $state('');
  let files = $state<F[]>([]), mode = $state<'write' | 'preview'>('write'), nameTouched = $state(false);
  let errs = $state<Record<string, string>>({}), serverErr = $state(''), busy = $state(false), confirmDel = $state(false), fileErr = $state('');
  let fileInput: HTMLInputElement | undefined = $state();

  $effect(() => {
    const s = skill;
    untrack(() => {
      scope = s?.scope ?? 'user'; name = s?.name ?? ''; title = s?.title ?? ''; description = s?.description ?? ''; category = s?.category ?? 'general';
      enabled = s?.enabled ?? true; body = s?.body_md ?? ''; files = (s?.files ?? []).map((f) => ({ name: f.name, content: f.content ?? '', key: ++n }));
      nameTouched = !!s; errs = {}; serverErr = ''; mode = 'write'; fileErr = '';
    });
  });

  const bytes = (s: string) => new TextEncoder().encode(s).length;
  const bodyBytes = $derived(bytes(body));
  const filesBytes = $derived(files.reduce((t, f) => t + bytes(f.content), 0));
  const draft = () => ({ name, title, description, category, body_md: body, files: files.map((f) => ({ name: f.name.trim(), content: f.content })) });

  function onTitle() { if (!nameTouched && !skill) name = slugify(title); }

  async function save() {
    serverErr = ''; errs = validateDraft(draft());
    if (Object.keys(errs).length) { queueMicrotask(() => document.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()); return; }
    busy = true;
    try {
      const input = { ...draft(), scope, enabled };
      const saved = skill ? await updateSkill(skill.id, input) : await createSkill(input);
      toast('Skill saved'); onsaved({ ...saved, body_md: saved.body_md ?? body });
    } catch (e) { serverErr = problem(e).message; }
    finally { busy = false; }
  }
  async function remove() {
    if (!skill) return; busy = true;
    try { await deleteSkill(skill.id); toast('Skill deleted'); confirmDel = false; ondeleted(skill.id); }
    catch (e) { toast(problem(e).message, 'bad'); confirmDel = false; }
    finally { busy = false; }
  }
  function download() {
    const text = serializeSkill({ name, description, category }, body);
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: 'text/markdown' })); a.download = `${name || 'skill'}.md`; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }
  function addFile() { files.push({ name: '', content: '', key: ++n }); queueMicrotask(() => document.getElementById(`sf-n-${n}`)?.focus()); }
  async function fromDevice(list: FileList | null) {
    fileErr = '';
    for (const f of list ?? []) {
      if (f.size > 256 * 1024) { fileErr = `${f.name} is ${formatBytes(f.size)}. Supporting files must be small text files, 256 KB at most.`; continue; }
      const text = await f.text();
      if (text.includes('\u0000')) { fileErr = `${f.name} is not a text file.`; continue; }
      files.push({ name: f.name, content: text, key: ++n });
    }
    if (fileInput) fileInput.value = '';
  }
  const ro = $derived(readonly);
</script>

<form class="ed" onsubmit={(e) => { e.preventDefault(); if (!ro) save(); }} novalidate>
  <div class="hd">
    <button type="button" class="btn ghost sm" onclick={oncancel}><span class="flip"><Icon name="chevron" size={14} /></span> All skills</button>
    <h2 id="ed-h" tabindex="-1">{ro ? skill?.title : skill ? 'Edit skill' : 'New skill'}</h2>
    {#if skill}<span class="chip">{skill.scope === 'org' ? 'Organisation' : 'Personal'}</span>{/if}
  </div>
  {#if ro}<div class="notice info" role="note"><span>This is an organisation skill. Only people who manage organisation skills can change it. You can read it, and attach it to a session.</span></div>{/if}

  <div class="field">
    <span class="label" id="sc-l">Who can use it</span>
    <div class="row" role="radiogroup" aria-labelledby="sc-l">
      <label class="row"><input type="radio" name="sc" value="user" bind:group={scope} disabled={ro || !!skill} /> Only me</label>
      <label class="row"><input type="radio" name="sc" value="org" bind:group={scope} disabled={ro || !!skill || !canOrg} /> Everyone in the organisation</label>
    </div>
    <span class="hint">{!canOrg && !skill ? 'Creating organisation skills needs the permission to manage them. ' : ''}{skill ? 'The scope of a saved skill cannot be changed. Create a copy to share it differently.' : 'Personal skills are visible only to you.'}</span>
  </div>

  <div class="two">
    <div class="field"><label for="sk-t">Title</label><input id="sk-t" class="input" bind:value={title} oninput={onTitle} maxlength="120" readonly={ro} aria-invalid={errs.title ? 'true' : undefined} aria-describedby={errs.title ? 'sk-t-e' : undefined} />{#if errs.title}<span id="sk-t-e" class="err">{errs.title}</span>{/if}</div>
    <div class="field"><label for="sk-n">Name</label><input id="sk-n" class="input mono" bind:value={name} oninput={() => (nameTouched = true)} maxlength="64" spellcheck="false" autocapitalize="off" readonly={ro} aria-invalid={errs.name ? 'true' : undefined} aria-describedby={errs.name ? 'sk-n-e' : 'sk-n-h'} />
      {#if errs.name}<span id="sk-n-e" class="err">{errs.name}</span>{:else}<span id="sk-n-h" class="hint">Lower-case letters, digits and hyphens. The model uses this name to open the skill.</span>{/if}</div>
  </div>
  <div class="two">
    <div class="field"><label for="sk-c">Category</label>
      <select id="sk-c" class="input" bind:value={category} disabled={ro} aria-invalid={errs.category ? 'true' : undefined}>{#each CATEGORIES as c}<option value={c}>{categoryLabel(c)}</option>{/each}</select>{#if errs.category}<span class="err">{errs.category}</span>{/if}</div>
    <div class="field"><span class="label">Availability</span><label class="row"><input type="checkbox" bind:checked={enabled} disabled={ro} /> Enabled</label><span class="hint">A disabled skill cannot be attached to new sessions.</span></div>
  </div>
  <div class="field"><label for="sk-d">One-line description</label><input id="sk-d" class="input" bind:value={description} maxlength="300" readonly={ro} aria-invalid={errs.description ? 'true' : undefined} aria-describedby={errs.description ? 'sk-d-e' : 'sk-d-h'} />
    {#if errs.description}<span id="sk-d-e" class="err">{errs.description}</span>{:else}<span id="sk-d-h" class="hint">Shown to the model in its list of skills. Say when it is useful.</span>{/if}</div>

  <div class="field">
    <div class="row"><label for="sk-b" class="grow">Instructions (Markdown)</label>
      <div class="seg2" role="group" aria-label="Editor view">
        <button type="button" aria-pressed={mode === 'write'} onclick={() => (mode = 'write')}>Write</button>
        <button type="button" aria-pressed={mode === 'preview'} onclick={() => (mode = 'preview')}>Preview</button>
      </div></div>
    {#if mode === 'write'}
      <textarea id="sk-b" class="input mono bodyta" rows="14" bind:value={body} readonly={ro} spellcheck="false" aria-invalid={errs.body ? 'true' : undefined} aria-describedby={errs.body ? 'sk-b-e' : 'sk-b-h'}></textarea>
    {:else}
      <div class="pv" role="region" aria-label="Preview of the instructions">{#if body.trim()}<Markdown text={body} />{:else}<p class="muted small">Nothing to preview yet.</p>{/if}</div>
    {/if}
    {#if errs.body}<span id="sk-b-e" class="err">{errs.body}</span>{:else}<span id="sk-b-h" class="hint">{(bodyBytes / 1024).toFixed(1)} KB of {BODY_MAX / 1024} KB. The model reads this only when it opens the skill.</span>{/if}
  </div>

  <fieldset class="field files">
    <legend class="label">Supporting files (optional)</legend>
    <span class="hint">Small text files copied into the session's workspace when the skill is opened, such as templates or scripts. {formatBytes(filesBytes)} of {formatBytes(FILES_MAX)}.</span>
    {#each files as f (f.key)}
      <div class="sf">
        <div class="row"><label class="sr-only" for={`sf-n-${f.key}`}>File name</label><input id={`sf-n-${f.key}`} class="input mono grow" placeholder="file name, for example template.py" bind:value={f.name} readonly={ro} />
          {#if !ro}<button type="button" class="btn sm danger" aria-label={`Remove ${f.name || 'this file'}`} onclick={() => (files = files.filter((x) => x.key !== f.key))}><Icon name="trash" size={14} /></button>{/if}</div>
        <label class="sr-only" for={`sf-c-${f.key}`}>Contents of {f.name || 'this file'}</label>
        <textarea id={`sf-c-${f.key}`} class="input mono" rows="4" bind:value={f.content} readonly={ro} spellcheck="false"></textarea>
      </div>
    {/each}
    {#if errs.files}<span class="err" role="alert">{errs.files}</span>{/if}
    {#if fileErr}<span class="err" role="alert">{fileErr}</span>{/if}
    {#if !ro}
      <div class="row">
        <button type="button" class="btn sm" onclick={addFile}><Icon name="plus" size={14} /> Add a file</button>
        <button type="button" class="btn sm" onclick={() => fileInput?.click()}><Icon name="upload" size={14} /> Add from this device</button>
        <input bind:this={fileInput} type="file" multiple hidden tabindex="-1" aria-label="Choose text files" onchange={(e) => fromDevice(e.currentTarget.files)} />
      </div>
    {/if}
  </fieldset>

  {#if serverErr}<p class="err" role="alert">{serverErr}</p>{/if}
  <div class="row foot">
    {#if !ro}<button class="btn primary" type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save skill'}</button>{/if}
    <button type="button" class="btn" onclick={oncancel}>{ro ? 'Close' : 'Cancel'}</button>
    <button type="button" class="btn" onclick={download} disabled={!name}><Icon name="download" size={14} /> Download .md</button>
    <span class="spacer"></span>
    {#if skill && !ro}<button type="button" class="btn danger" onclick={() => (confirmDel = true)}><Icon name="trash" size={14} /> Delete</button>{/if}
  </div>
</form>

<Modal bind:open={confirmDel} title="Delete this skill?">
  <p>“{skill?.title}” will be deleted{skill?.scope === 'org' ? ' for everyone in the organisation' : ''}. Sessions it was attached to keep working, but can no longer open it.</p>
  {#snippet footer()}<button class="btn" onclick={() => (confirmDel = false)}>Cancel</button><button class="btn danger solid" disabled={busy} onclick={remove}>Delete skill</button>{/snippet}
</Modal>

<style>
  .ed { max-width: 820px; }
  .hd { display: flex; align-items: center; gap: var(--s3); flex-wrap: wrap; margin-bottom: var(--s4); } .hd h2 { font-size: 18px; } .hd h2:focus { outline: none; }
  .flip { display: inline-flex; transform: rotate(180deg); }
  textarea.bodyta, input.mono, textarea.mono { font-family: var(--mono); }
  .two { align-items: start; display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 var(--s4); }
  .seg2 { display: inline-flex; } .seg2 button { min-height: 28px; padding: 0 12px; border: 1px solid var(--border-strong); background: var(--btn-bg); color: var(--btn-fg); font: 500 13px var(--font); cursor: pointer; }
  .seg2 button:first-child { border-radius: var(--radius-chip) 0 0 var(--radius-chip); } .seg2 button:last-child { border-radius: 0 var(--radius-chip) var(--radius-chip) 0; border-left: 0; }
  .seg2 button[aria-pressed='true'] { background: var(--btn-primary-bg); color: var(--btn-primary-fg); border-color: var(--btn-primary-bg); }
  .bodyta { font-size: 13px; line-height: 1.5; min-height: 260px; }
  .pv { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--s3); min-height: 260px; max-height: 520px; overflow: auto; background: var(--surface); }
  fieldset.files { border: 0; padding: 0; margin: 0 0 var(--s4); min-width: 0; } .files legend { padding: 0; }
  .sf { display: grid; gap: 6px; margin: 8px 0; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); }
  .foot { margin-top: var(--s4); }
  @media (max-width: 640px) { .two { grid-template-columns: minmax(0, 1fr); } }
</style>
