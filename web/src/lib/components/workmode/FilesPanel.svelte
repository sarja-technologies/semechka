<script lang="ts">
  // The agent's workspace: look through it, download a file, add one for the agent to work on. The agent sees what you add under /work.
  import { listFiles, uploadFile, fileUrl } from '$lib/workmode/client';
  import { errText } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  let { cid, refresh = 0 }: { cid: string; refresh?: number } = $props();
  let dir = $state(''), entries = $state<{ name: string; path: string; type: 'file' | 'dir'; size?: number }[]>([]), loading = $state(true), err = $state(''), note = $state(''), input: HTMLInputElement | undefined = $state();
  async function load(p = dir) {
    loading = true; err = '';
    try { const r = await listFiles(cid, p); dir = r.path ?? p; entries = [...r.entries].sort((a, b) => (a.type === b.type ? a.name.localeCompare(b.name) : a.type === 'dir' ? -1 : 1)); }
    catch (e) { err = errText(e); } finally { loading = false; }
  }
  $effect(() => { void refresh; load(dir); });
  const up = $derived(dir.includes('/') ? dir.slice(0, dir.lastIndexOf('/')) : '');
  const size = (n?: number) => (n == null ? '' : n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : n >= 1024 ? `${Math.round(n / 1024)} KB` : `${n} B`);
  async function add(e: Event) {
    const i = e.currentTarget as HTMLInputElement, f = i.files?.[0]; i.value = ''; if (!f) return;
    note = ''; err = '';
    try { await uploadFile(cid, dir ? `${dir}/${f.name}` : f.name, f); note = `${f.name} is in the workspace.`; await load(); } catch (x) { err = errText(x); }
  }
</script>
<section class="files" aria-label="Workspace files">
  <div class="h"><strong>/work{dir ? '/' + dir : ''}</strong>
    <span class="acts"><button type="button" class="btn sm" onclick={() => input?.click()}><Icon name="upload" size={14} /> Add a file</button></span>
    <input bind:this={input} type="file" class="sr-only" tabindex="-1" aria-label="Choose a file to add" onchange={add} /></div>
  {#if err}<p class="err small" role="alert">{err}</p>{/if}
  {#if note}<p class="small muted" role="status">{note}</p>{/if}
  {#if loading}<p class="small muted" role="status">Loading…</p>
  {:else}
    <ul>
      {#if dir}<li><button type="button" class="row" onclick={() => load(up)}><Icon name="folder" size={14} /> ..</button></li>{/if}
      {#each entries as f (f.path)}
        <li>{#if f.type === 'dir'}<button type="button" class="row" onclick={() => load(f.path)}><Icon name="folder" size={14} /> {f.name}</button>
          {:else}<a class="row" href={fileUrl(cid, f.path, true)} download><Icon name="download" size={14} /> {f.name} <span class="tiny muted">{size(f.size)}</span></a>{/if}</li>
      {:else}<li class="small muted pad">Nothing here yet.</li>{/each}
    </ul>
  {/if}
</section>
<style>
  .files { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); padding: 8px 10px; margin-bottom: 8px; display: grid; gap: 6px; max-height: 240px; overflow: auto; }
  .h { display: flex; align-items: center; gap: 8px; font: 13px var(--mono); } .acts { margin-left: auto; }
  ul { list-style: none; margin: 0; padding: 0; display: grid; } .pad { padding: 4px 0; } p { margin: 0; }
  .row { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 8px; min-height: 30px; padding: 0 6px; border-radius: var(--radius-sm); cursor: pointer; font-size: 13px; color: var(--fg); width: 100%; }
  .row:hover { background: var(--hover); } .row:focus-visible { outline: 2px solid var(--accent); }
</style>
