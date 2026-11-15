<script lang="ts">
  import { renderCached, onCodeCopyClick, linkCitations, type Cite } from '$lib/markdown';
  // cites: the sources a reply's [n] markers refer to; a marker becomes a link only when its source is here
  let { text, cites = [] }: { text: string; cites?: Cite[] } = $props();
  const html = $derived(cites.length ? linkCitations(renderCached(text), cites) : renderCached(text));
</script>
<!-- Code blocks carry a native <button class="copy-code">; one delegated listener handles them all. -->
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="prose" onclick={onCodeCopyClick}>{@html html}</div>
