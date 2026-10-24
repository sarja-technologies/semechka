<script lang="ts">
  import { renderMarkdown } from '$lib/markdown';
  let { text }: { text: string } = $props();
  const html = $derived(renderMarkdown(text));
  // Code blocks carry a native <button class="copy-code">; one delegated listener handles them all.
  async function onclick(e: MouseEvent) {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('button.copy-code');
    if (!btn) return;
    const code = btn.closest('.codeblock')?.querySelector('code')?.textContent ?? '';
    try { await navigator.clipboard.writeText(code); btn.textContent = 'Copied'; }
    catch { btn.textContent = 'Copy unavailable'; }
    setTimeout(() => { btn.textContent = 'Copy'; }, 1600);
  }
</script>
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="prose" {onclick}>{@html html}</div>
