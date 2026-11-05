<script lang="ts">
  // The attachments of the message being written, as a row of compact cards at the top of the composer: picture or video, the part each plays,
  // a play control, and remove / replace / edit. Cards can be reordered (drag, or Alt+Arrow keys); nothing here sends anything.
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from './MediaThumb.svelte';
  import VideoPreview from './VideoPreview.svelte';
  import ImagePreview from './ImagePreview.svelte';
  import { fmtDims, fmtSize, type Asset } from '$lib/media/assets';
  import type { Tray, TrayItem } from '$lib/media/tray.svelte';
  import type { Slot, Verdict } from '$lib/media/roles';
  import { opLabel } from '$lib/media/roles';
  import { openViewer } from '$lib/media/viewer.svelte';

  let { tray, slots = [], verdict, operation = null, onreplace, useLabel = 'Use edited version', onadd, empty = [], onslot, dropHint = false }:
    { tray: Tray; slots?: Slot[]; verdict?: Verdict; operation?: string | null; onreplace: (key: string) => void; useLabel?: string; onadd?: () => void;
      /** places that still want a file: shown as visible "add" cards so nobody has to guess where to put a source video or a reference picture */
      empty?: Slot[]; onslot?: (s: Slot) => void; dropHint?: boolean } = $props();

  let over = $state<string | null>(null), dragKey = $state<string | null>(null), live = $state('');
  const stateOf = (it: TrayItem) => verdict?.state[it.key] ?? 'used';
  const optionsFor = (it: TrayItem) => slots.filter((s) => s.kind === it.kind);
  const asAsset = (it: TrayItem): Asset | null => it.asset ?? (it.legacy ? { id: it.legacy.id, kind: it.kind === 'video' ? 'video' : 'image', mime: it.legacy.mime, size: it.legacy.size, name: it.name, width: it.legacy.width, height: it.legacy.height,
    url: `/api/chat/attachments/${it.legacy.id}/content` } : null);
  const ordinal = (it: TrayItem) => { const same = tray.items.filter((x) => x.role === it.role && stateOf(x) !== 'unused'); return same.length > 1 ? same.indexOf(it) + 1 : 0; };
  const caption = (it: TrayItem) => {
    const s = stateOf(it);
    return s === 'unused' ? (operation ? `Not used by ${opLabel(operation).toLowerCase()}` : it.kind === 'video' ? 'Chat can’t read video' : 'Not used') : s === 'extra' ? 'Too many for this role' : s === 'unassigned' ? 'Choose a role' : '';
  };

  function open(it: TrayItem) {
    const a = asAsset(it);
    if (!a) return;
    const sibs = tray.items.map(asAsset).filter((x): x is Asset => !!x);
    openViewer(a, { siblings: sibs, useLabel: it.asset ? useLabel : undefined, onuse: it.asset ? (n) => tray.swap(it.key, n) : undefined });
  }
  function edit(it: TrayItem) {
    const a = asAsset(it);
    if (!a || !it.asset) return;
    openViewer(a, { edit: true, useLabel, onuse: (n) => tray.swap(it.key, n) });
  }
  function dstart(e: DragEvent, it: TrayItem) {
    dragKey = it.key;
    e.dataTransfer?.setData('application/x-semechka-tray', it.key);
    if (e.dataTransfer) e.dataTransfer.effectAllowed = 'move';
  }
  function dover(e: DragEvent, it: TrayItem) {
    if (!dragKey || dragKey === it.key || !e.dataTransfer?.types.includes('application/x-semechka-tray')) return;
    e.preventDefault(); e.stopPropagation(); over = it.key;
  }
  function ddrop(e: DragEvent, it: TrayItem) {
    if (!dragKey) return;
    e.preventDefault(); e.stopPropagation();
    tray.reorder(dragKey, it.key); live = `Moved to position ${tray.items.findIndex((x) => x.key === dragKey) + 1}`; dragKey = over = null;
  }
  function onkey(e: KeyboardEvent, it: TrayItem) {
    if (!e.altKey || (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight')) return;
    e.preventDefault(); tray.move(it.key, e.key === 'ArrowLeft' ? -1 : 1);
    live = `${it.name} moved to position ${tray.items.findIndex((x) => x.key === it.key) + 1} of ${tray.items.length}`;
  }
  function pick(it: TrayItem, e: Event) { tray.setRole(it.key, (e.currentTarget as HTMLSelectElement).value || null); }
</script>

<div class="tray" role="list" aria-label="Attached files">
  {#each tray.items as it (it.key)}
    {@const st = stateOf(it)}
    {@const a = asAsset(it)}
    {@const up = it.upload}
    <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
    <article class="card" class:unused={st === 'unused'} class:warn={st === 'extra' || st === 'unassigned'} class:bad={!!it.error} class:over={over === it.key} class:drag={dragKey === it.key}
      role="listitem" tabindex="0" aria-label={`${it.name}, ${it.kind}${caption(it) ? `, ${caption(it)}` : ''}. Alt plus arrow keys reorder.`} draggable="true"
      ondragstart={(e) => dstart(e, it)} ondragend={() => (dragKey = over = null)} ondragover={(e) => dover(e, it)} ondragleave={() => over === it.key && (over = null)} ondrop={(e) => ddrop(e, it)} onkeydown={(e) => onkey(e, it)}>
      <div class="pic">
        {#if up && !it.error}
          <MediaThumb kind={it.kind} src={up.local} name={it.name} badge={false} />
          <div class="prog" role="progressbar" aria-label={`Uploading ${it.name}`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(up.fraction * 100)}>
            <span class="pct">{up.status === 'queued' ? 'Waiting…' : up.status === 'processing' ? 'Preparing…' : `${Math.round(up.fraction * 100)}%`}</span>
            <span class="bar"><i style:width={`${up.status === 'processing' ? 100 : up.fraction * 100}%`} class:pulse={up.status === 'processing'}></i></span>
          </div>
        {:else if it.error}
          <MediaThumb kind={it.kind} src={up?.local} name={it.name} badge={false} />
          <div class="errbox" role="alert"><Icon name="warn" size={16} /><span>{it.error}</span></div>
        {:else if a && it.kind === 'video'}
          <VideoPreview asset={a} onopen={() => open(it)} ratio="132 / 82" />
        {:else if a && it.kind === 'image'}
          <ImagePreview asset={a} onopen={() => open(it)} ratio="132 / 82" />
        {:else}
          <span class="file"><Icon name="log" size={22} /></span>
        {/if}

        {#if !(up && up.active) || it.error}
          <div class="tools">
            {#if it.error && up}<button type="button" class="t" onclick={() => tray.retry(it.key)} aria-label={`Retry ${it.name}`} title="Retry"><Icon name="refresh" size={13} /></button>{/if}
            {#if it.asset && !it.error && (it.asset.mime !== 'image/gif')}<button type="button" class="t" onclick={() => edit(it)} aria-label={`Edit ${it.name}`} title={it.kind === 'video' ? 'Trim or crop' : 'Crop or rotate'}><Icon name={it.kind === 'video' ? 'scissors' : 'crop'} size={13} /></button>{/if}
            {#if !it.error}<button type="button" class="t" onclick={() => onreplace(it.key)} aria-label={`Replace ${it.name}`} title="Replace"><Icon name="replace" size={13} /></button>{/if}
          </div>
        {/if}
        <button type="button" class="rm" onclick={() => tray.remove(it.key)} aria-label={up?.active ? `Cancel upload of ${it.name}` : `Remove ${it.name}`} title={up?.active ? 'Cancel upload' : 'Remove'}><Icon name="x" size={12} /></button>
        <span class="grip" aria-hidden="true"><Icon name="grip" size={13} /></span>
        {#if ordinal(it)}<span class="ord" aria-hidden="true">{ordinal(it)}</span>{/if}
      </div>
      <div class="meta">
        {#if slots.length && it.kind !== 'file' && !it.error && !(up && up.active)}
          <span class="sel">
            <select aria-label={`Role of ${it.name}`} value={it.role ?? ''} onchange={(e) => pick(it, e)}>
              <option value="" disabled={!!optionsFor(it).length && !!it.role}>{optionsFor(it).length ? 'Not used' : 'Not used here'}</option>
              {#each optionsFor(it) as s (s.input)}<option value={s.input}>{s.label}</option>{/each}
            </select>
            <Icon name="chevdown" size={12} />
          </span>
        {:else}
          <span class="nm" title={it.name}>{it.name}</span>
        {/if}
        <span class="sub" title={it.name}>{caption(it) || (a ? [fmtDims(a), fmtSize(a.size)].filter(Boolean).join(' · ') : fmtSize(it.size))}</span>
      </div>
    </article>
  {/each}
  {#each empty as sl (sl.input)}
    <div role="listitem" class="slotwrap">
      <button type="button" class="slot" class:need={sl.min > 0} onclick={() => onslot?.(sl)} aria-label={`Add ${sl.label.toLowerCase()}${sl.min > 0 ? ' (required)' : ' (optional)'}: choose a file or drop one here`}>
        <Icon name={sl.kind === 'video' ? 'video' : 'image'} size={22} />
        <strong>{sl.label}</strong>
        <span>{sl.min > 0 ? 'Required' : 'Optional'} · click or drop</span>
      </button>
    </div>
  {/each}
  {#if onadd}
    <div role="listitem" class="addwrap"><button type="button" class="add" onclick={onadd} aria-label="Add from your Library" title="Add from your Library"><Icon name="library" size={18} /><span>Library</span></button></div>
  {/if}
</div>
<span class="sr-only" role="status" aria-live="polite">{live}</span>

<style>
  .tray { display: flex; gap: 8px; overflow-x: auto; min-width: 0; max-width: 100%; padding: 2px 2px 6px; scrollbar-width: thin; scroll-snap-type: x proximity; }
  .card { position: relative; flex: none; width: 132px; display: grid; gap: 5px; scroll-snap-align: start; outline: none; transition: transform var(--medium) var(--ease), opacity var(--fast) var(--ease); }
  .card:focus-visible .pic { box-shadow: 0 0 0 2px var(--m-ring); }
  .card.drag { opacity: 0.45; } .card.over .pic { box-shadow: 0 0 0 2px var(--m-ring); transform: translateX(3px); }
  .pic { position: relative; width: 132px; height: 82px; border-radius: 10px; overflow: hidden; background: var(--m-card); border: 1px solid var(--border);
    transition: border-color var(--fast) var(--ease), box-shadow var(--fast) var(--ease), transform var(--fast) var(--ease); }
  .card:hover .pic { border-color: var(--border-strong); }
  .card.unused .pic { opacity: 0.55; border-style: dashed; } .card.warn .pic { border-color: var(--warn); } .card.bad .pic { border-color: var(--danger); }
  .file { display: grid; place-items: center; width: 100%; height: 100%; color: var(--fg-muted); }
  .tools { position: absolute; left: 5px; top: 5px; display: flex; gap: 3px; opacity: 0; transform: translateY(-2px); transition: opacity var(--fast) var(--ease), transform var(--fast) var(--ease); z-index: 3; }
  .card:hover .tools, .card:focus-within .tools { opacity: 1; transform: none; }
  .t, .rm { all: unset; box-sizing: border-box; width: 22px; height: 22px; display: grid; place-items: center; border-radius: 6px; cursor: pointer; color: #fff; background: rgba(14, 15, 16, 0.7); backdrop-filter: blur(4px); }
  .t:hover, .rm:hover { background: rgba(14, 15, 16, 0.92); } .t:focus-visible, .rm:focus-visible { outline: 2px solid var(--m-ring); }
  .rm { position: absolute; right: 5px; top: 5px; z-index: 3; opacity: 0.85; } .card:hover .rm, .card:focus-within .rm { opacity: 1; }
  .grip { position: absolute; left: 50%; top: 2px; margin-left: -7px; color: #fff; opacity: 0; filter: drop-shadow(0 1px 2px rgba(0,0,0,.6)); pointer-events: none; transition: opacity var(--fast) var(--ease); z-index: 2; }
  .card:hover .grip { opacity: 0.8; }
  .ord { position: absolute; left: 5px; bottom: 5px; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; display: grid; place-items: center; font-size: 11px; font-weight: 600;
    background: rgba(14, 15, 16, 0.78); color: #fff; font-variant-numeric: tabular-nums; z-index: 2; }
  .prog { position: absolute; inset: auto 0 0 0; padding: 18px 8px 7px; display: grid; gap: 4px; background: linear-gradient(to top, rgba(10, 11, 12, 0.82), transparent); color: #fff; z-index: 2; }
  .pct { font-size: 11px; font-variant-numeric: tabular-nums; }
  .bar { height: 3px; border-radius: 2px; background: rgba(255, 255, 255, 0.25); overflow: hidden; } .bar i { display: block; height: 100%; background: #fff; border-radius: 2px; transition: width 0.2s linear; }
  .bar i.pulse { animation: pulse 1.1s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.35; } }
  @media (prefers-reduced-motion: reduce) { .bar i.pulse { animation: none; } }
  .errbox { position: absolute; inset: 0; display: grid; align-content: center; justify-items: center; gap: 4px; padding: 8px 6px; text-align: center; font-size: 11px; line-height: 1.25; color: #fff; background: rgba(120, 28, 28, 0.82); z-index: 2; }
  .errbox span { display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .meta { display: grid; gap: 1px; min-width: 0; }
  .nm { font-size: 12px; font-weight: 500; color: var(--fg); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .sub { font-size: 11px; color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .card.warn .sub { color: var(--warn); }
  .sel { position: relative; display: block; }
  .sel select { appearance: none; -webkit-appearance: none; width: 100%; height: 24px; padding: 0 18px 0 7px; border-radius: 7px; border: 1px solid var(--border); background: var(--m-card); color: var(--fg); font: inherit; font-size: 11.5px; font-weight: 500; cursor: pointer; text-overflow: ellipsis;
    transition: border-color var(--fast) var(--ease), background var(--fast) var(--ease); }
  .sel select:hover { border-color: var(--border-strong); background: var(--hover); } .sel select:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 1px; }
  .sel :global(svg) { position: absolute; right: 6px; top: 6px; pointer-events: none; color: var(--fg-muted); }
  .addwrap, .slotwrap { flex: none; }
  .slot { all: unset; box-sizing: border-box; width: 132px; height: 82px; border-radius: 10px; border: 1.5px dashed var(--border-strong); display: grid; place-content: center; justify-items: center; gap: 2px; text-align: center; cursor: pointer; color: var(--fg-muted);
    transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease), color var(--fast) var(--ease); }
  .slot strong { font-size: 12.5px; font-weight: 600; color: var(--fg); } .slot span { font-size: 11px; }
  .slot.need { border-color: color-mix(in srgb, var(--m-ring) 70%, var(--border-strong)); }
  .slot:hover { background: var(--m-ring-soft); border-color: var(--m-ring); color: var(--fg); } .slot:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  @media (max-width: 719px) { .slot { width: 118px; height: 74px; } }
  .add { all: unset; flex: none; box-sizing: border-box; width: 56px; height: 82px; border-radius: 10px; border: 1px dashed var(--border-strong); display: grid; place-content: center; justify-items: center; gap: 3px; cursor: pointer; color: var(--fg-muted);
    font-size: 11px; transition: background var(--fast) var(--ease), color var(--fast) var(--ease), border-color var(--fast) var(--ease); }
  .add:hover { background: var(--hover); color: var(--fg); } .add:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  @media (pointer: coarse) { .tools, .rm { opacity: 1; } .t, .rm { width: 28px; height: 28px; } .sel select { height: 30px; } }
  @media (max-width: 719px) { .card, .pic { width: 118px; } .pic { height: 74px; } .add { height: 74px; } }
</style>
