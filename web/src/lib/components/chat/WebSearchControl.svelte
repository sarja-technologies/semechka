<script lang="ts">
  // Web search for the chosen model (shown only when the model's runtime identity offers it). One button showing the
  // current choice; it opens a short list of the three choices. The choice is saved on the account.
  import { tick } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { toast } from '$lib/toast.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import { WEB_MODES, webMode, setWebMode, type WebMode } from '$lib/chat/websearch.svelte';

  let { disabled = false }: { disabled?: boolean } = $props();
  let open = $state(false), btn: HTMLButtonElement | undefined = $state(), pop: HTMLDivElement | undefined = $state();
  const mode = $derived(webMode());
  const cur = $derived(WEB_MODES.find((m) => m.id === mode)!);

  async function show() {
    if (disabled) return;
    open = true; await tick();
    pop?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus();
  }
  function hide(focus = true) { open = false; if (focus) btn?.focus(); }
  async function choose(v: WebMode) {
    hide();
    if (v === mode) return;
    try { await setWebMode(v); } catch (e) { toast(errText(e), 'bad'); }
  }
  function key(e: KeyboardEvent) {
    const items = [...(pop?.querySelectorAll<HTMLElement>('[role="menuitemradio"]') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); hide(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    else if (e.key === 'Home') { e.preventDefault(); items[0]?.focus(); }
    else if (e.key === 'End') { e.preventDefault(); items[items.length - 1]?.focus(); }
    else if (e.key === 'Tab') hide(false);
  }
  function outside(e: PointerEvent) { if (open && !pop?.contains(e.target as Node) && !btn?.contains(e.target as Node)) hide(false); }
</script>

<svelte:window onpointerdown={outside} />
<span class="ws">
  <button type="button" class="trig" class:off={mode === 'off'} bind:this={btn} {disabled} aria-haspopup="menu" aria-expanded={open}
    aria-label={`Web search: ${cur.label}`} title={`Web search: ${cur.label}. ${cur.note}.`} onclick={() => (open ? hide() : show())} data-testid="web-search">
    <Icon name="globe" size={15} /><span class="pre">Web search:</span><span class="val">{cur.label}</span>
  </button>
  {#if open}
    <!-- svelte-ignore a11y_interactive_supports_focus -->
    <div class="pop pop-in" role="menu" aria-label="Web search" bind:this={pop} onkeydown={key} tabindex="-1">
      {#each WEB_MODES as m (m.id)}
        <button type="button" role="menuitemradio" aria-checked={m.id === mode} class:cur={m.id === mode} onclick={() => choose(m.id)}>
          <span class="ck" aria-hidden="true">{#if m.id === mode}<Icon name="check" size={15} />{/if}</span>
          <span class="tx"><span class="lb">{m.label}</span><span class="nt">{m.note}</span></span>
        </button>
      {/each}
    </div>
  {/if}
</span>

<style>
  .ws { position: relative; display: inline-flex; flex: none; }
  .trig { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 10px; border-radius: 999px;
    border: 1px solid var(--border); font-size: 13px; color: var(--fg-muted); cursor: pointer; white-space: nowrap;
    transition: background var(--fast) var(--ease), color var(--fast) var(--ease); }
  .trig:hover:not(:disabled), .trig[aria-expanded='true'] { color: var(--fg); background: var(--hover); }
  .trig:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .trig:disabled { cursor: default; opacity: 0.6; }
  .val { color: var(--fg); font-weight: 500; } .off .val { color: var(--fg-muted); font-weight: 400; }
  .pop { position: absolute; left: 0; bottom: calc(100% + 6px); z-index: 40; width: 300px; max-width: calc(100vw - 32px); padding: 4px; display: grid;
    background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius-menu); box-shadow: var(--shadow); }
  .pop button { all: unset; box-sizing: border-box; display: grid; grid-template-columns: 20px 1fr; gap: 6px; align-items: start; padding: 8px 10px 8px 6px;
    border-radius: var(--radius-sm); cursor: pointer; color: var(--fg); }
  .pop button:hover, .pop button:focus-visible { background: var(--hover); } .pop button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .ck { display: grid; place-items: center; height: 20px; color: var(--fg); }
  .tx { display: grid; gap: 1px; min-width: 0; } .lb { font-size: 14px; font-weight: 500; } .nt { font-size: 12.5px; color: var(--fg-muted); line-height: 1.4; }
  @media (max-width: 719px) { .pre { display: none; } .trig { padding: 0 8px; } }
  @media (pointer: coarse) { .trig { height: 40px; } .pop button { min-height: 44px; } }
</style>
