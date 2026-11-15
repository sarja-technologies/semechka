<script lang="ts">
  // This page no longer exists as a place of its own; old bookmarks land where its function lives now.
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  onMount(() => { goto('/chat', { replaceState: true }); });
</script>
<p class="muted" style="padding:24px" role="status">Opening…</p>
