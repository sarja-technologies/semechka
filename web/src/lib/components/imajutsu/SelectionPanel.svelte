<script lang="ts">
  // Panel: Selection. Says WHERE; the words in the prompt say WHAT. Opens the selection editor on the stage. For Outpaint it shows the new area instead.
  import Icon from '$lib/components/Icon.svelte';
  import PanelCard from './PanelCard.svelte';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s, n = 0 }: { s: Imajutsu; n?: number } = $props();
  const area = $derived(s.needs.selection === 'area'), req = $derived(s.needs.selection === 'required'), has = $derived(s.hasSel), sel = $derived(s.spec.selection);
  function edit() { s.stage = 'selection'; s.panel = 'selection'; document.getElementById('stage')?.scrollIntoView({ block: 'nearest' }); }
  const marks = $derived(s.maskDoc?.ops.length ?? 0);
</script>

<PanelCard id="selection" title={area ? 'New area' : 'Selection'} {n}
  hint={area ? 'The model fills the part of the new picture your picture does not cover.' : 'The selection tells the model WHERE to change. The words you wrote tell it WHAT.'}>
  {#if area}
    <button type="button" class="btn" onclick={() => { s.stage = 'picture'; }} data-testid="show-area">Show the new area</button>
    <p class="st-note">There is nothing to paint: the area is worked out from the size you choose under Size.</p>
      <label class="keep"><input type="checkbox" checked={s.keepOutside} onchange={(e) => s.edit((x) => { x.selection.keep_outside = e.currentTarget.checked; })} data-testid="keep-outside" />
        <span><strong>Also make a copy that keeps everything outside the selection exactly as it was</strong> <span class="st-note">When the run finishes, a second picture is made: the model's picture inside the selection, your original everywhere else. That is a copy of your own pixels, not AI, and the model's own picture is kept too.</span></span></label>
  {:else}
    {#if !s.source}<p class="st-note warn" role="status">Add your picture first. The selection is drawn on it.</p>{/if}
    <div class="st-row">
      <button type="button" class="btn" onclick={edit} disabled={!s.source} data-testid="open-mask"><Icon name="brush" size={15} /> {has ? 'Edit the selection' : 'Paint a selection'}</button>
      {#if has}<span class="st-badge ok" data-testid="mask-badge">{s.maskBusy ? 'Saving the selection…' : s.maskStale ? 'Selection changed, not saved yet' : 'Selection saved'}</span>{/if}
    </div>
    <p class="st-note" data-testid="selection-note">{#if has}{marks ? `${marks} mark${marks === 1 ? '' : 's'}` : 'A saved selection'}{sel.invert ? ', inverted' : ''}{sel.feather ? `, soft edge ${sel.feather} px` : ''}. It is saved with the project and comes back when you reload.{:else if req}A selection is needed. Paint it with the brush, or drag a box.{:else}Optional. Without one, the words alone say what to replace.{/if}</p>
    {#if ['replace_subject', 'inpaint'].includes(s.spec.mode) && has}
      <label class="keep"><input type="checkbox" checked={s.keepOutside} onchange={(e) => s.edit((x) => { x.selection.keep_outside = e.currentTarget.checked; })} data-testid="keep-outside" />
        <span><strong>Also make a copy that keeps everything outside the selection exactly as it was</strong> <span class="st-note">When the run finishes, a second picture is made: the model's picture inside the selection, your original everywhere else. That is a copy of your own pixels, not AI, and the model's own picture is kept too.</span></span></label>
    {/if}
  {/if}
</PanelCard>
<style>.keep { display: flex; gap: 10px; align-items: flex-start; font-size: 13px; cursor: pointer; } .keep input { margin-top: 3px; width: 18px; height: 18px; flex: none; }</style>
