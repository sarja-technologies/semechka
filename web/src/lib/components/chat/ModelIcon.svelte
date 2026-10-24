<script lang="ts">
  // A model's icon: the administrator's upload, else the developer's bundled logo (static/model-logos, only marks whose
  // owners allow this use; see docs/MODEL_ICONS.md), else a neutral lettermark. One-colour logos are drawn as a mask
  // in the text colour so they read on light and dark; a logo with its own dark-surface variant swaps to it.
  type Ic = { asset?: string; logo?: string; mask?: boolean; dark?: string; mark?: string | null; tone?: string | null };
  let { icon, label = '', size = 20 }: { icon?: Ic | null; label?: string; size?: number } = $props();
  const mark = $derived(icon?.mark || (label.trim()[0] ?? '?').toUpperCase());
</script>
{#if icon?.asset}
  <img class="mi" src={`/api/assets/${icon.asset}`} alt="" width={size} height={size} style:width="{size}px" style:height="{size}px" />
{:else if icon?.logo && icon.mask}
  <span class="mi mask" aria-hidden="true" style:width="{size}px" style:height="{size}px" style:--logo="url({icon.logo})"></span>
{:else if icon?.logo}
  <span class="mi" class:swap={!!icon.dark} aria-hidden="true" style:width="{size}px" style:height="{size}px">
    <img class="lg" src={icon.logo} alt="" width={size} height={size} />
    {#if icon.dark}<img class="lg dk" src={icon.dark} alt="" width={size} height={size} />{/if}
  </span>
{:else}
  <span class="mi mark" aria-hidden="true" style:width="{size}px" style:height="{size}px" style:font-size="{Math.round(size * (mark.length > 1 ? 0.42 : 0.55))}px"
    style:background={icon?.tone || 'var(--surface-2)'} class:plain={!icon?.tone}>{mark}</span>
{/if}
<style>
  .mi { flex: none; border-radius: calc(var(--radius-sm) * 0.75); object-fit: contain; display: inline-grid; place-items: center; }
  .lg { width: 100%; height: 100%; object-fit: contain; grid-area: 1 / 1; }
  .dk { display: none; }
  :global([data-variant='dark']) .swap .lg:not(.dk) { display: none; }
  :global([data-variant='dark']) .swap .dk { display: block; }
  .mask { background: var(--fg); -webkit-mask: var(--logo) center / contain no-repeat; mask: var(--logo) center / contain no-repeat; border-radius: 0; }
  .mark { color: #fff; font-weight: 600; letter-spacing: -0.02em; line-height: 1; font-family: var(--font); }
  .mark.plain { color: var(--fg-muted); border: 1px solid var(--border); }
</style>
