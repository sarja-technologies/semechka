<script lang="ts">
  // knowledge: the passages that were given to the model for one reply, shown as retrieved, never as proof the model
  // used them. web: the documents a reply cites (a Yaob system's web search), numbered as the reply's [n] markers.
  import Icon from './Icon.svelte';
  let { sources, skipped = 0, kind = 'knowledge' }: { sources: any[]; skipped?: number; kind?: 'knowledge' | 'web' } = $props();

  const sites = $derived([...new Set((sources ?? []).map((s) => s.site).filter(Boolean))] as string[]);
  const summary = $derived(sites.length > 3 ? `${sites.slice(0, 3).join(', ')} and ${sites.length - 3} more` : sites.join(', '));
  function when(d: string | undefined): string {
    if (!d) return '';
    if (/^\d{4}$/.test(d)) return d;
    const t = new Date(/^\d{4}-\d{2}$/.test(d) ? `${d}-01` : d);
    if (Number.isNaN(t.getTime())) return '';
    return t.toLocaleDateString(undefined, /^\d{4}-\d{2}$/.test(d) ? { month: 'long', year: 'numeric' } : { day: 'numeric', month: 'short', year: 'numeric' });
  }
  const safe = (u: string) => /^https?:\/\//i.test(u ?? '');
</script>
{#if kind === 'web'}
  {#if sources?.length}
    <details class="web" data-testid="web-sources">
      <summary><span class="chev" aria-hidden="true"><Icon name="chevron" size={13} /></span><span class="lbl">{sources.length === 1 ? '1 source' : `${sources.length} sources`}</span>{#if summary}<span class="sites">{summary}</span>{/if}</summary>
      <ol class="wl">
        {#each sources as s (s.n)}
          <li value={s.n}>
            {#if safe(s.url)}<a href={s.url} target="_blank" rel="noopener noreferrer nofollow" title={s.snippet ?? ''}>{s.title || s.site}</a>{:else}<span>{s.title || s.site}</span>{/if}
            <span class="meta">{s.site}{#if when(s.published)}, {when(s.published)}{/if}</span>
          </li>
        {/each}
      </ol>
    </details>
  {/if}
{:else if sources?.length || skipped}
  <details class="src">
    <summary class="small">{sources.length ? `Sources given to the model (${sources.length})` : 'No sources'}{skipped ? ` · ${skipped} collection${skipped > 1 ? 's' : ''} skipped` : ''}</summary>
    {#if skipped}<p class="small warn-t" role="status">{skipped} attached collection{skipped > 1 ? 's were' : ' was'} skipped because you no longer have access or {skipped > 1 ? 'they were' : 'it was'} deleted.</p>{/if}
    <p class="tiny muted" style="margin:4px 0">Found by keyword match, not by meaning. The model may not have used every passage.</p>
    <ol class="list">
      {#each sources as s (s.n)}
        <li><span class="mono tiny">[{s.n}]</span> <strong class="small">{s.file}</strong> <span class="muted tiny">passage {s.chunk + 1}</span>
          <blockquote>{s.excerpt}{s.excerpt.length >= 400 ? '…' : ''}</blockquote></li>
      {/each}
    </ol>
  </details>
{/if}
<style>
  .src { margin-top: 8px; border-left: 2px solid var(--border-strong); padding-left: 10px; }
  summary { cursor: pointer; color: var(--fg-muted); }
  .list { margin: 4px 0 0; padding-left: 0; list-style: none; display: grid; gap: 8px; }
  blockquote { margin: 2px 0 0; font-size: 12.5px; color: var(--fg-muted); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 9em; overflow: auto; }
  .warn-t { color: var(--warn); }

  .web { margin-top: 10px; font-size: 13px; max-width: 72ch; }
  .web summary { display: flex; align-items: baseline; gap: 8px; min-height: 28px; padding: 3px 0; list-style: none; border-radius: var(--radius-sm); }
  .web summary::-webkit-details-marker { display: none; }
  .chev { display: grid; flex: none; align-self: center; transition: transform var(--fast) var(--ease); }
  .web[open] .chev { transform: rotate(90deg); }
  .web summary:hover { color: var(--fg); }
  .web summary:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .lbl { color: var(--fg-2); font-weight: 500; white-space: nowrap; }
  .sites { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wl { margin: 4px 0 0; padding-left: 26px; display: grid; gap: 8px; color: var(--fg-muted); }
  .wl li { padding-left: 2px; }
  .wl li::marker { font-variant-numeric: tabular-nums; }
  .wl a { color: var(--link); text-decoration: none; overflow-wrap: anywhere; } .wl a:hover { text-decoration: underline; }
  .wl a:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; border-radius: 2px; }
  .meta { display: block; color: var(--fg-muted); font-size: 12.5px; }
  @media (pointer: coarse) { .web summary { min-height: 40px; } }
</style>
