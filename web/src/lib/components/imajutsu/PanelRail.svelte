<script lang="ts">
  // The panels this mode needs, in one slim row. Each shows what has been decided, so the row is also a summary of the project; pressing one goes to it.
  import Icon from '$lib/components/Icon.svelte';
  import { describe } from '$lib/imajutsu/adjust';
    import { fmtDims } from '$lib/imajutsu/spec';
  import { modelName } from '$lib/imajutsu/caps';
  import type { Imajutsu, Panel } from '$lib/imajutsu/store.svelte';
  let { s, list }: { s: Imajutsu; list: { id: Panel; label: string }[] } = $props();
  let nav: HTMLElement | undefined = $state();
  const sp = $derived(s.spec);
  const clip = (t: string, n = 26) => (t.length > n ? t.slice(0, n - 1) + '…' : t);
  const summary = $derived.by((): Record<string, string> => ({
    prompt: sp.prompt.trim() ? clip(sp.prompt.trim()) : s.key === 'iphone_photo' && sp.preset.scenes.length ? sp.preset.scenes.join(', ') : 'Not written',
    source: s.source ? clip(s.source.name ?? 'Picture') : 'Not chosen',
    selection: s.needs.selection === 'area' ? (s.plan?.output ? fmtDims(s.plan.output) : 'Set by size') : s.hasSel ? 'Selection made' : 'None',
    references: !sp.references.length ? 'None yet' : s.plan?.references ? `${s.used} of ${sp.references.length} used` : `${sp.references.length} added`,
    size: s.plan?.output ? fmtDims(s.plan.output) : sp.aspect.id === 'custom' ? 'Custom' : sp.aspect.id === 'source' ? 'Same as picture' : sp.aspect.id,
    model: s.entry ? clip(modelName(s.entry)) : s.needs.model ? 'Not chosen' : 'No model',
    adjust: describe(sp.adjustments).length ? `${describe(sp.adjustments).length} change${describe(sp.adjustments).length === 1 ? '' : 's'}` : 'None',
    generate: s.batch.length ? 'Started' : s.blockers.length ? `${s.blockers.length} to fix` : 'Ready'
  }));
  const done = $derived<Record<string, boolean>>({
    prompt: !!sp.prompt.trim(), source: !!s.source, selection: s.needs.selection === 'area' ? !!s.plan?.output : s.hasSel,
    references: s.used > 0, size: !!s.plan?.output, model: !!s.entry, adjust: describe(sp.adjustments).length > 0, generate: s.batch.length > 0
  });
  function go(id: Panel) { s.panel = id; const el = document.getElementById(`pn-${id}`); el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); el?.querySelector<HTMLElement>('input, textarea, select, button')?.focus({ preventScroll: true }); if (id === 'selection' && s.source && s.needs.selection !== 'area') s.stage = 'selection'; if (id === 'adjust' && s.adjustTarget) s.stage = 'adjust'; }
  $effect(() => { void s.panel; nav?.querySelector<HTMLElement>('[aria-current="step"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); });
</script>

<nav class="stp" aria-label="Panels" bind:this={nav} data-testid="rail">
  <ol>
    {#each list as p, i (p.id)}
      <li>
        <button type="button" class:on={s.panel === p.id} class:done={done[p.id]} aria-current={s.panel === p.id ? 'step' : undefined} onclick={() => go(p.id)} data-panel-link={p.id}>
          <span class="no" aria-hidden="true">{#if done[p.id] && s.panel !== p.id}<Icon name="check" size={12} />{:else}{i + 1}{/if}</span>
          <span class="tx"><strong>{p.label}</strong><span class="su" title={summary[p.id]}>{summary[p.id]}</span></span>
        </button>
      </li>
    {/each}
  </ol>
</nav>

<style>
  .stp { overflow-x: auto; scrollbar-width: thin; padding: 0 0 4px; min-width: 0; }
  ol { list-style: none; margin: 0; padding: 0; display: flex; gap: 6px; min-width: min-content; } li { flex: 1 0 118px; }
  button { all: unset; box-sizing: border-box; width: 100%; display: flex; align-items: center; gap: 9px; padding: 7px 10px; min-height: 48px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card-bg); cursor: pointer; transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease); }
  button:hover { background: var(--hover); } button:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  button.on { border-color: var(--fg); box-shadow: 0 0 0 1px var(--fg) inset; } button.done:not(.on) .no { background: var(--success); color: var(--bg); border-color: var(--success); }
  .no { flex: none; width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; font: 600 12px var(--font); border: 1.5px solid var(--border-strong); color: var(--fg-2); } button.on .no { background: var(--fg); color: var(--bg); border-color: var(--fg); }
  .tx { display: grid; min-width: 0; line-height: 1.25; } strong { font-size: 12.5px; font-weight: 600; } .su { font-size: 11.5px; color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
