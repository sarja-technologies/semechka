<script lang="ts">
  // Videos are made inside conversations now; the old address opens a new one ready to make a video.
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  onMount(() => { goto(`/chat?new=${Date.now()}&op=video`, { replaceState: true }); });
</script>
<p class="muted" style="padding:24px" role="status">Opening…</p>
