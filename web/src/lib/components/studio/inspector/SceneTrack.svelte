<script lang="ts">
  // One cell per scene. Saved scenes are buttons that play the saved clip; the current scene shows the sampler's real step;
  // pending scenes are hollow. No invented percentages: the fill inside the current cell is sampling.step / sampling.max.
  import type { SceneCell } from '../../../studio/inspector/logic.ts';
  import Icon from '../../Icon.svelte';
  let { cells, sampling = null, selected = null, onselect }: {
    cells: SceneCell[]; sampling?: { step?: number | null; max?: number | null; scene?: number | null } | null; selected?: number | null; onselect?: (index: number) => void;
  } = $props();
  const stepOf = (c: SceneCell) => (c.state === 'current' && sampling && (sampling.scene == null || sampling.scene === c.index) && sampling.step != null ? sampling : null);
  const label = (c: SceneCell) => {
    const s = stepOf(c);
    switch (c.state) {
      case 'saved': return `Scene ${c.index}: saved. Play it`;
      case 'current': return `Scene ${c.index}: being generated${s ? `, sampler step ${s.step}${s.max != null ? ` of ${s.max}` : ''}` : ''}`;
      case 'writing': return `Scene ${c.index}: file is being written`;
      case 'interrupted': return `Scene ${c.index}: was running when the job stopped`;
      default: return c.unknownTotal ? 'More scenes may follow; the total is not known yet' : `Scene ${c.index}: waiting`;
    }
  };
</script>
<ol class="track" aria-label="Scenes">
  {#each cells as c (c.index)}
    {@const s = stepOf(c)}
    <li>
      {#if c.state === 'saved'}
        <button class="cell saved" class:sel={selected === c.index} aria-pressed={selected === c.index} aria-label={label(c)} onclick={() => onselect?.(c.index)}>
          <span class="n">{c.index}</span><Icon name="play" size={12} />
        </button>
      {:else}
        <span class="cell {c.state}" role="img" aria-label={label(c)} aria-current={c.state === 'current' ? 'step' : undefined}>
          {#if c.state === 'current' && s?.max}<i class="fill" style:width="{Math.min(100, ((s.step ?? 0) / s.max) * 100)}%"></i>{/if}
          <span class="n">{c.unknownTotal ? '?' : c.index}</span>
          {#if c.state === 'current' && s}<span class="step">{s.step}/{s.max ?? '?'}</span>{/if}
          {#if c.state === 'interrupted'}<Icon name="x" size={12} />{/if}
        </span>
      {/if}
    </li>
  {/each}
</ol>
<style>
  .track { display: flex; flex-wrap: wrap; gap: 6px; list-style: none; margin: 0; padding: 0; }
  .cell { position: relative; overflow: hidden; display: inline-flex; align-items: center; justify-content: center; gap: 4px; min-width: 44px; height: 36px; padding: 0 8px;
    border-radius: var(--radius-sm); border: 1.5px solid var(--border-strong); background: transparent; color: var(--fg-muted); font: 600 13px/1 var(--mono); }
  .cell .n, .cell .step, .cell :global(svg) { position: relative; }
  .cell .step { font-size: 11px; font-weight: 500; }
  .saved { background: color-mix(in srgb, var(--success) 18%, var(--card-bg)); border-color: var(--success); color: var(--fg); cursor: pointer; }
  .saved:hover { background: color-mix(in srgb, var(--success) 30%, var(--card-bg)); }
  .saved.sel { box-shadow: 0 0 0 2px var(--accent); }
  .saved :global(svg) { color: var(--success); }
  .current { border-color: var(--accent); color: var(--fg); min-width: 64px; animation: ring 1.6s var(--ease) infinite; }
  .current .fill { position: absolute; left: 0; top: 0; bottom: 0; background: color-mix(in srgb, var(--accent) 26%, transparent); }
  .pending { border-style: dashed; }
  .writing { border-color: var(--info); color: var(--fg); background: repeating-linear-gradient(135deg, transparent 0 5px, color-mix(in srgb, var(--info) 22%, transparent) 5px 8px); }
  .interrupted { border-color: var(--danger); color: var(--danger); }
  @keyframes ring { 50% { box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent); } }
  @media (prefers-reduced-motion: reduce) { .current { animation: none; box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 35%, transparent); } }
  @media (pointer: coarse) { .cell { height: 44px; min-width: 48px; } }
</style>
