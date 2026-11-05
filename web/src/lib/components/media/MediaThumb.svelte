<script lang="ts">
  // The picture of a picture or a video: the server's small preview, with the file itself as a fallback. Fills its box (cover) or fits in it (contain).
  import Icon from '$lib/components/Icon.svelte';
  import { thumbUrl, contentUrl, fmtDuration, isLibraryId } from '$lib/media/assets';

  let { id, kind, src, name = '', duration = null, fit = 'cover', badge = true, ratio }:
    { id?: string; kind: 'image' | 'video' | 'file'; src?: string | null; name?: string; duration?: number | null; fit?: 'cover' | 'contain'; badge?: boolean; ratio?: string } = $props();

  let failed = $state(false), loaded = $state(false), fallback = $state(false);
  const url = $derived.by(() => {
    if (src) return src;
    if (!id) return '';
    if (isLibraryId(id)) return fallback && kind === 'image' ? contentUrl({ id }) : thumbUrl({ id });
    return `/api/chat/attachments/${id}/content`;
  });
  $effect(() => { void url; loaded = false; failed = false; });
  function broke() { if (!fallback && id && isLibraryId(id) && kind === 'image') { fallback = true; return; } failed = true; }
</script>

<div class="th" class:contain={fit === 'contain'} style:aspect-ratio={ratio}>
  {#if kind === 'file' || failed || !url}
    <span class="ph"><Icon name={kind === 'video' ? 'video' : kind === 'image' ? 'image' : 'file'} size={22} /></span>
  {:else}
    <img src={url} alt={name} draggable="false" loading="lazy" class:in={loaded} onload={() => (loaded = true)} onerror={broke} />
    {#if !loaded}<span class="sk" aria-hidden="true"></span>{/if}
  {/if}
  {#if badge && kind === 'video' && duration}<span class="dur">{fmtDuration(duration)}</span>{/if}
</div>

<style>
  .th { position: relative; width: 100%; height: 100%; background: var(--m-card); overflow: hidden; display: grid; }
  img { grid-area: 1 / 1; width: 100%; height: 100%; object-fit: cover; display: block; opacity: 0; transition: opacity var(--medium) var(--ease); user-select: none; }
  img.in { opacity: 1; }
  .contain img { object-fit: contain; }
  .ph { place-self: center; color: var(--fg-muted); }
  .sk { grid-area: 1 / 1; background: linear-gradient(100deg, transparent 20%, color-mix(in srgb, var(--fg) 7%, transparent) 50%, transparent 80%); background-size: 200% 100%; }
  @media (prefers-reduced-motion: no-preference) { .sk { animation: shimmer 1.4s linear infinite; } @keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } } }
  .dur { position: absolute; right: 6px; bottom: 6px; font-size: 11px; line-height: 1; padding: 3px 5px; border-radius: 5px; background: rgba(12, 13, 14, 0.72); color: #f2f2f2;
    font-variant-numeric: tabular-nums; letter-spacing: 0.01em; backdrop-filter: blur(4px); }
</style>
