<script lang="ts">
  // The six steps in one slim row. Each shows what has been decided, so the row is also a summary of the project.
  import Icon from '$lib/components/Icon.svelte';
  import { STEPS, type Studio } from '$lib/studio/store.svelte';
  import { RESOLUTIONS, fmtSecs, ratioWords, framing } from '$lib/studio/spec';
  let { s }: { s: Studio } = $props();
  const sp = $derived(s.spec);
  let nav: HTMLElement | undefined = $state();
  // on a narrow screen the row scrolls sideways: keep the current step in view
  $effect(() => { void s.step; nav?.querySelector<HTMLElement>('[aria-current="step"]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' }); });

  const summary = $derived.by((): Record<string, string> => {
    const fr = framing(sp, s.sw || 16, s.sh || 9);
    const prep = [sp.frame.aspect === 'original' ? '' : ratioWords(fr.ratio), sp.trim ? fmtSecs(s.keptSeconds) : '', sp.resolution.preset === 'original' ? '' : sp.resolution.preset === 'custom' ? `${sp.resolution.width}×${sp.resolution.height}` : RESOLUTIONS.find((r) => r.id === sp.resolution.preset)?.label ?? sp.resolution.preset, sp.crop ? 'cropped' : ''].filter(Boolean);
    return {
      video: s.source ? `${s.source.name ?? 'Video'}` : 'Not chosen',
      prepare: prep.length ? prep.join(' · ') : 'Original',
      references: !sp.references.length ? 'None yet' : s.mode ? `${s.used} of ${sp.references.length} used` : `${sp.references.length} added`,
      operation: s.mode ? s.mode.label : 'Not chosen',
      model: s.entry?.alias ?? s.caps?.models.find((m) => m.model_id === sp.model)?.alias ?? 'Not chosen',
      generate: s.jobId ? 'Started' : s.blockers.length ? 'Not ready' : 'Ready'
    };
  });
  const done = $derived<Record<string, boolean>>({
    video: !!s.project, prepare: s.prep.state === 'done' && !s.prepStale, references: !!s.mode && s.used >= Math.max(1, s.policy.min) && s.used > 0,
    operation: !!s.mode && (s.mode.status !== 'experimental' || sp.accept_experimental), model: !!s.entry, generate: !!s.jobId
  });
</script>

<nav class="stp" aria-label="Steps" bind:this={nav}>
  <ol>
    {#each STEPS as st, i (st.id)}
      <li>
        <button type="button" class:on={s.step === st.id} class:done={done[st.id]} aria-current={s.step === st.id ? 'step' : undefined} disabled={!s.project && st.id !== 'video'} onclick={() => (s.step = st.id)} data-step={st.id}>
          <span class="no" aria-hidden="true">{#if done[st.id] && s.step !== st.id}<Icon name="check" size={12} />{:else}{i + 1}{/if}</span>
          <span class="tx"><strong>{st.label}</strong><span class="su" title={summary[st.id]}>{summary[st.id]}</span></span>
        </button>
      </li>
    {/each}
  </ol>
</nav>

<style>
  .stp { overflow-x: auto; scrollbar-width: thin; padding: 0 0 4px; min-width: 0; }
  ol { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(6, minmax(112px, 1fr)); gap: 6px; min-width: 700px; }
  button { all: unset; box-sizing: border-box; width: 100%; display: flex; align-items: center; gap: 9px; padding: 7px 10px; min-height: 48px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card-bg); cursor: pointer;
    transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease); }
  button:hover:not(:disabled) { background: var(--hover); } button:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; } button:disabled { opacity: .5; cursor: not-allowed; }
  button.on { border-color: var(--fg); box-shadow: 0 0 0 1px var(--fg) inset; } button.done:not(.on) .no { background: var(--success); color: var(--bg); border-color: var(--success); }
  .no { flex: none; width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; font: 600 12px var(--font); border: 1.5px solid var(--border-strong); color: var(--fg-2); }
  button.on .no { background: var(--fg); color: var(--bg); border-color: var(--fg); }
  .tx { display: grid; min-width: 0; line-height: 1.25; } strong { font-size: 12.5px; font-weight: 600; } .su { font-size: 11.5px; color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
