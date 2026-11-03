<script lang="ts">
  import { untrack } from 'svelte';
  // The session's workspace: browse, preview, download, upload and delete. Everything comes from the server; folders
  // load when opened. Paths are only ever sent as data, the server resolves them inside the container.
  import { listFiles, fileUrl, readTextFile, uploadFile, deleteFile, problem } from '$lib/work/client';
  import { formatBytes, isTextFile, isImage, relativeTime } from '$lib/work/format';
  import type { FileEntry, WorkState } from '$lib/work/types';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';

  let { sessionId, refreshKey = 0, uploadMaxMb, usage }:
    { sessionId: string; refreshKey?: number; uploadMaxMb?: number; usage?: WorkState['usage'] } = $props();

  type Node = { loading: boolean; error: string; entries?: FileEntry[] };
  let tree = $state<Record<string, Node>>({});
  let expanded = $state<Record<string, boolean>>({});
  let selected = $state<FileEntry | null>(null);
  let dirTarget = $state('');           // where uploads go: the opened folder, else the selected file's folder, else the root
  let pv = $state<{ loading: boolean; text: string; cut: boolean; error: string }>({ loading: false, text: '', cut: false, error: '' });
  let results = $state<{ name: string; ok: boolean; msg: string }[]>([]);
  let uploading = $state(false), drag = $state(false), input: HTMLInputElement | undefined = $state();
  let confirmDel = $state(false), delBusy = $state(false);
  const PREVIEW_MAX = 256 * 1024;

  async function load(path: string) {
    const prev = tree[path];
    tree[path] = { loading: true, error: '', entries: prev?.entries };
    try {
      const l = await listFiles(sessionId, path);
      tree[path] = { loading: false, error: '', entries: [...l.entries].sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1)) };
    } catch (e) { tree[path] = { loading: false, error: problem(e).message, entries: prev?.entries }; }
  }
  $effect(() => { sessionId; untrack(() => { tree = {}; expanded = {}; selected = null; results = []; dirTarget = ''; load(''); }); });
  let lastKey = untrack(() => refreshKey);
  $effect(() => { const k = refreshKey; if (k !== lastKey) { lastKey = k; untrack(refreshAll); } });
  function refreshAll() { for (const p of ['', ...Object.keys(expanded).filter((k) => expanded[k])]) load(p); if (selected) pick(selected, true); }

  function toggle(e: FileEntry) {
    expanded[e.path] = !expanded[e.path];
    dirTarget = e.path;
    if (expanded[e.path] && !tree[e.path]) load(e.path);
  }
  const parent = (p: string) => (p.includes('/') ? p.slice(0, p.lastIndexOf('/')) : '');

  async function pick(e: FileEntry, quiet = false) {
    selected = e; dirTarget = parent(e.path);
    pv = { loading: false, text: '', cut: false, error: '' };
    if (!isTextFile(e.name, e.mime) || isImage(e.name, e.mime)) return;
    if (typeof e.size === 'number' && e.size > 5 * PREVIEW_MAX) { pv.error = `This file is ${formatBytes(e.size)}, too large to preview. Download it to open it.`; return; }
    pv.loading = !quiet;
    try { const r = await readTextFile(sessionId, e.path, PREVIEW_MAX); pv = { loading: false, text: r.text, cut: r.cut, error: '' }; }
    catch (err) { pv = { loading: false, text: '', cut: false, error: problem(err).message }; }
  }

  async function upload(files: FileList | File[]) {
    const list = [...files];
    if (!list.length) return;
    uploading = true; results = [];
    for (const f of list) {
      if (uploadMaxMb && f.size > uploadMaxMb * 1024 * 1024) { results.push({ name: f.name, ok: false, msg: `${formatBytes(f.size)} is over the ${uploadMaxMb} MB limit for one file.` }); continue; }
      try { await uploadFile(sessionId, dirTarget, f); results.push({ name: f.name, ok: true, msg: `Added (${formatBytes(f.size)})` }); }
      catch (e) { results.push({ name: f.name, ok: false, msg: problem(e).message }); }
    }
    uploading = false;
    if (input) input.value = '';
    await load(dirTarget);
    if (dirTarget) expanded[dirTarget] = true;
  }

  async function remove() {
    if (!selected) return;
    delBusy = true;
    const gone = selected;
    try { await deleteFile(sessionId, gone.path); selected = null; confirmDel = false; toast(`Deleted ${gone.name}`); await load(parent(gone.path)); }
    catch (e) { toast(problem(e).message, 'bad'); confirmDel = false; }
    finally { delBusy = false; }
  }
  const dirLabel = $derived(dirTarget ? `/work/${dirTarget}` : '/work');
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="fp" class:drag ondragover={(e) => { if (e.dataTransfer?.types.includes('Files')) { e.preventDefault(); drag = true; } }} ondragleave={() => (drag = false)} ondrop={(e) => { e.preventDefault(); drag = false; if (e.dataTransfer?.files) upload(e.dataTransfer.files); }}>
  <div class="bar">
    <span class="small muted grow">Workspace <span class="mono">/work</span></span>
    <button class="btn sm" onclick={() => input?.click()} disabled={uploading}><Icon name="upload" size={14} /> {uploading ? 'Uploading…' : 'Upload'}</button>
    <button class="btn sm icon" aria-label="Refresh files" onclick={refreshAll}><Icon name="refresh" size={14} /></button>
    <input bind:this={input} type="file" multiple hidden tabindex="-1" onchange={(e) => upload(e.currentTarget.files ?? [])} aria-label="Choose files to upload" />
  </div>
  <p class="tiny muted up">Uploads go to <span class="mono">{dirLabel}</span>{uploadMaxMb ? `. Up to ${uploadMaxMb} MB per file.` : '.'} <span class="dropnote">You can also drop files here.</span>
    {#if usage?.workspace_bytes != null}<br />Workspace size: {formatBytes(usage.workspace_bytes)}{usage.workspace_limit_bytes ? ` of ${formatBytes(usage.workspace_limit_bytes)}` : ''}.{/if}</p>

  {#if results.length}
    <ul class="res" aria-label="Upload results" role="status">
      {#each results as r}<li class={r.ok ? '' : 'err'}>{#if r.msg.includes(r.name)}{r.msg}{:else}<strong>{r.name}</strong>: {r.msg}{/if}</li>{/each}
    </ul>
  {/if}

  <div class="tree" role="region" aria-label="Files">
    {#snippet level(path: string, depth: number)}
      {@const n = tree[path]}
      {#if !n || (n.loading && !n.entries)}<p class="muted small ind" style:padding-left={`${8 + depth * 14}px`} role="status">Loading…</p>
      {:else}
        {#if n.error}<p class="err small ind" style:padding-left={`${8 + depth * 14}px`} role="alert">{n.error} <button class="linkish" onclick={() => load(path)}>Try again</button></p>{/if}
        {#if n.entries && !n.entries.length && !n.error}<p class="muted small ind" style:padding-left={`${8 + depth * 14}px`}>{path ? 'This folder is empty.' : 'The workspace is empty. Upload files or ask the session to create some.'}</p>{/if}
        {#if n.entries?.length}
          <ul>
            {#each n.entries as e (e.path)}
              <li>
                {#if e.type === 'dir'}
                  <button class="node" style:padding-left={`${8 + depth * 14}px`} aria-expanded={!!expanded[e.path]} onclick={() => toggle(e)}>
                    <span class="chev" class:open={expanded[e.path]}><Icon name="chevron" size={13} /></span><Icon name="folder" size={15} /><span class="nm">{e.name}</span>
                  </button>
                  {#if expanded[e.path]}{@render level(e.path, depth + 1)}{/if}
                {:else}
                  <button class="node" class:on={selected?.path === e.path} style:padding-left={`${8 + depth * 14 + 17}px`} aria-current={selected?.path === e.path ? 'true' : undefined} onclick={() => pick(e)}>
                    <Icon name="file" size={15} /><span class="nm">{e.name}</span><span class="sz tiny muted">{formatBytes(e.size)}</span>
                  </button>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      {/if}
    {/snippet}
    {@render level('', 0)}
  </div>

  {#if selected}
    <section class="prev" aria-label={`Preview of ${selected.name}`}>
      <div class="ph">
        <strong class="nm2" title={selected.path}>{selected.name}</strong>
        <span class="tiny muted">{formatBytes(selected.size)}{selected.modified ? ` · ${relativeTime(selected.modified)}` : ''}</span>
        <span class="break"></span>
        <a class="btn sm" href={fileUrl(sessionId, selected.path, true)} download={selected.name}><Icon name="download" size={14} /> Download</a>
        <button class="btn sm danger" onclick={() => (confirmDel = true)}><Icon name="trash" size={14} /> Delete</button>
      </div>
      {#if isImage(selected.name, selected.mime)}
        <img src={fileUrl(sessionId, selected.path)} alt={`Preview of ${selected.name}`} class="pimg" />
      {:else if pv.loading}<p class="muted small" role="status">Loading preview…</p>
      {:else if pv.error}<p class="muted small" role="status">{pv.error}</p>
      {:else if isTextFile(selected.name, selected.mime)}
        <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
        <pre tabindex="0" aria-label="File contents">{pv.text || '(empty file)'}</pre>
        {#if pv.cut}<p class="tiny muted">Only the first {formatBytes(PREVIEW_MAX)} are shown. Download the file to read the rest.</p>{/if}
      {:else}<p class="muted small">No preview for this kind of file. Download it to open it.</p>{/if}
    </section>
  {/if}
</div>

<Modal bind:open={confirmDel} title={selected?.type === 'dir' ? 'Delete this folder?' : 'Delete this file?'}>
  <p><strong class="mono">{selected?.path}</strong> will be removed from the session's workspace. This cannot be undone. Artifacts already published keep their own copy.</p>
  {#snippet footer()}<button class="btn" onclick={() => (confirmDel = false)}>Cancel</button><button class="btn danger solid" disabled={delBusy} onclick={remove}>{delBusy ? 'Deleting…' : 'Delete'}</button>{/snippet}
</Modal>

<style>
  .fp { display: grid; gap: var(--s2); border-radius: var(--radius); }
  .fp.drag { outline: 2px dashed var(--accent); outline-offset: 2px; }
  .bar { display: flex; align-items: center; gap: 6px; }
  .up { margin: 0; }
  @media (pointer: coarse) { .dropnote { display: none; } }
  .res { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; font-size: 13px; }
  .tree { border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--surface); max-height: 320px; overflow: auto; padding: 4px 0; }
  ul { list-style: none; margin: 0; padding: 0; }
  .node { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 6px; width: 100%; min-height: 30px; padding-right: 8px; cursor: pointer; font-size: 13px; }
  .node:hover { background: var(--hover); } .node.on { background: var(--selected); }
  .node:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .chev { display: inline-flex; width: 13px; transition: transform var(--fast) var(--ease); color: var(--fg-muted); } .chev.open { transform: rotate(90deg); }
  .nm { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; min-width: 0; } .sz { flex: none; }
  .ind { margin: 0; padding-top: 6px; padding-bottom: 6px; }
  .prev { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: var(--s2); display: grid; gap: 8px; background: var(--surface); min-width: 0; }
  .ph { display: flex; align-items: center; gap: 6px 8px; flex-wrap: wrap; } .nm2 { overflow-wrap: anywhere; } .break { flex-basis: 100%; height: 0; }
  pre { margin: 0; padding: 8px 10px; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); font: 12.5px/1.5 var(--mono); white-space: pre; overflow: auto; max-height: 300px; }
  .pimg { max-width: 100%; max-height: 300px; object-fit: contain; border-radius: var(--radius-sm); background: var(--surface-2); }
  @media (pointer: coarse) { .node { min-height: 40px; } }
</style>
