<script lang="ts">
  // The big area on the left (on top on a phone). What it shows depends on what you are doing: your picture, the selection editor, the adjustment
  // preview, or a result. Tabs switch between them; the tab you cannot use yet is disabled and says why.
  import Icon from '$lib/components/Icon.svelte';
  import { contentUrl, fmtDims } from '$lib/media/assets';
  import { ratioOfAspect } from '$lib/imajutsu/spec';
  import AdjustStage from './AdjustStage.svelte';
  import MaskEditor from './MaskEditor.svelte';
  import OutpaintView from './OutpaintView.svelte';
  import ResultView from './ResultView.svelte';
  import type { Imajutsu, StageView } from '$lib/imajutsu/store.svelte';
  let { s }: { s: Imajutsu } = $props();
  const n = $derived(s.needs), src = $derived(s.source);
  const sel = $derived(n.selection === 'optional' || n.selection === 'required');
  const hasResult = $derived(s.resultIds.length > 0 || s.copies.length > 0);
  const tabs = $derived<{ id: StageView; label: string; off: string }[]>([
    { id: 'picture', label: s.key === 'outpaint' ? 'New area' : n.source ? 'Picture' : 'Canvas', off: '' },
    { id: 'selection', label: 'Selection', off: !sel ? 'This mode does not use a selection.' : !src ? 'Add your picture first.' : '' },
    { id: 'adjust', label: 'Adjust', off: !s.adjustTarget ? 'Add a picture or make a result first.' : '' },
    { id: 'result', label: 'Result', off: !hasResult ? 'No result yet.' : '' }
  ]);
  const view = $derived(tabs.find((t) => t.id === s.stage && !t.off) ? s.stage : 'picture');
  function key(e: KeyboardEvent) {
    const els = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('button[role=tab]:not([aria-disabled=true])')];
    const i = els.indexOf(document.activeElement as HTMLButtonElement), d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
    if (d && i >= 0) { e.preventDefault(); els[(i + d + els.length) % els.length].focus(); }
  }
  const frameR = $derived(s.plan?.output?.width && s.plan.output.height ? s.plan.output.width / s.plan.output.height : ratioOfAspect(s.spec.aspect) ?? 1);
</script>

<section class="stage" id="stage" aria-label="Stage" data-testid="stage" data-view={view}>
  <!-- svelte-ignore a11y_interactive_supports_focus -->
  <div class="tabs" role="tablist" aria-label="What the stage shows" onkeydown={key}>
    {#each tabs as t (t.id)}
      <button type="button" role="tab" id={`tab-${t.id}`} aria-selected={view === t.id} aria-controls="stage-body" aria-disabled={!!t.off} tabindex={view === t.id ? 0 : -1} title={t.off || undefined} data-stage={t.id}
        onclick={() => { if (!t.off) { s.stage = t.id; if (t.id === 'adjust' && !s.cropOn) s.panel = 'adjust'; } }}>{t.label}{#if t.off}<span class="sr-only">. Unavailable: {t.off}</span>{/if}</button>
    {/each}
  </div>
  <div class="body" id="stage-body" role="tabpanel" aria-labelledby={`tab-${view}`}>
    {#if view === 'selection' && src}
      <MaskEditor {s} onclose={() => (s.stage = 'picture')} />
    {:else if view === 'adjust'}
      <AdjustStage {s} />
    {:else if view === 'result'}
      <ResultView {s} />
    {:else if s.spec.mode === 'outpaint'}
      <OutpaintView {s} />
    {:else if n.source && src}
      <div class="pic" data-testid="stage-picture">
        <img src={contentUrl(src)} alt={src.name ? `Your picture, ${src.name}` : 'Your picture'} />
      </div>
      <p class="tiny muted">{src.name ?? 'Picture'} · {fmtDims(src)}{sel && s.hasSel ? ' · selection drawn in the Selection tab' : ''}</p>
    {:else if n.source}
      <div class="empty" data-testid="stage-empty"><Icon name="image" size={30} /><strong>Drop a picture here</strong><span>Or paste one, or use the Your picture panel.</span></div>
    {:else}
      <div class="canvas" style:aspect-ratio={String(frameR)} data-testid="stage-canvas" role="img" aria-label={`Empty canvas in the shape you chose${s.plan?.output ? `, ${fmtDims(s.plan.output)}` : ''}. Your picture appears here.`}>
        <span>{s.plan?.output ? fmtDims(s.plan.output) : ''}</span>
      </div>
      <p class="st-note">{s.batch.length ? 'The picture appears under Result when it is finished.' : 'Your picture appears here. Pick the shape and press Generate.'}</p>
    {/if}
  </div>
</section>

<style>
  .stage { display: grid; gap: 10px; min-width: 0; padding: var(--s3); border: 1px solid var(--border); border-radius: var(--radius-menu); background: var(--card-bg); }
  .tabs { display: flex; gap: 4px; overflow-x: auto; scrollbar-width: thin; }
  .tabs button { all: unset; box-sizing: border-box; flex: none; min-height: 40px; padding: 0 14px; border-radius: 999px; font-size: 13px; cursor: pointer; border: 1px solid transparent; color: var(--fg-2); display: inline-flex; align-items: center; }
  .tabs button:hover:not([aria-disabled='true']) { background: var(--hover); } .tabs button[aria-selected='true'] { background: var(--selected); border-color: var(--border-strong); color: var(--fg); font-weight: 600; }
  .tabs button[aria-disabled='true'] { opacity: .5; cursor: not-allowed; } .tabs button:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  @media (pointer: coarse) { .tabs button { min-height: 44px; } }
  .body { display: grid; gap: 8px; justify-items: center; min-width: 0; }
  .pic { position: relative; max-width: 100%; } .pic img { display: block; max-width: 100%; max-height: min(72vh, 780px); border-radius: 10px; border: 1px solid var(--border); background: var(--m-card); }
  .empty { display: grid; justify-items: center; gap: 6px; padding: 60px 20px; width: 100%; text-align: center; color: var(--fg-muted); border: 1.5px dashed var(--border-strong); border-radius: var(--radius); } .empty strong { color: var(--fg); }
  .canvas { width: min(100%, 520px); max-height: 70vh; display: grid; place-items: center; border-radius: 10px; border: 1px dashed var(--border-strong); background: repeating-linear-gradient(45deg, var(--m-card) 0 10px, var(--card-bg) 10px 20px); color: var(--fg-muted); font-size: 13px; font-variant-numeric: tabular-nums; }
</style>
