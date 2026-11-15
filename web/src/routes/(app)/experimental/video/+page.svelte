<script lang="ts">
  import ExpBack from '$lib/components/ExpBack.svelte';
  import MediaStudio from '$lib/components/media/MediaStudio.svelte';
</script>
<svelte:head><title>Advanced video · Experimental</title></svelte:head>
<div class="head"><ExpBack /><h1>Advanced video</h1></div>
<MediaStudio kind="video" advanced />
<style>.head { padding: 16px 24px 0; max-width: 1440px; width: 100%; margin: 0 auto; display: flex; align-items: center; } @media (max-width: 899px) { .head { padding: 12px 16px 0; } }</style>
