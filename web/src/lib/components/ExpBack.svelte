<script lang="ts">
  // Where the standalone Imajutsu and OpenJutsu screens belong: under Experimental. One quiet way back.
  import Icon from '$lib/components/Icon.svelte';
</script>
<a class="xb" href="/experimental"><span class="fl"><Icon name="chevron" size={13} /></span>Experimental</a>
<style>
  .xb { display: inline-flex; align-items: center; gap: 2px; font-size: 13px; color: var(--fg-muted); text-decoration: none; margin-right: 10px; padding: 2px 6px 2px 2px; border-radius: var(--radius-sm); }
  .xb:hover { color: var(--fg); background: var(--hover); text-decoration: none; } .xb:focus-visible { outline: 2px solid var(--accent); }
  .fl { display: grid; transform: rotate(180deg); }
</style>
