<script lang="ts">
  // Files on their way to the server: real progress, cancel, and retry when something failed.
  import Icon from '$lib/components/Icon.svelte';
  import type { Upload } from '$lib/media/upload.svelte';
  // `s` is any page store that keeps `uploads` (OpenJutsu Studio's, Imajutsu's). `kind: 'any'` or an explicit `uploads` list shows exactly what the caller passes.
  let { s, kind = 'video', uploads }: { s: { uploads: Upload[]; retryUpload: (u: Upload) => void; dismissUpload: (u: Upload) => void }; kind?: 'video' | 'image' | 'any'; uploads?: Upload[] } = $props();
  const rows = $derived(uploads ?? (kind === 'any' ? s.uploads : s.uploads.filter((u) => (kind === 'video') === /^video\//.test(u.file.type) || (kind === 'video') === /\.(mp4|m4v|mov|webm|mkv|avi)$/i.test(u.file.name))));
</script>
{#if rows.length}
  <ul class="up" aria-label="Uploads">
    {#each rows as u (u.key)}
      <li>
        <span class="nm" title={u.file.name}>{u.file.name}</span>
        {#if u.status === 'error'}
          <span class="st-note bad" role="alert">{u.error}</span>
          <button type="button" class="btn sm" onclick={() => s.retryUpload(u)}>Try again</button>
          <button type="button" class="btn sm ghost" aria-label={`Dismiss ${u.file.name}`} onclick={() => s.dismissUpload(u)}><Icon name="x" size={13} /></button>
        {:else}
          <div class="st-prog" class:ind={u.status === 'processing'} role="progressbar" aria-label={`Uploading ${u.file.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(u.fraction * 100)}><i style:width={u.status === 'processing' ? undefined : `${u.fraction * 100}%`}></i></div>
          <span class="tiny muted pc">{u.status === 'queued' ? 'Waiting…' : u.status === 'processing' ? 'Preparing…' : `${Math.round(u.fraction * 100)}%`}</span>
          <button type="button" class="btn sm ghost" aria-label={`Cancel upload of ${u.file.name}`} onclick={() => s.dismissUpload(u)}><Icon name="x" size={13} /></button>
        {/if}
      </li>
    {/each}
  </ul>
{/if}
<style>
  .up { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
  li { display: grid; grid-template-columns: minmax(0, 1fr) minmax(80px, 160px) auto auto; align-items: center; gap: 8px; }
  li > .st-note { grid-column: 1 / -1; } .nm { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .pc { min-width: 5ch; text-align: right; }
</style>
