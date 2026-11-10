<script lang="ts">
  import { goto } from '$app/navigation';
  import { app } from '$lib/session.svelte';
  import { homeHref } from '$lib/nav';
  $effect(() => { if (app.me?.ui) goto(homeHref(app.me.ui), { replaceState: true }); });
</script>
<p class="muted" style="padding:24px" role="status">Opening…</p>
