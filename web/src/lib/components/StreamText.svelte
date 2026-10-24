<script lang="ts">
  // A reply that is still arriving. Finished blocks become fixed DOM nodes and are never touched again; only the
  // unfinished tail is re-rendered, at most once per animation frame however fast tokens arrive.
  import { renderMarkdown, stableBoundary, onCodeCopyClick } from '$lib/markdown';
  let { text }: { text: string } = $props();
  let frozen = $state<string[]>([]), tail = $state('');
  let upto = 0, raf = 0;
  function update() {
    raf = 0;
    const t = text;
    if (t.length < upto) { frozen = []; upto = 0; } // a new reply reused this component
    const b = stableBoundary(t, upto);
    if (b > upto) { frozen.push(renderMarkdown(t.slice(upto, b))); upto = b; }
    tail = renderMarkdown(t.slice(upto));
  }
  $effect(() => {
    text; // track
    if (!raf) raf = requestAnimationFrame(update);
  });
  $effect(() => () => { if (raf) cancelAnimationFrame(raf); });
</script>
<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
<div class="prose" data-streaming onclick={onCodeCopyClick}>{#each frozen as h, i (i)}{@html h}{/each}{@html tail}</div>
