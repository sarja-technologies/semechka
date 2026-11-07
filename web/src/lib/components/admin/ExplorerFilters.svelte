<script lang="ts">
  import { workflowLabel, STATUS_LABEL, activeFilterCount, type Filters } from '$lib/admin/explorer';
  import type { Meta } from '$lib/admin/client';
  let { kind, meta, value = $bindable(), busy = false, onsearch }: { kind: 'conversations' | 'media'; meta: Meta | null; value: Filters; busy?: boolean; onsearch: () => void } = $props();
  const MODES = ['chat', 'image', 'video', 'code', 'knowledge', 'agents'];
  const statuses = $derived(kind === 'media' ? ['queued', 'running', 'completed', 'failed', 'cancelled', 'expired'] : ['active', 'archived', 'expired']);
  const workflows = $derived((meta?.workflows ?? []));
  const models = $derived((meta?.models ?? []).filter((m) => kind === 'conversations' || m.kind !== 'chat'));
  function reset() { value = {}; onsearch(); }
  const n = $derived(activeFilterCount(value));
  const id = (x: string) => `${kind === 'media' ? 'mf' : 'cf'}-${x}`;
</script>
<form class="fb" role="search" aria-label={kind === 'media' ? 'Filter the media library' : 'Filter conversations'} onsubmit={(e) => { e.preventDefault(); onsearch(); }}>
  <div class="g">
    <div class="f"><label for={id('user')}>Person</label>
      <select id={id('user')} class="input" bind:value={value.user_id}><option value={undefined}>Everyone in reach</option>{#each meta?.users ?? [] as u (u.id)}<option value={u.id}>{u.label}</option>{/each}</select></div>
    <div class="f"><label for={id('model')}>Model</label>
      <select id={id('model')} class="input" bind:value={value.model_id}><option value={undefined}>Any model</option>{#each models as m (m.id)}<option value={m.id}>{m.alias}</option>{/each}</select></div>
    {#if kind === 'media'}
      <div class="f"><label for={id('kind')}>Media type</label>
        <select id={id('kind')} class="input" bind:value={value.kind}><option value={undefined}>Pictures and videos</option><option value="image">Pictures</option><option value="video">Videos</option></select></div>
      <div class="f"><label for={id('wf')}>Workflow</label>
        <select id={id('wf')} class="input" bind:value={value.workflow}><option value={undefined}>Any workflow</option>{#each workflows as w (w)}<option value={w}>{workflowLabel(w)}</option>{/each}</select></div>
    {:else}
      <div class="f"><label for={id('mode')}>Mode</label>
        <select id={id('mode')} class="input" bind:value={value.mode}><option value={undefined}>Any mode</option>{#each MODES as m (m)}<option value={m}>{m}</option>{/each}</select></div>
      <div class="f"><label for={id('media')}>Contains</label>
        <select id={id('media')} class="input" bind:value={value.media}><option value={undefined}>Anything</option><option value="any">Pictures or videos</option><option value="image">Pictures</option><option value="video">Videos</option></select></div>
    {/if}
    <div class="f"><label for={id('status')}>Status</label>
      <select id={id('status')} class="input" bind:value={value.status}><option value={undefined}>Any status</option>{#each statuses as s (s)}<option value={s}>{STATUS_LABEL[s] ?? s}</option>{/each}</select></div>
    <div class="f"><label for={id('since')}>{kind === 'media' ? 'Created from' : 'Updated from'}</label><input id={id('since')} type="date" class="input" bind:value={value.since} /></div>
    <div class="f"><label for={id('until')}>{kind === 'media' ? 'Created until' : 'Updated until'}</label><input id={id('until')} type="date" class="input" bind:value={value.until} /></div>
    {#if kind === 'conversations'}
      <div class="f wide"><label for={id('q')}>Search titles</label><input id={id('q')} class="input" maxlength="120" placeholder="Words in a conversation title" bind:value={value.q} aria-describedby={id('qh')} />
        <span id={id('qh')} class="hint">Titles and metadata only. Message text is encrypted and is not searched.</span></div>
    {/if}
    <div class="row act">
    <button class="btn primary" type="submit" disabled={busy}>{busy ? 'Searching…' : 'Search'}</button>
    <button class="btn" type="button" onclick={reset} disabled={busy || !n}>Reset filters</button>
    <span class="small muted" aria-live="polite">{n ? `${n} filter${n === 1 ? '' : 's'} set` : 'No filters set'}</span>
  </div>
  </div>
</form>
<style>
  .fb { border: 1px solid var(--border); border-radius: var(--radius-menu); background: var(--surface); padding: var(--s3) var(--s4); margin-bottom: var(--s4); }
  .g { display: grid; gap: var(--s3) var(--s3); grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); align-items: end; }
  .f { display: grid; gap: 4px; align-content: start; } .f.wide { grid-column: span 2; }
  .f label { font-weight: 500; font-size: 13px; }
  .act { grid-column: 1 / -1; padding-top: 4px; }
  @media (max-width: 640px) { .g { grid-template-columns: repeat(2, minmax(0, 1fr)); } .f.wide { grid-column: 1 / -1; } }
  .f .hint { font-size: 12px; }
</style>
