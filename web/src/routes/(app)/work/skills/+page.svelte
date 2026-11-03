<script lang="ts">
  // Markdown skills: written instructions the model can open during a session. Personal ones are yours; organisation
  // ones are shared and editable only by people who manage them.
  import { onMount, tick } from 'svelte';
  import { app } from '$lib/session.svelte';
  import { work, loadOptions } from '$lib/work/store.svelte';
  import { listSkills, getSkill, setSkillEnabled, importSkill, problem, type Problem } from '$lib/work/client';
  import { CATEGORIES, categoryLabel, parseSkillMarkdown } from '$lib/work/skill';
  import { relativeTime } from '$lib/work/format';
  import type { WorkSkill } from '$lib/work/types';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import SkillEditor from '$lib/components/work/SkillEditor.svelte';

  const op = $derived(app.me?.ui?.operations?.work);
  const canOrg = $derived(!!work.options?.perms?.skills_manage);
  let rows = $state<WorkSkill[]>([]), loaded = $state(false), error = $state<Problem | null>(null);
  let q = $state(''), cat = $state(''), scopeF = $state('');
  let editing = $state<{ skill: WorkSkill | null; readonly: boolean } | null>(null), opening = $state('');
  let busyToggle = $state('');

  async function load() {
    try { rows = await listSkills(); error = null; } catch (e) { error = problem(e); } finally { loaded = true; }
  }
  onMount(() => { if (op?.enabled) { loadOptions(); load(); } });

  const mayEdit = (s: WorkSkill) => s.can_edit ?? (s.scope === 'user' || canOrg);
  const shown = $derived(rows.filter((s) => (!cat || s.category === cat) && (!scopeF || s.scope === scopeF) && (!q || `${s.title} ${s.name} ${s.description}`.toLowerCase().includes(q.toLowerCase()))));

  async function open(s: WorkSkill | null) {
    if (s) {
      opening = s.id;
      try { const full = await getSkill(s.id); editing = { skill: full, readonly: !mayEdit(s) }; }
      catch (e) { toast(problem(e).message, 'bad'); return; } finally { opening = ''; }
    } else editing = { skill: null, readonly: false };
    await tick(); document.getElementById('ed-h')?.focus();
  }
  let lastOpener = '';
  async function close() { editing = null; await tick(); (document.getElementById(lastOpener) ?? document.getElementById('new-skill'))?.focus(); }
  function saved(s: WorkSkill) {
    const i = rows.findIndex((r) => r.id === s.id);
    if (i >= 0) rows[i] = { ...rows[i], ...s, body_md: undefined, files: undefined }; else rows.unshift({ ...s, body_md: undefined, files: undefined });
    editing = null; tick().then(() => document.getElementById('sk-' + s.id)?.focus());
    loadOptions(true);
  }
  function deleted(id: string) { rows = rows.filter((r) => r.id !== id); close(); loadOptions(true); }

  async function toggle(s: WorkSkill, on: boolean) {
    busyToggle = s.id;
    try { const r = await setSkillEnabled(s.id, on); s.enabled = r?.enabled ?? on; loadOptions(true); }
    catch (e) { toast(problem(e).message, 'bad'); s.enabled = !on; }
    finally { busyToggle = ''; }
  }

  // ---- import ------------------------------------------------------------------------------------------------------
  let impOpen = $state(false), impFile = $state<File | null>(null), impScope = $state<'user' | 'org'>('user'), impErr = $state(''), impBusy = $state(false);
  let impParsed = $state<ReturnType<typeof parseSkillMarkdown> | null>(null), impRead = $state('');
  $effect(() => { if (!impOpen) { impFile = null; impParsed = null; impErr = ''; impRead = ''; impScope = 'user'; } });
  async function pickImport(list: FileList | null) {
    impErr = ''; impParsed = null; impRead = '';
    const f = list?.[0]; impFile = f ?? null;
    if (!f) return;
    if (f.size > 128 * 1024) { impRead = `This file is larger than the 64 KB a skill can hold (it is ${Math.round(f.size / 1024)} KB).`; return; }
    impParsed = parseSkillMarkdown(await f.text());
  }
  async function doImport() {
    if (!impFile) return; impBusy = true; impErr = '';
    try { const s = await importSkill(impFile, impScope); impOpen = false; toast(`Imported ${s.title || s.name}`); await load(); }
    catch (e) { impErr = problem(e).message; }
    finally { impBusy = false; }
  }
  const importable = $derived(!!impFile && !!impParsed && impParsed.problems.length === 0 && !impRead);
</script>

<svelte:head><title>Skills · Work</title></svelte:head>

