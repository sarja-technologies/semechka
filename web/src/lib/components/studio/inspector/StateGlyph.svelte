<script lang="ts">
  // A node/scene state as a shape, not just a colour: hollow = waiting, play = running now, tick = done, dashed tick = reused
  // from cache, cross = failed. Always paired with a text label somewhere nearby or an aria-label.
  let { state, size = 16, label = '' }: { state: string; size?: number; label?: string } = $props();
</script>
<svg class="g {state}" width={size} height={size} viewBox="0 0 16 16" role={label ? 'img' : undefined} aria-label={label || undefined} aria-hidden={label ? undefined : 'true'}>
  {#if state === 'running'}
    <circle cx="8" cy="8" r="7" fill="currentColor" opacity="0.18" /><circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6" /><path d="M6.5 5l4.5 3-4.5 3z" fill="currentColor" />
  {:else if state === 'done'}
    <circle cx="8" cy="8" r="7" fill="currentColor" /><path d="M4.8 8.3l2.2 2.2 4.3-4.6" fill="none" stroke="var(--glyph-ink, #fff)" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" />
  {:else if state === 'cached'}
    <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-dasharray="2.4 1.8" /><path d="M5 8.2l2 2 4-4.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" />
  {:else if state === 'failed'}
    <path d="M5 1.5h6L14.5 5v6L11 14.5H5L1.5 11V5z" fill="currentColor" /><path d="M5.6 5.6l4.8 4.8M10.4 5.6l-4.8 4.8" stroke="var(--glyph-ink, #fff)" stroke-width="1.8" stroke-linecap="round" />
  {:else if state === 'unknown'}
    <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.4" stroke-dasharray="1 2.4" />
  {:else}
    <circle cx="8" cy="8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.6" />
  {/if}
</svg>
<style>
  .g { flex: none; color: var(--fg-muted); --glyph-ink: var(--card-bg); }
  .g.running { color: var(--accent); } .g.done { color: var(--success); } .g.cached { color: var(--info); } .g.failed { color: var(--danger); }
  .g.running circle:nth-child(2) { transform-origin: 8px 8px; animation: pulse 1.4s var(--ease) infinite; }
  @keyframes pulse { 50% { opacity: 0.45; } }
  @media (prefers-reduced-motion: reduce) { .g.running circle:nth-child(2) { animation: none; } }
</style>
