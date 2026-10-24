<script lang="ts">
  // Pictures are made inside conversations now; the old address opens a new one ready to make a picture.
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  onMount(() => { goto(`/chat?new=${Date.now()}&op=image`, { replaceState: true }); });
</script>
<p class="muted" style="padding:24px" role="status">Opening…</p>
