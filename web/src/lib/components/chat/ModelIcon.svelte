<script lang="ts">
  // A model's icon: the administrator's upload when there is one, otherwise a neutral lettermark for the provider.
  // Third-party logos are not bundled (their brand-asset terms do not allow redistribution in an open-source,
  // white-label product); see docs/MODEL_ICONS.md.
  let { icon, label = '', size = 20 }: { icon?: { asset?: string; mark?: string | null; tone?: string | null } | null; label?: string; size?: number } = $props();
  const mark = $derived(icon?.mark || (label.trim()[0] ?? '?').toUpperCase());
</script>
{#if icon?.asset}
  <img class="mi" src={`/api/assets/${icon.asset}`} alt="" width={size} height={size} style:width="{size}px" style:height="{size}px" />
{:else}
  <span class="mi mark" aria-hidden="true" style:width="{size}px" style:height="{size}px" style:font-size="{Math.round(size * (mark.length > 1 ? 0.42 : 0.55))}px"
    style:background={icon?.tone || 'var(--surface-2)'} class:plain={!icon?.tone}>{mark}</span>
{/if}
<style>
  .mi { flex: none; border-radius: calc(var(--radius-sm) * 0.75); object-fit: contain; display: inline-grid; place-items: center; }
  .mark { color: #fff; font-weight: 600; letter-spacing: -0.02em; line-height: 1; font-family: var(--font); }
  .mark.plain { color: var(--fg-muted); border: 1px solid var(--border); }
</style>
