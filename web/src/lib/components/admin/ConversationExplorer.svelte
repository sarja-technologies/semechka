<script lang="ts">
  import { untrack } from 'svelte';
  // Search the conversations of every person in reach, then open one to read it with its media in context.
  import { listConversations, readConversation, type Meta } from '$lib/admin/client';
  import { appendPage, statusTone, STATUS_LABEL, type Filters, type FileRef } from '$lib/admin/explorer';
  import { errMsg, fmtTime } from './util';
  import ExplorerFilters from './ExplorerFilters.svelte';
  import ConversationView from './ConversationView.svelte';
  import State from './State.svelte';
  let { meta, initial = {}, openId = '', onjob, onfile, onscope }: { meta: Meta | null; initial?: Filters; openId?: string; onjob: (id: string) => void; onfile: (f: FileRef) => void; onscope?: (s: string) => void } = $props();
  let filters = $state<Filters>({});
  let items = $state<any[]>([]), cursor = $state<string | null>(null), searched = $state(false), busy = $state(false), error = $state(''), truncated = $state(false), searchedBy = $state('');
  let open = $state<any>(null), loading = $state(''), viewer: HTMLElement | undefined = $state();
  async function run(more = false) {
    busy = true; error = '';
    try {
      const r = await listConversations(filters, more ? cursor : null);
      if (!r) return;
      items = more ? appendPage(items, r.items) : r.items; cursor = r.next_cursor; truncated = r.truncated; searched = true; searchedBy = r.searched ?? ''; onscope?.(r.scope);
    } catch (e) { error = errMsg(e); } finally { busy = false; }
  }
  async function view(c: { id: string; title: string }) {
    loading = c.id; error = '';
    try { const d = await readConversation(c.id, c.title); if (d) { open = d; viewer?.scrollIntoView({ block: 'nearest' }); } } catch (e) { error = errMsg(e); } finally { loading = ''; }
  }
  $effect(() => { initial; untrack(() => { if (Object.keys(initial).length) { filters = { ...initial }; run(); } }); });
  $effect(() => { const id = openId; if (id) untrack(() => view({ id, title: '' })); });
</script>
<ExplorerFilters kind="conversations" {meta} bind:value={filters} {busy} onsearch={() => run()} />
{#if error}<div class="notice bad" role="alert"><div class="grow">{error}</div></div>{/if}
{#if !searched && !busy && !open}
  <div class="panel"><div class="panel-b empty"><p style="margin:0"><strong>Nothing has been listed yet.</strong></p><p class="small muted" style="margin:4px 0 0">Choose filters and press Search. Searching asks for a reason and records one access in the audit log; the people whose conversations appear are told.</p></div></div>
{:else}
  <div class="cols">
    <section class="panel" aria-label="Conversations">
      <div class="panel-h"><h3>Conversations{items.length ? ` (${items.length}${cursor ? '+' : ''})` : ''}</h3>{#if searchedBy}<span class="tiny muted">Matched on {searchedBy} only</span>{/if}</div>
      <State loading={busy && !items.length} empty={!busy && !items.length && !error ? 'No conversations match these filters.' : ''} />
      {#if items.length}
        <ul class="cl" aria-busy={busy}>
          {#each items as c (c.id)}
            <li><button class="ci" class:on={open?.id === c.id} aria-current={open?.id === c.id ? 'true' : undefined} onclick={() => view(c)} disabled={!!loading}>
              <span class="t"><strong>{c.title || 'Untitled'}</strong>{#if c.status !== 'active'}<span class="chip {statusTone(c.status)}">{STATUS_LABEL[c.status]}</span>{/if}</span>
              <span class="tiny muted">{c.owner.display_name} · {c.mode}{c.models.length ? ` · ${c.models.map((m: any) => m.alias).join(', ')}` : ''}</span>
              <span class="tiny muted">{c.messages} message{c.messages === 1 ? '' : 's'}{c.media_items ? ` · ${c.media_items} picture/video` : ''}{c.failed_messages ? ` · ${c.failed_messages} failed` : ''} · {fmtTime(c.updated_at)}</span></button></li>
          {/each}
        </ul>
        <div class="row pad">
          {#if cursor}<button class="btn sm" onclick={() => run(true)} disabled={busy}>{busy ? 'Loading…' : 'Load more'}</button>{/if}
          <span class="tiny muted" role="status">{cursor ? '' : 'End of results. '}{truncated ? 'The search stopped at its scan limit; load more to continue. ' : ''}Opening one records another access.</span>
        </div>
      {/if}
    </section>
    <section class="panel viewer" aria-live="polite" bind:this={viewer} aria-label="Opened conversation">
      {#if open}<ConversationView conv={open} {onjob} {onfile} />
      {:else}<div class="panel-b muted small">{loading ? 'Opening…' : 'Choose a conversation to read it with its pictures and videos.'}</div>{/if}
    </section>
  </div>
{/if}
<style>
  .empty { text-align: center; padding: 32px 16px; }
  .cols { display: grid; grid-template-columns: minmax(260px, 1fr) minmax(0, 2fr); gap: 16px; align-items: start; }
  .cl { list-style: none; margin: 0; padding: 0; max-height: 68vh; overflow: auto; }
  .ci { all: unset; box-sizing: border-box; width: 100%; display: grid; gap: 1px; padding: 10px 16px; border-top: 1px solid var(--border); cursor: pointer; font-size: 14px; }
  .ci:hover { background: var(--hover); } .ci.on { background: var(--selected); box-shadow: inset 3px 0 0 var(--accent); } .ci:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .t { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; } .t strong { overflow-wrap: anywhere; }
  .pad { padding: 10px 16px; border-top: 1px solid var(--border); }
  .viewer { position: sticky; top: 64px; max-height: calc(100vh - 80px); overflow: auto; }
  @media (max-width: 960px) { .cols { grid-template-columns: minmax(0, 1fr); } .viewer { position: static; max-height: none; } .cl { max-height: 40vh; } }
</style>
