<script lang="ts">
  // Turns the area around the composer into a drop target. Files dragged anywhere over the window light the target softly; over the target itself
  // it firms up. Dropping anywhere in the app attaches (and never lets the browser open the file instead). Also handles pasted pictures.
  import type { Snippet } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';

  let { onfiles, disabled = false, hint = 'Drop pictures or videos to attach them', sub = '', children }:
    { onfiles: (files: File[]) => void; disabled?: boolean; hint?: string; sub?: string; children: Snippet } = $props();
  let depth = $state(0), here = $state(false), count = $state(0);
  const armed = $derived(depth > 0 && !disabled);
  const hasFiles = (e: DragEvent) => !!e.dataTransfer && [...e.dataTransfer.types].includes('Files');

  function enter(e: DragEvent) { if (!hasFiles(e)) return; depth++; count = e.dataTransfer?.items?.length ?? 0; }
  function leave(e: DragEvent) { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) here = false; }
  function over(e: DragEvent) {
    if (!hasFiles(e)) return;
    e.preventDefault(); // without this the browser would open the file in this tab
    if (e.dataTransfer) e.dataTransfer.dropEffect = disabled ? 'none' : 'copy';
  }
  function drop(e: DragEvent) {
    if (!hasFiles(e)) return;
    e.preventDefault(); depth = 0; here = false;
    if (!disabled && e.dataTransfer?.files.length) onfiles([...e.dataTransfer.files]);
  }
  function paste(e: ClipboardEvent) {
    if (disabled) return;
    const files = [...(e.clipboardData?.files ?? [])].filter((f) => /^(image|video)\//.test(f.type));
    if (files.length) { e.preventDefault(); onfiles(files.map((f, i) => (f.name && f.name !== 'image.png' ? f : new File([f], `Pasted picture ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}${files.length > 1 ? ` ${i + 1}` : ''}.${f.type.split('/')[1] || 'png'}`, { type: f.type }))));
    }
  }
</script>

<svelte:window ondragenter={enter} ondragleave={leave} ondragover={over} ondrop={drop} onpaste={paste} />

<div class="dz" class:armed class:here>
  {@render children()}
  {#if armed}
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="veil" ondragenter={() => (here = true)} ondragleave={(e) => { if (!(e.currentTarget as HTMLElement).contains(e.relatedTarget as Node)) here = false; }} aria-hidden="true">
      <div class="msg"><span class="ico"><Icon name="upload" size={18} /></span><strong>{hint}</strong>{#if sub}<span>{sub}</span>{/if}</div>
    </div>
  {/if}
</div>

<style>
  .dz { position: relative; min-width: 0; border-radius: calc(var(--radius-composer) + 4px); }
  .veil { position: absolute; inset: -6px; z-index: 30; border-radius: calc(var(--radius-composer) + 6px); display: grid; place-items: center; text-align: center;
    background: color-mix(in srgb, var(--bg) 93%, transparent); backdrop-filter: blur(5px); border: 2px dashed color-mix(in srgb, var(--m-ring) 70%, transparent);
    animation: veilin var(--medium) var(--ease); transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease); }
  .here .veil { background: color-mix(in srgb, var(--m-ring) 11%, var(--bg) 84%); border-color: var(--m-ring); border-style: solid; box-shadow: 0 0 0 4px var(--m-ring-soft); }
  .msg { display: grid; gap: 4px; justify-items: center; color: var(--fg); font-size: 13px; pointer-events: none; }
  .msg span:not(.ico) { color: var(--fg-muted); font-size: 12px; }
  .ico { width: 36px; height: 36px; border-radius: 50%; display: grid; place-items: center; background: var(--m-ring-soft); color: var(--m-ring); margin-bottom: 2px; transition: transform var(--medium) var(--ease); }
  .here .ico { transform: translateY(-2px) scale(1.08); }
  @keyframes veilin { from { opacity: 0; transform: scale(0.99); } to { opacity: 1; transform: none; } }
  @media (prefers-reduced-motion: reduce) { .veil { animation: none; } .ico { transition: none; } }
</style>
