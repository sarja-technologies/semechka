<script lang="ts">
  // A picture or video thumbnail from the explorer's own short-lived link, or an honest placeholder (no file, expired, failed, no preview made).
  import Icon from '$lib/components/Icon.svelte';
  let { url = '', kind = 'image', fileState = 'available', label = '', fit = 'cover' }: { url?: string; kind?: string; fileState?: string; label?: string; fit?: 'cover' | 'contain' } = $props();
  let broken = $state(false);
  $effect(() => { url; broken = false; });
  const text = $derived(label || (fileState === 'expired' ? 'Expired' : fileState === 'missing' ? 'No file' : ''));
</script>
<div class="th" class:dim={fileState !== 'available'}>
  {#if url && !broken}
    <img src={url} alt={label || (kind === 'video' ? 'Video preview' : 'Picture preview')} loading="lazy" style:object-fit={fit} onerror={() => (broken = true)} />
  {:else}
    <span class="ph"><Icon name={kind === 'video' ? 'video' : 'image'} size={22} />{#if text}<span class="tiny">{text}</span>{/if}</span>
  {/if}
</div>
<style>
  .th { width: 100%; height: 100%; background: var(--surface-2); display: grid; place-items: center; overflow: hidden; }
  .th img { width: 100%; height: 100%; display: block; }
  .ph { display: grid; justify-items: center; gap: 4px; color: var(--fg-muted); text-align: center; padding: 4px; }
  .dim { opacity: 0.85; }
</style>
