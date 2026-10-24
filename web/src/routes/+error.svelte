<script lang="ts">
  import { page } from '$app/state';
  import AuthFrame from '$lib/components/AuthFrame.svelte';
  const missing = $derived(page.status === 404);
</script>
<AuthFrame title={missing ? 'Page not found' : 'Something went wrong'}>
  <p class="small muted">{missing ? 'There is no page at this address. It may have moved, or the link may be mistyped.' : 'This page could not be shown. Reloading usually helps; if it keeps happening, tell an administrator.'}</p>
  <p class="small" style="margin-top:16px"><a href="/">Back to the start</a></p>
</AuthFrame>
