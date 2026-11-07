<script lang="ts">
  // The options of the iPhone Photo preset, shown in the composer's settings only while the preset is on: which quality (each one is a model the routing
  // table recommends), the 9:16 size, and the scene hints the shared prompt builder turns into sentences. Facts come from the server; nothing is guessed here.
  import type { PresetInfo, PortraitSize } from '$lib/media/preset';

  let { info, modelId, sizes, size = $bindable(''), scenes = $bindable([]), onpick, disabled = false }:
    { info: PresetInfo; modelId: string; sizes: PortraitSize[]; size: string; scenes: string[]; onpick: (modelId: string) => void; disabled?: boolean } = $props();
  const toggle = (id: string) => { scenes = scenes.includes(id) ? scenes.filter((x) => x !== id) : [...scenes, id]; };
</script>

<div class="po" role="group" aria-label="iPhone Photo options">
  <div class="row">
    <span class="lbl" id="po-q">Quality</span>
    <div class="seg" role="radiogroup" aria-labelledby="po-q">
      {#each info.tiers as t (t.id)}
        {@const ok = t.status === 'ready' && !!t.model_id}
        <button type="button" role="radio" aria-checked={ok && t.model_id === modelId} class="s" class:on={ok && t.model_id === modelId} disabled={!ok || disabled}
          title={ok ? [t.alias, t.seconds_hint].filter(Boolean).join(': ') : t.reason || 'Not available'} onclick={() => t.model_id && onpick(t.model_id)}>{t.label}</button>
      {/each}
    </div>
  </div>
  {#if sizes.length}
    <div class="row">
      <label class="lbl" for="po-size">Size (9:16)</label>
      <select id="po-size" class="input sz" bind:value={size} disabled={disabled}>{#each sizes as s (s.label)}<option value={`${s.w}x${s.h}`}>{s.label} · {s.note}</option>{/each}</select>
    </div>
  {/if}
  {#if info.scene_chips.length}
    <div class="row">
      <span class="lbl" id="po-s">Scene</span>
      <div class="chips" role="group" aria-labelledby="po-s">
        {#each info.scene_chips as c (c.id)}
          <button type="button" class="c" class:on={scenes.includes(c.id)} aria-pressed={scenes.includes(c.id)} onclick={() => toggle(c.id)} disabled={disabled}>{c.label}</button>
        {/each}
      </div>
    </div>
  {/if}
  <p class="tiny muted">{info.description}</p>
</div>

<style>
  .po { display: grid; gap: 8px; width: 100%; }
  .row { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; }
  .lbl { font-size: 12px; color: var(--fg-muted); min-width: 64px; }
  .seg { display: inline-flex; padding: 2px; gap: 2px; border-radius: 999px; background: var(--bg-2); flex-wrap: wrap; }
  .s { all: unset; box-sizing: border-box; height: 26px; padding: 0 11px; border-radius: 999px; cursor: pointer; font-size: 12.5px; color: var(--fg-muted); white-space: nowrap; }
  .s:hover:not(:disabled) { color: var(--fg); background: var(--hover); }
  .s.on { background: var(--composer-bg); color: var(--fg); font-weight: 600; box-shadow: var(--shadow-sm); }
  .s:disabled { opacity: 0.5; cursor: not-allowed; } .s:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 1px; }
  .sz { min-height: 32px; padding: 4px 8px; font-size: 13px; color: var(--fg); width: min(100%, 260px); }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .c { all: unset; box-sizing: border-box; height: 26px; padding: 0 10px; border-radius: 999px; cursor: pointer; font-size: 12.5px; color: var(--fg-muted); border: 1px solid var(--border); }
  .c:hover:not(:disabled) { color: var(--fg); background: var(--hover); }
  .c.on { color: var(--fg); background: var(--m-ring-soft); border-color: var(--m-ring); }
  .c:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 1px; }
  @media (pointer: coarse) { .s, .c { height: 36px; } }
</style>
