<script lang="ts">
  // Governed access to someone else's conversations. Nothing is listed or shown until a reason is given; every list
  // and every conversation opened is a separate audit record that the owner sees on their account page.
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { get, post } from '$lib/api';
  import { can } from '$lib/session.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import { errMsg, fmtTime } from '$lib/components/admin/util';
  import { loadUsers, type Opt } from '$lib/components/admin/lookup';

  let policy = $state<string | null>(null), users = $state<Opt[]>([]), who = $state(page.url.searchParams.get('user') ?? '');
  let reason = $state(''), convs = $state<any[] | null>(null), open = $state<any>(null), err = $state(''), busy = $state(false);
  onMount(async () => {
    try { policy = (await get('/api/me/content-access')).policy; } catch { policy = null; }
    loadUsers().then((u) => (users = u)).catch((e) => (err = errMsg(e)));
  });
  const ok = $derived(reason.trim().length >= 10);
  const person = $derived(users.find((u) => u.id === who)?.label ?? '');
  async function list() {
    err = ''; busy = true; open = null;
    try { convs = await post(`/api/admin/content/users/${who}/conversations`, { reason }); }
    catch (e) { err = errMsg(e); convs = null; } finally { busy = false; }
  }
  async function read(id: string) {
    err = ''; busy = true;
    try { open = await post(`/api/admin/content/conversations/${id}`, { reason }); }
    catch (e) { err = errMsg(e); } finally { busy = false; }
  }
</script>

<Page title="Conversation access" lead="Read another person's conversations only when there is a legitimate need, such as a legal request or a security incident. Each time you list or open their conversations, the access is recorded with your name and reason, and they can see it in their account.">
  {#if !can('admin.content.read')}
    <div class="notice info"><div>This needs the “Read other people's conversations” permission, which no role includes by default and no wildcard grants.</div></div>
  {:else if policy === 'off'}
    <div class="notice info"><div>Turned off by the privacy policy: no administrator can read conversations. An owner can change this in <a href="/admin/privacy">Privacy &amp; Retention</a>; the change itself is recorded.</div></div>
  {:else}
    <section class="panel"><div class="panel-b stack">
      <div class="two">
        <div class="field"><label for="cw">Person</label>
          <select id="cw" class="input" bind:value={who} onchange={() => { convs = null; open = null; }}><option value="">Choose…</option>{#each users as u (u.id)}<option value={u.id}>{u.label}</option>{/each}</select></div>
        <div class="field"><label for="cr">Reason</label><input id="cr" class="input" maxlength="500" bind:value={reason} placeholder="e.g. Legal hold request 2026-114 from counsel" aria-describedby="crh" />
          <span id="crh" class="hint">At least 10 characters. Recorded with every access and shown to the person.</span></div>
      </div>
      <div class="row"><button class="btn primary" disabled={!who || !ok || busy} onclick={list}>List their conversations</button>
        {#if who && ok}<span class="small muted">This records one access: you listed {person}'s conversations.</span>{/if}</div>
      {#if err}<div class="notice bad" role="alert"><div>{err}</div></div>{/if}
    </div></section>

    {#if convs}
      <div class="cols">
        <section class="panel"><div class="panel-h"><h3>Conversations ({convs.length})</h3></div>
          <ul class="cl">
            {#each convs as c (c.id)}
              <li><button class="ci" class:on={open?.id === c.id} onclick={() => read(c.id)} disabled={busy}><span>{c.title || 'Untitled'}</span><span class="tiny muted">{c.mode} · {fmtTime(c.updated_at)}</span></button></li>
            {:else}<li class="small muted pad">No saved conversations.</li>{/each}
          </ul>
          <p class="hint pad">Opening one records another access.</p>
        </section>
        <section class="panel" aria-live="polite">
          {#if open}
            <div class="panel-h"><h3>{open.title || 'Untitled'}</h3><span class="chip warn">Recorded access</span></div>
            <div class="panel-b">
              <p class="hint" style="margin-top:0">Owner: {open.owner.email}. Showing the current version of each reply{open.branches ? `; ${open.branches} earlier version${open.branches === 1 ? ' is' : 's are'} not shown` : ''}. Attachments are counted, not opened.</p>
              <ol class="msgs">
                {#each open.messages as m (m.id)}
                  <li class={m.role}><div class="meta tiny muted">{m.role === 'user' ? 'Person' : m.model || 'Assistant'} · {fmtTime(m.created_at)}{m.attachments ? ` · ${m.attachments} attachment${m.attachments === 1 ? '' : 's'}` : ''}{m.status && m.status !== 'complete' ? ` · ${m.status}` : ''}</div><div class="body">{m.content}</div></li>
                {/each}
              </ol>
            </div>
          {:else}<div class="panel-b muted small">Choose a conversation to open it.</div>{/if}
        </section>
      </div>
    {/if}
  {/if}
</Page>

<style>
  .two { display: grid; gap: 4px 16px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); }
  .cols { display: grid; grid-template-columns: minmax(240px, 1fr) minmax(0, 2fr); gap: 16px; margin-top: 16px; align-items: start; }
  .cl { list-style: none; margin: 0; padding: 0; max-height: 60vh; overflow: auto; }
  .ci { all: unset; box-sizing: border-box; width: 100%; display: grid; gap: 2px; padding: 8px 16px; border-top: 1px solid var(--border); cursor: pointer; font-size: 14px; }
  .ci:hover { background: var(--hover); } .ci.on { background: var(--selected); } .ci:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .pad { padding: 8px 16px; margin: 0; }
  .msgs { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; }
  .msgs li { border-left: 2px solid var(--border-strong); padding-left: 12px; }
  .msgs li.user { border-color: var(--accent); }
  .body { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; }
  @media (max-width: 860px) { .cols { grid-template-columns: minmax(0, 1fr); } }
</style>
