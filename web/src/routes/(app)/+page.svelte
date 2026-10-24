<script lang="ts">
  import { goto } from '$app/navigation';
  import { app } from '$lib/session.svelte';
  $effect(() => { const h = app.me?.ui.home; if (h) goto(h === 'usage' ? '/usage' : h === 'chat' ? '/chat' : h === 'help' ? '/help' : `/unavailable/${h}`, { replaceState: true }); });
</script>
<p class="muted" style="padding:24px">Opening…</p>
