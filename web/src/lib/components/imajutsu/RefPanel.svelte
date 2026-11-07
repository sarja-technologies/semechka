<script lang="ts">
  // Panel: References. Add as many pictures as you like and keep them here; the model takes only so many, in the order shown, and every picture says
  // plainly whether it is Used in this run or Not used. A role (what the picture is for) becomes one sentence in the prompt; that sentence is shown.
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import MediaAssetPicker from '$lib/components/media/MediaAssetPicker.svelte';
  import PanelCard from './PanelCard.svelte';
  import { openViewer } from '$lib/media/viewer.svelte';
  import type { Asset } from '$lib/media/assets';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s, n = 0 }: { s: Imajutsu; n?: number } = $props();

  const refs = $derived(s.spec.references), max = $derived(s.slots), req = $derived(s.needs.refs === 'required');
  const pending = $derived(s.uploads.filter((u) => s.targetOf(u) === 'ref' && u.status !== 'ready'));
  const roles = $derived(s.roles);
  let pickOpen = $state(false), input: HTMLInputElement | undefined = $state(), live = $state(''), over = $state<number | null>(null), dragFrom = $state<number | null>(null);
  const noun = (k: number) => `${k} picture${k === 1 ? '' : 's'}`;
  const canAuto = $derived(max > 0 && refs.length > max && (refs.slice(0, max).some((r) => !r.active) || refs.slice(max).some((r) => r.active)));
  const sentences = $derived(s.plan?.prompt?.sentences ?? []);
  const sentenceFor = (i: number) => sentences.find((x) => x.position === i + 1 && x.ref !== 'source')?.sentence ?? '';
  const label = (i: number) => s.refAsset(refs[i])?.name ?? `Picture ${i + 1}`;

  function chosen(e: Event) { const i = e.currentTarget as HTMLInputElement; if (i.files) s.addFiles([...i.files], 'ref'); i.value = ''; }
  function toggle(i: number, on: boolean) { s.setActive(i, on); live = on ? `Picture ${i + 1} is switched on.` : `Picture ${i + 1} is switched off.`; }
  function relocate(i: number, to: number) { if (to < 0 || to >= refs.length) return; s.moveRef(i, to); live = `Moved to position ${to + 1} of ${refs.length}.`; requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-ref-index="${to}"]`)?.focus()); }
  function key(e: KeyboardEvent, i: number) {
    if (!e.altKey) return;
    const d = e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : 0;
    if (d) { e.preventDefault(); relocate(i, i + d); }
  }
  const open = (i: number) => { const list = refs.map((r) => s.refAsset(r)).filter((x): x is Asset => !!x), a = s.refAsset(refs[i]); if (a) openViewer(a, { siblings: list }); };
  function dstart(e: DragEvent, i: number) { dragFrom = i; e.dataTransfer?.setData('application/x-semechka-imajutsu-ref', String(i)); if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'; }
  function dover(e: DragEvent, i: number) { if (dragFrom === null || !e.dataTransfer?.types.includes('application/x-semechka-imajutsu-ref')) return; e.preventDefault(); over = i; }
  function ddrop(e: DragEvent, i: number) { if (dragFrom === null) return; e.preventDefault(); e.stopPropagation(); const from = dragFrom; dragFrom = over = null; if (from !== i) { s.moveRef(from, i); live = `Moved to position ${i + 1} of ${refs.length}.`; } }
</script>

<PanelCard id="references" title="Reference pictures" {n}
  hint={s.needs.refs === 'none' ? 'This mode does not use reference pictures. Yours stay here for other modes.' : s.total > 0 ? `Pictures show the model what to use. Add as many as you like and keep them here. This model takes ${noun(s.total)}${s.plan?.references?.source_counts ? ', and your own picture counts as one of them' : ''}, in the order shown.` : 'Pictures show the model what to use. Add as many as you like and keep them here.'}>
  <div class="st-row bar">
    <button type="button" class="btn" onclick={() => input?.click()}><Icon name="plus" size={15} /> Add pictures</button>
    <button type="button" class="btn" onclick={() => (pickOpen = true)}><Icon name="library" size={15} /> From your Library</button>
    {#if canAuto}<button type="button" class="btn ghost" onclick={() => s.autoFillRefs()}>Use the first {noun(Math.min(max, refs.length))}</button>{/if}
    <input bind:this={input} type="file" accept="image/*" multiple hidden onchange={chosen} data-testid="ref-file" />
    <span class="count tiny" role="status" data-testid="ref-count">{#if !refs.length}No pictures yet{:else if s.plan?.references && s.plan.references.used.length + s.plan.references.not_used.length === refs.length}{s.used} of {refs.length} used in this run{:else}{refs.length} added{/if}</span>
  </div>
  {#if req && refs.length && s.plan?.references && s.used < 1}<p class="st-note warn" role="status" data-testid="ref-min">{s.mode?.label} needs at least one reference picture that is used.</p>{/if}
  {#if max > 0 && refs.length > max}<p class="st-note" role="status" data-testid="ref-over">{refs.length} pictures added, but this model uses {max}. The first {max} that are switched on are sent; the others are kept here and do not affect the result.</p>{/if}
  {#if s.needs.refs !== 'none' && !roles.length && refs.length}<p class="st-note">This server offers no roles for pictures: every used picture guides the result in the same way.</p>{/if}

  <ul class="gal" aria-label="Reference pictures" data-testid="ref-gallery">
    {#each refs as r, i (r.asset + ':' + i)}
      {@const st = s.refView(i)}
      {@const used = st.state === 'used'}
      {@const sent = used ? sentenceFor(i) : ''}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
      <li class="tile" class:off={st.state === 'unused'} class:over={over === i} class:drag={dragFrom === i} data-ref-index={i} tabindex="0" draggable="true"
        aria-label={`${label(i)}. ${used ? `Used in this run, number ${st.order}.` : st.state === 'unused' ? st.note : 'Working it out.'} Alt plus arrow keys moves it.`}
        ondragstart={(e) => dstart(e, i)} ondragend={() => (dragFrom = over = null)} ondragover={(e) => dover(e, i)} ondragleave={() => over === i && (over = null)} ondrop={(e) => ddrop(e, i)} onkeydown={(e) => key(e, i)}>
        <div class="pic">
          <button type="button" class="view" onclick={() => open(i)} aria-label={`Preview ${label(i)}`}><MediaThumb id={r.asset} kind="image" name={label(i)} ratio="1 / 1" /></button>
          {#if used}<span class="num" aria-hidden="true">{st.order}</span>{/if}
          <button type="button" class="rm" onclick={() => s.removeRef(i)} aria-label={`Remove ${label(i)}`} title="Remove from this project"><Icon name="x" size={12} /></button>
          <span class="mv">
            <button type="button" onclick={() => relocate(i, i - 1)} disabled={i === 0} aria-label={`Move ${label(i)} earlier`}><Icon name="chevron" size={13} /></button>
            <button type="button" onclick={() => relocate(i, i + 1)} disabled={i === refs.length - 1} aria-label={`Move ${label(i)} later`}><Icon name="chevron" size={13} /></button>
          </span>
        </div>
        <div class="meta">
          <span class="st-badge wrap" class:ok={used} data-testid={used ? 'badge-used' : st.state === 'unused' ? 'badge-unused' : 'badge-pending'}>{used ? `Used · ${st.order}` : st.note}</span>
          <label class="use"><input type="checkbox" checked={r.active} onchange={(e) => toggle(i, e.currentTarget.checked)} aria-label={`Use ${label(i)} in this run`} /> <span>Use</span></label>
          {#if roles.length}
            <label class="fld"><span class="sr-only">Role of {label(i)}</span>
              <select class="input" value={r.role ?? ''} aria-label={`Role of ${label(i)}`} data-testid="ref-role" onchange={(e) => s.edit((sp) => { sp.references[i].role = e.currentTarget.value || null; })}>
                <option value="">{s.spec.mode === 'replace_subject' ? 'The new subject' : 'No role'}</option>
                {#each roles as ro (ro.id)}<option value={ro.id}>{ro.label}</option>{/each}
              </select></label>
          {/if}
          <input class="input" value={r.preserve} placeholder="Keep (optional): the red scarf" maxlength="200" aria-label={`What to keep from ${label(i)}`} data-testid="ref-keep" oninput={(e) => s.edit((sp) => { sp.references[i].preserve = e.currentTarget.value; })} />
          <input class="input" value={r.label} placeholder="Label (optional)" maxlength="80" aria-label={`Label for ${label(i)}`} oninput={(e) => s.edit((sp) => { sp.references[i].label = e.currentTarget.value; })} />
          {#if used}<p class="sentence" data-testid="ref-sentence">{#if sent}<span class="tiny muted">In the prompt:</span> {sent}{:else}<span class="tiny muted">{s.planBusy ? 'Writing the sentence…' : 'No sentence.'}</span>{/if}</p>{/if}
          {#if st.state === 'unused'}<p class="tiny muted">Kept here. It does not change the result.</p>{/if}
        </div>
      </li>
    {/each}
    {#each pending as u (u.key)}
      <li class="tile pend">
        <div class="pic"><MediaThumb kind="image" src={u.local} name={u.file.name} badge={false} ratio="1 / 1" />
          <div class="pg" role="progressbar" aria-label={`Uploading ${u.file.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(u.fraction * 100)}>
            {#if u.status === 'error'}<span>{u.error}</span>{:else}<span>{u.status === 'processing' ? 'Preparing…' : `${Math.round(u.fraction * 100)}%`}</span>{/if}
          </div>
          <button type="button" class="rm" onclick={() => s.dismissUpload(u)} aria-label={`Cancel ${u.file.name}`}><Icon name="x" size={12} /></button>
        </div>
        {#if u.status === 'error'}<button type="button" class="btn sm" onclick={() => s.retryUpload(u)}>Try again</button>{/if}
      </li>
    {/each}
    <li class="addtile"><button type="button" class="add" onclick={() => input?.click()}><Icon name="plus" size={20} /><strong>Add pictures</strong><span>Click, drop or paste</span></button></li>
  </ul>
  <span class="sr-only" role="status" aria-live="polite">{live}</span>
</PanelCard>
<MediaAssetPicker bind:open={pickOpen} accept="image" title="Add reference pictures" onpick={(l) => s.addRefs(l)} />

<style>
  .bar { margin-bottom: 0; } .count { margin-left: auto; color: var(--fg-muted); font-variant-numeric: tabular-nums; }
  .gal { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 14px 12px; }
  .tile { display: grid; gap: 6px; align-content: start; outline: none; transition: opacity var(--fast) var(--ease); min-width: 0; }
  .tile:focus-visible .pic { box-shadow: 0 0 0 2px var(--m-ring); }
  .pic { position: relative; aspect-ratio: 1; border-radius: 12px; overflow: hidden; background: var(--m-card); border: 1px solid var(--border); transition: box-shadow var(--fast) var(--ease), transform var(--fast) var(--ease); }
  .tile.off .pic { border-style: dashed; } .tile.off .view { opacity: .5; }
  .tile.over .pic { box-shadow: 0 0 0 2px var(--m-ring); transform: translateX(3px); } .tile.drag { opacity: .45; }
  .view { all: unset; display: block; width: 100%; height: 100%; cursor: zoom-in; } .view:focus-visible { outline: 2px solid var(--m-ring); outline-offset: -2px; }
  .num { position: absolute; left: 6px; top: 6px; min-width: 22px; height: 22px; padding: 0 6px; border-radius: 11px; display: grid; place-items: center; font: 600 12px var(--font); background: rgba(14,15,16,.82); color: #fff; font-variant-numeric: tabular-nums; }
  .rm, .mv button { all: unset; box-sizing: border-box; width: 26px; height: 26px; border-radius: 7px; display: grid; place-items: center; cursor: pointer; color: #fff; background: rgba(14,15,16,.72); backdrop-filter: blur(4px); }
  .rm { position: absolute; right: 6px; top: 6px; } .rm:hover, .mv button:hover:not(:disabled) { background: rgba(14,15,16,.94); }
  .rm:focus-visible, .mv button:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 1px; }
  .mv { position: absolute; left: 6px; right: 6px; bottom: 6px; display: flex; justify-content: space-between; opacity: 0; transition: opacity var(--fast) var(--ease); pointer-events: none; }
  .mv button { pointer-events: auto; } .mv button:disabled { opacity: .3; cursor: default; } .mv button:first-child :global(svg) { transform: scaleX(-1); }
  .tile:hover .mv, .tile:focus-within .mv { opacity: 1; }
  @media (pointer: coarse) { .mv { opacity: 1; } .rm, .mv button { width: 44px; height: 44px; } .use { min-height: 44px; } }
  .meta { display: grid; gap: 5px; justify-items: start; min-width: 0; } .meta :global(.input) { min-height: 32px; font-size: 12.5px; }
  .wrap { white-space: normal; border-radius: 10px; line-height: 1.3; padding-block: 2px; }
  .use { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; min-height: 32px; cursor: pointer; } .fld { width: 100%; } .meta input.input { width: 100%; }
  .sentence { margin: 0; font-size: 12px; line-height: 1.4; color: var(--fg-2); overflow-wrap: anywhere; }
  .addtile { display: grid; } .add { all: unset; box-sizing: border-box; display: grid; place-content: center; justify-items: center; gap: 3px; min-height: 150px; aspect-ratio: 1; border-radius: 12px; border: 1.5px dashed var(--border-strong); cursor: pointer; color: var(--fg-muted); text-align: center; padding: 8px;
    transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease); }
  .add strong { color: var(--fg); font-size: 13px; } .add span { font-size: 11.5px; } .add:hover { background: var(--m-ring-soft); border-color: var(--m-ring); } .add:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .pg { position: absolute; inset: auto 0 0 0; padding: 14px 8px 8px; font-size: 11.5px; color: #fff; background: linear-gradient(to top, rgba(10,11,12,.85), transparent); }
</style>
