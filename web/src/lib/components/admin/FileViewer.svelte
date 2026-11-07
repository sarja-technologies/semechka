<script lang="ts">
  // Preview or download of ONE stored file. The file is streamed from the existing store through a link that works for this administrator only and
  // for a few minutes; asking for it writes the audit entry, so nothing is fetched until the person presses the button.
  import { fileLink } from '$lib/admin/client';
  import { errMsg } from './util';
  import { fmtSize, type FileRef } from '$lib/admin/explorer';
  import Modal from '$lib/components/Modal.svelte';
  let { file = $bindable() }: { file: FileRef | null } = $props();
  let open = $state(false), url = $state(''), err = $state(''), busy = $state(false), vErr = $state(false);
  $effect(() => { if (file) { open = true; url = ''; err = ''; vErr = false; load(file); } });
  $effect(() => { if (!open) file = null; });
  async function load(f: FileRef) {
    if (!(f.kind === 'image' || f.kind === 'video')) return;
    busy = true;
    try { const r = await fileLink(f.type, f.id, 'view'); if (r) url = r.url; else open = false; } catch (e) { err = errMsg(e); } finally { busy = false; }
  }
  async function download() {
    if (!file) return;
    busy = true; err = '';
    try {
      const r = await fileLink(file.type, file.id, 'download');
      if (r) { const a = document.createElement('a'); a.href = r.url; a.download = r.filename ?? ''; document.body.append(a); a.click(); a.remove(); }
    } catch (e) { err = errMsg(e); } finally { busy = false; }
  }
</script>
<Modal bind:open title={file?.label || file?.name || (file?.kind === 'video' ? 'Video' : 'File')} wide>
  {#if file}
    <div class="stage">
      {#if busy && !url}<p class="muted" role="status">Recording the access and preparing the file…</p>
      {:else if err}<div class="notice bad" role="alert"><div>{err}</div></div>
      {:else if url && file.kind === 'image'}<img src={url} alt={file.label || 'Picture'} onerror={() => (vErr = true)} />
      {:else if url && file.kind === 'video'}<!-- svelte-ignore a11y_media_has_caption --><video src={url} controls preload="metadata" playsinline onerror={() => (vErr = true)}></video>
      {:else}<p class="muted">This kind of file has no preview. You can download it.</p>{/if}
      {#if vErr}<div class="notice warn" role="alert"><div>The browser could not play this file. Download it to open it elsewhere.</div></div>{/if}
    </div>
    <p class="tiny muted" style="margin:8px 0 0">{file.mime ?? ''}{file.size ? ` · ${fmtSize(file.size)}` : ''} · <span class="mono">{file.id}</span></p>
  {/if}
  {#snippet footer()}
    <span class="small muted grow">Each preview and download is its own audit entry.</span>
    <button class="btn primary" onclick={download} disabled={busy}>Download</button>
  {/snippet}
</Modal>
<style>
  .stage { display: grid; place-items: center; min-height: 160px; background: var(--surface-2); border-radius: var(--radius-sm); overflow: hidden; }
  .stage img, .stage video { max-width: 100%; max-height: 60vh; display: block; }
</style>
