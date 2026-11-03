<script lang="ts">
  // Message box for a session. The text stays in the box until the server accepted the message.
  import { app } from '$lib/session.svelte';
  import Icon from '$lib/components/Icon.svelte';

  let { onsend, disabled = false, reason = '', placeholder = 'Message the session' }:
    { onsend: (text: string) => Promise<boolean>; disabled?: boolean; reason?: string; placeholder?: string } = $props();
  let text = $state(''), busy = $state(false), ta: HTMLTextAreaElement | undefined = $state();
  const cfg = $derived(app.me?.ui?.composer ?? {});
  const max = $derived<number>(cfg.max_chars || 20000);
  const enterSends = $derived(cfg.enter_sends !== false);
  const can = $derived(!disabled && !busy && text.trim().length > 0 && text.length <= max);

  $effect(() => { text; if (ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 220) + 'px'; } });

  export function focus() { ta?.focus(); }
  async function send() {
    if (!can) return;
    busy = true;
    const ok = await onsend(text.trim());
    busy = false;
    if (ok) { text = ''; ta?.focus(); }
  }
  function key(e: KeyboardEvent) {
    if (e.key !== 'Enter' || e.isComposing) return;
    const mod = e.ctrlKey || e.metaKey;
    if (enterSends ? !e.shiftKey : mod) { e.preventDefault(); send(); }
  }
</script>

<form class="comp" onsubmit={(e) => { e.preventDefault(); send(); }}>
  <label class="sr-only" for="wk-msg">Message</label>
  <textarea id="wk-msg" bind:this={ta} bind:value={text} class="ta" rows="1" {placeholder} onkeydown={key} aria-describedby="wk-hint" {disabled}></textarea>
  <button class="send" type="submit" disabled={!can} aria-label="Send message" title={disabled && reason ? reason : 'Send'}><Icon name="send" size={18} /></button>
</form>
<p id="wk-hint" class="tiny muted hint2">
  {#if disabled && reason}{reason}
  {:else if text.length > max}<span class="err">The message is {(text.length - max).toLocaleString()} characters over the limit of {max.toLocaleString()}.</span>
  {:else}{enterSends ? 'Enter sends, Shift+Enter adds a line.' : 'Ctrl+Enter sends, Enter adds a line.'}{/if}
</p>

<style>
  .comp { display: flex; align-items: flex-end; gap: 8px; background: var(--composer-bg); border: 1px solid var(--border-strong); border-radius: var(--radius-composer); padding: 6px 6px 6px 14px; box-shadow: var(--shadow-sm); }
  .comp:focus-within { border-color: var(--accent); }
  .ta { flex: 1; min-width: 0; border: 0; background: transparent; color: var(--fg); font: inherit; resize: none; padding: 7px 0; max-height: 220px; line-height: 1.45; }
  .ta:focus-visible { outline: none; }
  .ta::placeholder { color: var(--fg-muted); }
  .send { flex: none; width: 36px; height: 36px; border-radius: 50%; border: 0; background: var(--btn-primary-bg); color: var(--btn-primary-fg); display: grid; place-items: center; cursor: pointer; }
  .send:disabled { opacity: 0.4; cursor: not-allowed; }
  .send:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .hint2 { margin: 4px 8px 0; }
  @media (pointer: coarse) { .send { width: 44px; height: 44px; } }
</style>
