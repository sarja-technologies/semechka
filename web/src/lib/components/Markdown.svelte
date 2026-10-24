<script lang="ts">
  import { renderCached, onCodeCopyClick } from '$lib/markdown';
  let { text }: { text: string } = $props();
  const html = $derived(renderCached(text));
</script>
<!-- Code blocks carry a native <button class="copy-code">; one delegated listener handles them all. -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="prose" onclick={onCodeCopyClick}>{@html html}</div>
