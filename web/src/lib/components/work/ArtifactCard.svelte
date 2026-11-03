<script lang="ts">
  // One artifact: a preview (picture or video inline), its details, Download and Save to Library.
  import { artifactUrl, saveToLibrary, problem } from '$lib/work/client';
  import { formatBytes, isImage, isVideo } from '$lib/work/format';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';

  type A = { id: string; name: string; mime?: string; size?: number; saved_to_library?: boolean };
  let { a, compact = false, onsaved }: { a: A; compact?: boolean; onsaved?: (id: string) => void } = $props();
  const img = $derived(isImage(a.name, a.mime)), vid = $derived(isVideo(a.name, a.mime));
  let saved = $state(false), busy = $state(false), zoom = $state(false), broken = $state(false);
  $effect(() => { saved = !!a.saved_to_library; });
  // Save is requested here; the parent learns of it through onsaved so every card for this artifact agrees.

  async function save() {
    busy = true;
    try { await saveToLibrary(a.id); saved = true; onsaved?.(a.id); toast('Saved to your Library'); }
    catch (e) { toast(problem(e).message, 'bad'); }
    finally { busy = false; }
  }
</script>

<article class="art" class:compact aria-label={`Artifact ${a.name}`}>
  {#if img && !broken}
    <button class="thumb" onclick={() => (zoom = true)} aria-label={`Open a larger preview of ${a.name}`}>
      <img src={artifactUrl(a.id)} alt={`Preview of ${a.name}`} loading="lazy" onerror={() => (broken = true)} />
    </button>
  {:else if vid && !broken}
    <!-- svelte-ignore a11y_media_has_caption -->
    <video class="thumb vid" src={artifactUrl(a.id)} controls preload="metadata" aria-label={`Video ${a.name}`} onerror={() => (broken = true)}></video>
  {:else}
    <div class="thumb none"><Icon name="file" size={22} /><span class="tiny muted">{broken ? 'Preview not available' : 'No preview'}</span></div>
  {/if}
  <div class="info">
    <strong class="name" title={a.name}>{a.name}</strong>
    <span class="tiny muted">{a.mime ?? ''}{a.mime && a.size ? ' · ' : ''}{formatBytes(a.size)}</span>
    <div class="row act">
      <a class="btn sm" href={artifactUrl(a.id, true)} download={a.name}><Icon name="download" size={14} /> Download</a>
      {#if saved}<span class="chip ok">In Library</span>
      {:else}<button class="btn sm" onclick={save} disabled={busy}><Icon name="library" size={14} /> {busy ? 'Saving…' : 'Save to Library'}</button>{/if}
    </div>
  </div>
</article>

<Modal bind:open={zoom} title={a.name} wide>
  <img class="big" src={artifactUrl(a.id)} alt={a.name} />
  {#snippet footer()}<a class="btn" href={artifactUrl(a.id, true)} download={a.name}><Icon name="download" size={14} /> Download</a><button class="btn primary" onclick={() => (zoom = false)}>Close</button>{/snippet}
</Modal>

<style>
  .art { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: var(--s3); padding: var(--s3); border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
  .art.compact { grid-template-columns: 84px minmax(0, 1fr); padding: var(--s2); }
  .thumb { all: unset; box-sizing: border-box; display: grid; place-items: center; width: 100%; aspect-ratio: 4 / 3; background: var(--surface-2); border-radius: var(--radius-sm); overflow: hidden; cursor: zoom-in; border: 1px solid var(--border); }
  .thumb:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .thumb img, .thumb.vid { width: 100%; height: 100%; object-fit: cover; display: block; }
  .thumb.vid { cursor: default; object-fit: contain; background: var(--surface-2); }
  .thumb.none { cursor: default; gap: 2px; align-content: center; color: var(--fg-muted); text-align: center; }
  .info { display: grid; gap: 4px; align-content: start; min-width: 0; }
  .name { overflow-wrap: anywhere; font-size: 13px; }
  .act { gap: 6px; margin-top: 4px; }
  .big { display: block; max-width: 100%; max-height: 70vh; margin: 0 auto; }
  @media (max-width: 480px) { .art { grid-template-columns: 84px minmax(0, 1fr); } }
</style>
