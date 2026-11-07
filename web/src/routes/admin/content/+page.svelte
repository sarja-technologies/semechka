<script lang="ts">
  // Conversations & media: one governed explorer over everything people have said to and made with the models on this server.
  // Nothing is listed until a reason is given; every list, every conversation, generation record, preview and download is a separate audit entry
  // that the owner sees on their account page.
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { get } from '$lib/api';
  import { can } from '$lib/session.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import AuditBanner from '$lib/components/admin/AuditBanner.svelte';
  import ReasonDialog from '$lib/components/admin/ReasonDialog.svelte';
  import ConversationExplorer from '$lib/components/admin/ConversationExplorer.svelte';
  import MediaLibrary from '$lib/components/admin/MediaLibrary.svelte';
  import FileViewer from '$lib/components/admin/FileViewer.svelte';
  import MediaDrawer from '$lib/components/admin/MediaDrawer.svelte';
  import { loadMeta, readJob, type Meta } from '$lib/admin/client';
  import { forgetReason } from '$lib/admin/reason.svelte';
  import { errMsg } from '$lib/components/admin/util';
  import type { FileRef, Filters } from '$lib/admin/explorer';

  let policy = $state<string | null>(null), meta = $state<Meta | null>(null), err = $state('');
  let tab = $state<'conversations' | 'media'>(page.url.searchParams.get('tab') === 'media' ? 'media' : 'conversations');
  let scope = $state(''), file = $state<FileRef | null>(null), jobDetail = $state<any>(null), openId = $state('');
  const user = page.url.searchParams.get('user') ?? '';
  const initial: Filters = user ? { user_id: user } : {};
  function setTab(t: 'conversations' | 'media') { tab = t; goto(`?tab=${t}`, { replaceState: true }); }
  onMount(() => {
    (async () => {
      try { policy = (await get('/api/me/content-access')).policy; } catch { policy = null; }
      if (policy === 'audited' && can('admin.content.read')) { try { meta = await loadMeta(); scope = meta.scope; } catch (e) { err = errMsg(e); } }
    })();
    return () => forgetReason();
  });
  async function openJob(id: string) { err = ''; try { const d = await readJob(id); if (d) jobDetail = d; } catch (e) { err = errMsg(e); } }
  function showConversation(id: string) { jobDetail = null; tab = 'conversations'; openId = ''; queueMicrotask(() => (openId = id)); }
  const TABS = [['conversations', 'Conversations'], ['media', 'Media library']] as const;
  function keys(e: KeyboardEvent) {
    const i = TABS.findIndex(([k]) => k === tab);
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); setTab(TABS[(i + (e.key === 'ArrowRight' ? 1 : TABS.length - 1)) % TABS.length][0]); }
  }
</script>

<Page title="Conversations & media" lead="Read conversations and open generated pictures and videos from any model or workflow, only when there is a legitimate need such as a legal request or a security incident.">
  {#if !can('admin.content.read')}
    <div class="notice info"><div>This needs the “Read other people's conversations” permission, which no role includes by default and no wildcard grants.</div></div>
  {:else if policy === 'off'}
    <div class="notice info"><div>Turned off by the privacy policy: no administrator can read conversations or open media. An owner can change this in <a href="/admin/privacy">Privacy &amp; Retention</a>; the change itself is recorded.</div></div>
  {:else if policy === null}
    <div class="notice warn" role="alert"><div>The privacy policy could not be read. Reload the page to try again.</div></div>
  {:else}
    <AuditBanner {scope} />
    {#if err}<div class="notice bad" role="alert"><div>{err}</div></div>{/if}
    <div class="tabs" role="tablist" aria-label="Explorer sections" tabindex="-1" onkeydown={keys}>
      {#each TABS as [k, label] (k)}<button role="tab" id={`tab-${k}`} aria-selected={tab === k} aria-controls={`pn-${k}`} tabindex={tab === k ? 0 : -1} class:on={tab === k} onclick={() => setTab(k)}>{label}</button>{/each}
    </div>
    <div role="tabpanel" id="pn-conversations" aria-labelledby="tab-conversations" hidden={tab !== 'conversations'}>
      <ConversationExplorer {meta} {initial} {openId} onjob={openJob} onfile={(f) => (file = f)} onscope={(s) => (scope = s)} />
    </div>
    <div role="tabpanel" id="pn-media" aria-labelledby="tab-media" hidden={tab !== 'media'}>
      {#if tab === 'media'}<MediaLibrary {meta} initial={tab === 'media' && user ? initial : {}} onfile={(f) => (file = f)} onconversation={showConversation} onscope={(s) => (scope = s)} />{/if}
    </div>
    <MediaDrawer detail={jobDetail} onclose={() => (jobDetail = null)} onfile={(f) => (file = f)} onconversation={showConversation} />
    <FileViewer bind:file />
  {/if}
</Page>
<ReasonDialog />

<style>
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: var(--s4); }
  .tabs button { all: unset; box-sizing: border-box; cursor: pointer; padding: 0 16px; min-height: 40px; display: inline-flex; align-items: center; font-weight: 500; color: var(--fg-muted); border-bottom: 2px solid transparent; margin-bottom: -1px; }
  .tabs button:hover { color: var(--fg); } .tabs button.on { color: var(--fg); border-bottom-color: var(--accent); }
  .tabs button:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; border-radius: var(--radius-sm); }
</style>
