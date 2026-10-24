<script lang="ts">
  import { renderMarkdown } from '$lib/markdown';
  let { text }: { text: string } = $props();
  const html = $derived(renderMarkdown(text));
</script>
<div class="prose">{@html html}</div>