{#if !op?.enabled}
  <div class="pg"><h1>Skills</h1><div class="notice warn" role="status"><div><strong>Work Mode is not available to you.</strong> {op?.reason || ''}</div></div></div>
{:else}
  <div class="pg">
    {#if editing}
      <SkillEditor skill={editing.skill} readonly={editing.readonly} {canOrg} oncancel={close} onsaved={saved} ondeleted={deleted} />
    {:else}
      <header class="hd">
        <div class="grow"><a class="small" href="/work">Work sessions</a><h1>Skills</h1></div>
        <button class="btn" onclick={() => (impOpen = true)}><Icon name="upload" size={16} /> Import .md</button>
        <button id="new-skill" class="btn primary" onclick={() => { lastOpener = 'new-skill'; open(null); }}><Icon name="plus" size={16} /> New skill</button>
      </header>
      <div class="notice info"><div class="small"><strong>What a skill is.</strong> A Markdown file with a name, a one-line description and instructions. A session lists its attached skills by description and the model opens the full text only when it needs it. A skill is only text: it cannot give a session more tools, network access or permissions.</div></div>

      {#if !loaded}<p class="muted" role="status">Loading skills…</p>
      {:else if error && !rows.length}<div class="notice bad" role="alert"><div>{error.message} <button class="btn sm" onclick={load}>Try again</button></div></div>
      {:else}
        {#if error}<p class="err" role="alert">{error.message}</p>{/if}
        <div class="row filters" role="search">
          <label class="sr-only" for="sf-q">Search skills</label><input id="sf-q" class="input grow" type="search" placeholder="Search skills" bind:value={q} />
          <label class="sr-only" for="sf-c">Category</label><select id="sf-c" class="input" bind:value={cat}><option value="">All categories</option>{#each CATEGORIES as c}<option value={c}>{categoryLabel(c)}</option>{/each}</select>
          <label class="sr-only" for="sf-s">Owner</label><select id="sf-s" class="input" bind:value={scopeF}><option value="">Personal and organisation</option><option value="user">Personal</option><option value="org">Organisation</option></select>
        </div>
        {#if !rows.length}
          <p class="muted">No skills yet. Write one for something you ask for often, such as “how we format a weekly report”, or import a .md file.</p>
        {:else if !shown.length}
          <p class="muted">No skill matches those filters.</p>
        {:else}
          <ul class="grid" aria-label="Skills">
            {#each shown as s (s.id)}
              <li class="panel"><div class="panel-b">
                <div class="row top"><h2 class="grow">{s.title || s.name}</h2><span class="chip {s.scope === 'org' ? 'info' : ''}">{s.scope === 'org' ? 'Organisation' : 'Personal'}</span></div>
                <p class="mono tiny muted nm">{s.name}</p>
                <p class="small desc">{s.description}</p>
                <div class="row" style="gap:6px"><span class="chip">{categoryLabel(s.category)}</span>{#if s.updated_at}<span class="tiny muted">Updated {relativeTime(s.updated_at)}</span>{/if}</div>
                <div class="row act">
                  <label class="row sw"><input type="checkbox" checked={s.enabled} disabled={!mayEdit(s) || busyToggle === s.id} onchange={(e) => toggle(s, e.currentTarget.checked)} title={mayEdit(s) ? '' : 'Only people who manage organisation skills can change this'} /> {s.enabled ? 'Enabled' : 'Disabled'}{#if !mayEdit(s)} <span class="tiny muted">(read only)</span>{/if}</label>
                  <span class="spacer"></span>
                  <button id={'sk-' + s.id} class="btn sm" disabled={opening === s.id} onclick={() => { lastOpener = 'sk-' + s.id; open(s); }}>{mayEdit(s) ? 'Edit' : 'View'}</button>
                </div>
              </div></li>
            {/each}
          </ul>
        {/if}
      {/if}
    {/if}
  </div>
{/if}

<Modal bind:open={impOpen} title="Import a skill from a .md file">
  <p class="small muted">The file must start with front matter between two lines of three dashes, with a name, a description and a category, followed by the instructions.</p>
  <div class="field"><label for="imp-f">Markdown file</label><input id="imp-f" class="input" type="file" accept=".md,.markdown,text/markdown,text/plain" onchange={(e) => pickImport(e.currentTarget.files)} /></div>
  {#if impRead}<p class="err" role="alert">{impRead}</p>{/if}
  {#if impParsed}
    {#if impParsed.problems.length}<div class="notice bad" role="alert"><ul class="plist">{#each impParsed.problems as p}<li>{p}</li>{/each}</ul></div>
    {:else}<dl class="kv"><dt>Name</dt><dd>{impParsed.meta.name}</dd><dt>Description</dt><dd>{impParsed.meta.description}</dd><dt>Category</dt><dd>{impParsed.meta.category ? categoryLabel(impParsed.meta.category) : 'General (default)'}</dd><dt>Instructions</dt><dd>{(impParsed.body.length / 1024).toFixed(1)} KB</dd></dl>{/if}
  {/if}
  <div class="field" style="margin-top:var(--s4)"><label for="imp-s">Who can use it</label>
    <select id="imp-s" class="input" bind:value={impScope}><option value="user">Only me</option><option value="org" disabled={!canOrg}>Everyone in the organisation{canOrg ? '' : ' (needs permission)'}</option></select></div>
  {#if impErr}<p class="err" role="alert">{impErr}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (impOpen = false)}>Cancel</button><button class="btn primary" disabled={!importable || impBusy} onclick={doImport}>{impBusy ? 'Importing…' : 'Import'}</button>{/snippet}
</Modal>

<style>
  .pg { padding: 16px 24px 32px; max-width: 1100px; width: 100%; }
  .hd { display: flex; align-items: flex-end; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
  .filters { margin-bottom: var(--s4); gap: 8px; } .filters .input { width: auto; } .filters .grow { min-width: 180px; }
  .grid { list-style: none; margin: 0; padding: 0; display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(290px, 1fr)); }
  h2 { font-size: 15px; overflow-wrap: anywhere; } .top { align-items: flex-start; flex-wrap: nowrap; } .nm { margin: 2px 0 8px; } .desc { margin: 0 0 10px; }
  .act { margin-top: 12px; } .sw { gap: 6px; font-size: 13px; }
  .plist { margin: 0; padding-left: 18px; }
  @media (max-width: 719px) { .pg { padding: 12px; } }
</style>
