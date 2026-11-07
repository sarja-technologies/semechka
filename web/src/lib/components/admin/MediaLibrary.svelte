<script lang="ts">
  import { untrack } from 'svelte';
  // The central media library: every generation job of every workflow (including failed and cancelled ones) and every stored item no job produced.
  import { listMedia, readAsset, readJob, type Meta } from '$lib/admin/client';
  import { appendPage, fmtDur, fmtSize, statusTone, STATUS_LABEL, workflowLabel, type Filters, type FileRef } from '$lib/admin/explorer';
  import { errMsg, fmtTime } from './util';
  import ExplorerFilters from './ExplorerFilters.svelte';
  import MediaThumb from './MediaThumb.svelte';
  import MediaDrawer from './MediaDrawer.svelte';
  import State from './State.svelte';
  let { meta, initial = {}, onfile, onconversation, onscope }: { meta: Meta | null; initial?: Filters; onfile: (f: FileRef) => void; onconversation: (id: string) => void; onscope?: (s: string) => void } = $props();
  let filters = $state<Filters>({});
  let items = $state<any[]>([]), cursor = $state<string | null>(null), searched = $state(false), busy = $state(false), error = $state(''), truncated = $state(false), scope = $state('');
  let detail = $state<any>(null), opener: HTMLElement | null = null, opening = $state('');
  async function run(more = false) {
    busy = true; error = '';
    try {
      const r = await listMedia(filters, more ? cursor : null);
      if (!r) return;
      items = more ? appendPage(items, r.items) : r.items; cursor = r.next_cursor; truncated = r.truncated; searched = true; scope = r.scope; onscope?.(r.scope);
    } catch (e) { error = errMsg(e); } finally { busy = false; }
  }
  async function open(it: any, ev: Event) {
    opener = ev.currentTarget as HTMLElement; opening = it.id; error = '';
    try { const d = it.entry === 'job' ? await readJob(it.id) : await readAsset(it.id); if (d) detail = d; } catch (e) { error = errMsg(e); } finally { opening = ''; }
  }
  function close() { detail = null; opener?.focus(); }
  const thumbOf = (it: any) => it.outputs?.find((o: any) => o.thumb_url);
  const thumbLabel = (it: any, has: boolean) => has ? '' : it.status === 'failed' || it.status === 'cancelled' ? 'No result' : it.status === 'queued' || it.status === 'running' ? 'Not finished'
    : it.status === 'completed' && it.kind === 'video' && it.outputs?.[0]?.available ? `Video · ${it.outputs[0].duration ? fmtDur(it.outputs[0].duration) : fmtSize(it.outputs[0].size)}` : '';
  const stateOf = (it: any) => (it.status === 'expired' ? 'expired' : it.outputs?.[0]?.state ?? 'missing');
  $effect(() => { initial; untrack(() => { if (Object.keys(initial).length) { filters = { ...initial }; run(); } }); });
</script>
<ExplorerFilters kind="media" {meta} bind:value={filters} {busy} onsearch={() => run()} />
{#if error}<div class="notice bad" role="alert"><div class="grow">{error}</div></div>{/if}
{#if !searched && !busy}
  <div class="panel"><div class="panel-b empty"><p style="margin:0"><strong>Nothing has been listed yet.</strong></p><p class="small muted" style="margin:4px 0 0">Choose filters and press Search. Searching asks for a reason and records one access in the audit log; the people whose items appear are told.</p></div></div>
{:else}
  <State loading={busy && !items.length} empty={!busy && !items.length && !error ? 'No pictures, videos or generation records match these filters.' : ''} />
  {#if items.length}
    <ul class="mg" aria-label="Media library" aria-busy={busy}>
      {#each items as it (it.id)}
        {@const t = thumbOf(it)}
        <li>
          <button class="mc" onclick={(e) => open(it, e)} disabled={!!opening} aria-label={`Open ${workflowLabel(it.workflow)} ${it.kind} by ${it.owner.display_name}, ${STATUS_LABEL[it.status] ?? it.status}`}>
            <span class="im"><MediaThumb url={t?.thumb_url} kind={it.kind} fileState={t ? 'available' : stateOf(it)} label={thumbLabel(it, !!t)} />
              <span class="chip {statusTone(it.status)} st">{STATUS_LABEL[it.status] ?? it.status}</span>
              {#if it.outputs?.length > 1}<span class="chip n">{it.outputs.length} files</span>{/if}</span>
            <span class="tx">
              <span class="t1"><strong>{workflowLabel(it.workflow)}</strong>{it.operation && it.operation !== it.workflow && it.operation !== 'generate' ? ` · ${it.operation}` : ''}</span>
              <span class="t2 small">{it.model?.alias ?? '—'} · {it.kind}</span>
              <span class="t2 tiny muted">{it.owner.display_name} · {fmtTime(it.created_at)}</span>
              {#if it.error}<span class="tiny err">{it.error}</span>{/if}
            </span>
          </button>
        </li>
      {/each}
    </ul>
    <div class="row more">
      {#if cursor}<button class="btn" onclick={() => run(true)} disabled={busy}>{busy ? 'Loading…' : 'Load more'}</button>{/if}
      <span class="small muted" role="status">{items.length} shown{cursor ? '' : ' · end of results'}{truncated ? ' · the search stopped at its scan limit; load more to continue' : ''}</span>
    </div>
  {/if}
{/if}
<MediaDrawer {detail} onclose={close} {onfile} {onconversation} />
<style>
  .empty { text-align: center; padding: 32px 16px; }
  .mg { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
  .mc { all: unset; box-sizing: border-box; display: grid; grid-template-rows: auto 1fr; width: 100%; height: 100%; cursor: pointer; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-menu); overflow: hidden; }
  .mc:hover:not(:disabled) { border-color: var(--border-strong); } .mc:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .im { position: relative; aspect-ratio: 4 / 3; display: block; }
  .st { position: absolute; left: 8px; top: 8px; background: var(--surface); } .n { position: absolute; right: 8px; bottom: 8px; background: var(--surface); }
  .tx { display: grid; gap: 1px; padding: 10px 12px; align-content: start; min-width: 0; }
  .t1, .t2 { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; } .err { color: var(--danger); overflow-wrap: anywhere; white-space: normal; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .more { margin-top: 16px; }
</style>
