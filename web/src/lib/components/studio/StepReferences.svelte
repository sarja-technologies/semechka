<script lang="ts">
  // Step 3: reference pictures. Add as many as you like; the operation decides how many are really used. Each picture says plainly whether it is
  // used in this run (and in which place) or only kept: a picture that is not used never changes the result.
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import MediaAssetPicker from '$lib/components/media/MediaAssetPicker.svelte';
  import { openViewer } from '$lib/media/viewer.svelte';
  import type { Asset } from '$lib/media/assets';
  import type { Studio } from '$lib/studio/store.svelte';

  let { s }: { s: Studio } = $props();
  const refs = $derived(s.spec.references);
  const max = $derived(s.policy.active_max), min = $derived(s.policy.min);
  const known = $derived(!!s.mode);
  const pending = $derived(s.uploads.filter((u) => !(/^video\//.test(u.file.type) || /\.(mp4|m4v|mov|webm|mkv|avi)$/i.test(u.file.name))));
  let pickOpen = $state(false), input: HTMLInputElement | undefined = $state(), live = $state(''), over = $state<number | null>(null), dragFrom = $state<number | null>(null);
  const canAuto = $derived(known && max > 0 && refs.length > 1 && (refs.slice(0, max).some((r) => !r.active) || refs.slice(max).some((r) => r.active)));
  const noun = (n: number) => `${n} picture${n === 1 ? '' : 's'}`;

  function chosen(e: Event) { const i = e.currentTarget as HTMLInputElement; if (i.files) s.addFiles([...i.files]); i.value = ''; }
  function toggle(i: number, on: boolean, el: HTMLInputElement) {
    const m = s.setActive(i, on);
    live = m || (on ? `Picture ${i + 1} is now used.` : `Picture ${i + 1} is now not used.`);
    el.checked = known ? s.states[i].used : s.spec.references[i].active;   // a refused switch-on must not stay ticked on screen
  }
  function relocate(i: number, to: number) { if (to < 0 || to >= refs.length) return; s.moveRef(i, to); live = `Moved to position ${to + 1} of ${refs.length}.`; requestAnimationFrame(() => document.querySelector<HTMLElement>(`[data-ref-index="${to}"]`)?.focus()); }
  function key(e: KeyboardEvent, i: number) {
    if (!e.altKey) return;
    const d = e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : 0;
    if (d) { e.preventDefault(); relocate(i, i + d); }
  }
  const open = (i: number) => {
    const list = refs.map((r) => s.refAsset(r)).filter((x): x is Asset => !!x), a = s.refAsset(refs[i]);
    if (a) openViewer(a, { siblings: list });
  };
  function dstart(e: DragEvent, i: number) { dragFrom = i; e.dataTransfer?.setData('application/x-semechka-studio-ref', String(i)); if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move'; }
  function dover(e: DragEvent, i: number) { if (dragFrom === null || !e.dataTransfer?.types.includes('application/x-semechka-studio-ref')) return; e.preventDefault(); over = i; }
  function ddrop(e: DragEvent, i: number) { if (dragFrom === null) return; e.preventDefault(); e.stopPropagation(); const from = dragFrom; dragFrom = over = null; if (from !== i) { s.moveRef(from, i); live = `Moved to position ${i + 1} of ${refs.length}.`; } }
  const label = (i: number) => s.refAsset(refs[i])?.name ?? `Picture ${i + 1}`;
</script>

<div class="st-h">
  <h2>Reference pictures</h2>
  <p>Pictures show the model what to put into the video. Add as many as you like and keep them here.
    {#if known}{s.mode?.label} uses {noun(max)} at most, in the order shown.{:else}Choose the operation (step 4) to see how many are used.{/if}</p>
</div>

<div class="st-row bar">
  <button type="button" class="btn" onclick={() => input?.click()}><Icon name="plus" size={15} /> Add pictures</button>
  <button type="button" class="btn" onclick={() => (pickOpen = true)}><Icon name="library" size={15} /> From your Library</button>
  {#if canAuto}<button type="button" class="btn ghost" onclick={() => s.autoFillRefs()} title="Use the first pictures and leave the rest out">Use the first {noun(Math.min(max, refs.length))}</button>{/if}
  <input bind:this={input} type="file" accept="image/*" multiple hidden onchange={chosen} />
  <span class="count tiny" role="status" data-testid="ref-count">{#if !refs.length}No pictures yet{:else if known}{s.used} of {refs.length} used in this run{:else}{refs.length} added{/if}</span>
</div>

{#if known && min > 0 && s.used < min}
  <p class="st-note warn" role="status" data-testid="ref-min">{s.mode?.label} needs {min === 1 ? 'a picture' : `at least ${noun(min)}`} that is marked Used. {s.used} {s.used === 1 ? 'is' : 'are'} used now.</p>
{/if}
{#if known && max === 0}<p class="st-note" role="status">{s.mode?.label} does not use pictures. Yours stay here for other operations.</p>{/if}

<ul class="gal" aria-label="Reference pictures" data-testid="ref-gallery">
  {#each refs as r, i (r.asset + ':' + i)}
    {@const st = s.states[i]}
    {@const a = s.refAsset(r)}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
    <li class="tile" class:off={known && !st.used} class:over={over === i} class:drag={dragFrom === i} data-ref-index={i} tabindex="0" draggable="true"
      aria-label={`${label(i)}. ${known ? (st.used ? `Used in this run, number ${st.order}.` : st.note + '.') : r.active ? 'Active.' : 'Not active.'} Alt plus arrow keys moves it.`}
      ondragstart={(e) => dstart(e, i)} ondragend={() => (dragFrom = over = null)} ondragover={(e) => dover(e, i)} ondragleave={() => over === i && (over = null)} ondrop={(e) => ddrop(e, i)} onkeydown={(e) => key(e, i)}>
      <div class="pic">
        <button type="button" class="view" onclick={() => open(i)} aria-label={`Preview ${label(i)}`}><MediaThumb id={r.asset} kind="image" name={label(i)} ratio="1 / 1" /></button>
        {#if known && st.used}<span class="num" aria-hidden="true">{st.order}</span>{/if}
        <button type="button" class="rm" onclick={() => s.removeRef(i)} aria-label={`Remove ${label(i)}`} title="Remove from this project"><Icon name="x" size={12} /></button>
        <span class="mv">
          <button type="button" onclick={() => relocate(i, i - 1)} disabled={i === 0} aria-label={`Move ${label(i)} earlier`}><Icon name="chevron" size={13} /></button>
          <button type="button" onclick={() => relocate(i, i + 1)} disabled={i === refs.length - 1} aria-label={`Move ${label(i)} later`}><Icon name="chevron" size={13} /></button>
        </span>
      </div>
      <div class="meta">
        {#if known}
          <span class="st-badge wrap" class:ok={st.used} data-testid={st.used ? 'badge-used' : 'badge-unused'}>{st.used ? `Used in this run · ${st.order}` : st.note}</span>
        {:else}<span class="st-badge">{r.active ? 'Active' : 'Off'}</span>{/if}
        <label class="use"><input type="checkbox" checked={known ? st.used : r.active} onchange={(e) => toggle(i, e.currentTarget.checked, e.currentTarget)} aria-label={`Use ${label(i)} in this run`} /> <span>Use</span></label>
        <input class="input lbl" value={r.label} placeholder="Label (optional)" maxlength="80" aria-label={`Label for ${label(i)}`} oninput={(e) => s.edit((sp) => { sp.references[i].label = e.currentTarget.value; })} />
        {#if known && st.used && max > 1}
          <label class="slot"><span class="sr-only">Which target in the video {label(i)} stands for</span>
            <select class="input" value={r.subject ?? ''} aria-label={`Which target ${label(i)} replaces`} onchange={(e) => s.edit((sp) => { sp.references[i].subject = e.currentTarget.value ? Number(e.currentTarget.value) : null; })}>
              <option value="">Replaces: in order ({st.order})</option>
              {#each Array.from({ length: max }, (_, k) => k + 1) as k (k)}<option value={k}>Replaces: target {k}</option>{/each}
            </select>
          </label>
        {/if}
      </div>
    </li>
  {/each}
  {#each pending as u (u.key)}
    <li class="tile pend">
      <div class="pic"><MediaThumb kind="image" src={u.local} name={u.file.name} badge={false} ratio="1 / 1" />
        <div class="pg" role="progressbar" aria-label={`Uploading ${u.file.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(u.fraction * 100)}>
          {#if u.status === 'error'}<span>{u.error}</span>{:else}<span>{u.status === 'ready' ? 'Added' : u.status === 'processing' ? 'Preparing…' : `${Math.round(u.fraction * 100)}%`}</span>{/if}
        </div>
        <button type="button" class="rm" onclick={() => s.dismissUpload(u)} aria-label={`Cancel ${u.file.name}`}><Icon name="x" size={12} /></button>
      </div>
      {#if u.status === 'error'}<button type="button" class="btn sm" onclick={() => s.retryUpload(u)}>Try again</button>{/if}
    </li>
  {/each}
  <li class="addtile"><button type="button" class="add" onclick={() => input?.click()}><Icon name="plus" size={20} /><strong>Add pictures</strong><span>Click, drop or paste</span></button></li>
</ul>
<span class="sr-only" role="status" aria-live="polite">{live}</span>
{#if s.notice}<p class="st-note warn" role="status">{s.notice}</p>{/if}

{#if s.schema?.refs?.description}
  <details class="st-details"><summary>How pictures are matched</summary><div class="st-in"><p class="st-note">{s.schema.refs.description}</p></div></details>
{/if}

<MediaAssetPicker bind:open={pickOpen} accept="image" title="Add reference pictures" onpick={(l) => s.addRefs(l)} />
<div class="st-foot"><button type="button" class="btn" onclick={() => (s.step = 'prepare')}>Back</button><button type="button" class="btn primary" onclick={() => (s.step = 'operation')}>Continue to Operation <Icon name="chevron" size={14} /></button></div>

<style>
  .bar { margin-bottom: var(--s3); } .count { margin-left: auto; color: var(--fg-muted); font-variant-numeric: tabular-nums; }
  .gal { list-style: none; margin: 0 0 var(--s3); padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(128px, 1fr)); gap: 12px; }
  .tile { display: grid; gap: 6px; align-content: start; outline: none; transition: opacity var(--fast) var(--ease); }
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
  @media (pointer: coarse) { .mv { opacity: 1; } .rm, .mv button { width: 34px; height: 34px; } }
  .meta { display: grid; gap: 5px; justify-items: start; min-width: 0; } .meta :global(.input) { min-height: 32px; font-size: 12.5px; }
  .wrap { white-space: normal; border-radius: 10px; line-height: 1.3; padding-block: 2px; }
  .use { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; min-height: 32px; cursor: pointer; } .lbl, .slot { width: 100%; }
  .addtile { display: grid; } .add { all: unset; box-sizing: border-box; display: grid; place-content: center; justify-items: center; gap: 3px; min-height: 128px; aspect-ratio: 1; border-radius: 12px; border: 1.5px dashed var(--border-strong); cursor: pointer; color: var(--fg-muted); text-align: center; padding: 8px;
    transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease); }
  .add strong { color: var(--fg); font-size: 13px; } .add span { font-size: 11.5px; } .add:hover { background: var(--m-ring-soft); border-color: var(--m-ring); } .add:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .pg { position: absolute; inset: auto 0 0 0; padding: 14px 8px 8px; font-size: 11.5px; color: #fff; background: linear-gradient(to top, rgba(10,11,12,.85), transparent); }
</style>
