<script lang="ts">
  // Duration in SECONDS for a video model: a slider and an editable number that always agree, the model's real limits, the exact lengths when only specific ones exist,
  // and one honest line saying what will actually be generated. FPS and frame counts never appear here (the model's frame rate is automatic; see media/duration.ts).
  import { describePlan, fmtSeconds, listedDurations, planDuration, type DurationSpec } from '$lib/media/duration';
  let { spec, seconds = $bindable(0), fps = null, maxSeconds = null, disabled = false, id = 'duration', onuser }: {
    spec: DurationSpec; seconds: number; fps?: number | null; maxSeconds?: number | null; disabled?: boolean; id?: string; onuser?: () => void;
  } = $props();

  // a few specific lengths read best as buttons; many close ones read best as a slider (the typed box and the slider both snap to a length the model really makes)
  const listed = $derived((() => { const l = listedDurations(spec, fps); return l && l.length <= 8 ? l : null; })());
  const plan = $derived(planDuration(seconds, spec, { fps, maxSeconds }));
  // what is typed in the box while it is being edited; it becomes the (honest) actual length when the person leaves the box or presses Enter
  let typed = $state<string | null>(null);
  const shown = $derived(typed ?? (plan ? String(Math.round(plan.seconds * 100) / 100) : ''));
  const lo = $derived(spec.min), hi = $derived(Math.min(spec.max, maxSeconds ?? spec.max));

  function setFrom(raw: number) {
    const p = planDuration(raw, spec, { fps, maxSeconds });
    if (p) { seconds = p.seconds; onuser?.(); }
  }
  function commit() {
    if (typed !== null) { const n = Number(typed.replace(',', '.')); if (Number.isFinite(n) && n > 0) setFrom(n); }
    typed = null;
  }
  $effect(() => { if (!plan && spec.available) seconds = spec.default; });
</script>

<div class="dur" data-testid="duration">
  <div class="top">
    <label for={`${id}-n`} class="lab">Duration</label>
    <span class="val" aria-live="polite" data-testid="duration-actual">{plan ? fmtSeconds(plan.seconds) : ''}</span>
  </div>
  {#if !spec.available}
    <p class="tiny err" role="status">{spec.why ?? 'This model cannot make a clip within your limits.'}</p>
  {:else}
    <div class="row">
      {#if listed}
        <div class="chips" role="radiogroup" aria-label="Duration in seconds">
          {#each listed as o (o)}
            <button type="button" role="radio" aria-checked={Math.abs((plan?.seconds ?? 0) - o) < 0.01} {disabled} onclick={() => setFrom(o)}>{fmtSeconds(o)}</button>
          {/each}
        </div>
      {:else}
        <input type="range" class="slider" aria-label="Duration in seconds" min={lo} max={hi} step={spec.step} value={Math.min(hi, Math.max(lo, plan?.seconds ?? spec.default))} {disabled}
          oninput={(e) => { typed = null; setFrom(Number(e.currentTarget.value)); }} data-testid="duration-slider" />
      {/if}
      <span class="numwrap"><input id={`${id}-n`} class="input num" type="text" inputmode="decimal" aria-label="Duration in seconds, exact" value={shown} {disabled} data-testid="duration-input"
        oninput={(e) => { typed = e.currentTarget.value; const n = Number(typed.replace(',', '.')); if (Number.isFinite(n) && n > 0) setFrom(n); }}
        onblur={commit} onkeydown={(e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } }} /><span class="u">s</span></span>
    </div>
    <p class="tiny note" class:warn={plan?.adjusted} data-testid="duration-note">{describePlan(plan, spec)}</p>
    <p class="tiny muted" data-testid="duration-limits">
      {#if listed}This model makes {listed.map(fmtSeconds).join(', ')}.{:else}This model makes {fmtSeconds(lo)} to {fmtSeconds(hi)}{#if spec.options?.length && spec.declared}, at {spec.options.length} fixed lengths{:else}, in steps of {fmtSeconds(spec.step)}{/if}.{/if}
      {#if maxSeconds && maxSeconds < spec.max} Your organisation allows at most {maxSeconds} s.{/if}
    </p>
  {/if}
</div>

<style>
  .dur { display: grid; gap: 6px; padding: 8px 2px 2px; }
  .top { display: flex; justify-content: space-between; align-items: baseline; }
  .lab { font-weight: 600; font-size: 13px; }
  .val { font-variant-numeric: tabular-nums; font-weight: 600; }
  .row { display: flex; gap: 12px; align-items: center; }
  .slider { flex: 1; min-width: 0; accent-color: var(--accent, currentColor); }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; flex: 1; }
  .chips button { padding: 5px 12px; border-radius: 999px; border: 1px solid var(--line, #8884); background: transparent; color: inherit; cursor: pointer; }
  .chips button[aria-checked='true'] { background: var(--accent, #2a6df4); color: var(--on-accent, #fff); border-color: transparent; }
  .numwrap { display: inline-flex; align-items: center; gap: 6px; }
  .num { width: 5.5em; text-align: right; font-variant-numeric: tabular-nums; }
  .u { color: var(--fg-muted); }
  .note.warn { color: var(--warn); }
  .muted { color: var(--fg-muted); }
</style>
