<script lang="ts">
  // Every picture and video this person generated, newest first, with the conversation it came from.
  import { onMount } from 'svelte';
  import { get, del, qs } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { app } from '$lib/session.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import MediaProtection from '$lib/components/media/MediaProtection.svelte';

  const ops = $derived(app.me.ui.operations ?? {});
  let kind = $state<'' | 'image' | 'video'>(''), jobs = $state<any[]>([]), loading = $state(true), error = $state('');
  let confirm = $state<any>(null), protOpen = $state(false);
  async function load() {
    loading = true;
    try { jobs = await get(`/api/media/jobs${qs({ kind: kind || undefined })}`); error = ''; }
    catch (e) { error = errText(e); jobs = []; }
    finally { loading = false; }
  }
  // without picture or video access the server refuses the list too, so say so instead of asking
  onMount(() => { if (ops.image?.enabled || ops.video?.enabled) load(); else { loading = false; error = 'Pictures and video are not available to you, so there is nothing to show here.'; } });
  const items = $derived(jobs.flatMap((j) => (j.assets ?? []).map((a: any) => ({ j, a }))));
  const running = $derived(jobs.filter((j) => j.status === 'queued' || j.status === 'running').length);
  async function remove() {
    const j = confirm; confirm = null; if (!j) return;
    try { await del(`/api/media/jobs/${j.id}`); jobs = jobs.filter((x) => x.id !== j.id); toast('Deleted'); } catch (e) { toast(errText(e), 'bad'); }
  }
  const when = (t: number) => new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  const src = (a: any) => `/api/media/assets/${a.id}/content`;
</script>

<svelte:head><title>Library</title></svelte:head>
<div class="lib">
  <header class="lh">
    <h1>Library</h1>
    <div class="row">
      <div class="seg" role="radiogroup" aria-label="Show">
        {#each [['', 'All'], ['image', 'Pictures'], ['video', 'Videos']] as [k, l] (k)}
          {#if !k || ops[k]?.enabled}<button role="radio" aria-checked={kind === k} class:on={kind === k} onclick={() => { kind = k as typeof kind; load(); }}>{l}</button>{/if}
        {/each}
      </div>
      <button class="btn ghost sm" onclick={() => (protOpen = true)}>How is this stored?</button>
    </div>
  </header>
  {#if running}<p class="small muted" role="status">{running} still generating. They appear here when finished.</p>{/if}
  {#if loading}<p class="muted" role="status">Loading…</p>
  {:else if error}<div class="notice bad" role="alert"><div>{error}</div></div>
  {:else if !items.length}
    <div class="emp"><p>Nothing here yet.</p><p class="small muted">Choose Picture or Video in the message box of any conversation to make one.</p><a class="btn" href="/chat">Open a conversation</a></div>
  {:else}
    <ul class="grid">
      {#each items as { j, a } (a.id)}
        <li class="card">
          <a class="media" href={src(a)} target="_blank" rel="noopener" aria-label={`Open: ${j.prompt}`}>
            {#if a.kind === 'video' || a.mime?.startsWith('video/')}
              <!-- svelte-ignore a11y_media_has_caption -->
              <video src={src(a)} preload="metadata" muted playsinline></video><span class="badge"><Icon name="video" size={14} /></span>
            {:else}<img src={src(a)} alt="" loading="lazy" />{/if}
          </a>
          <div class="meta">
            <p class="pr" title={j.prompt}>{j.prompt}</p>
            <p class="tiny muted">{[j.model, a.width && a.height ? `${a.width}×${a.height}` : null, when(a.created_at ?? j.created_at)].filter(Boolean).join(' · ')}</p>
            <div class="acts">
              {#if j.conversation_id}<a class="btn ghost sm" href={`/chat/${j.conversation_id}`}><Icon name="chat" size={14} /> Conversation</a>{/if}
              <a class="btn ghost icon sm" href={`${src(a)}?download=true`} download aria-label="Download" title="Download"><Icon name="download" size={15} /></a>
              <button class="btn ghost icon sm" aria-label="Delete" title="Delete" onclick={() => (confirm = j)}><Icon name="trash" size={15} /></button>
            </div>
            {#if !a.saved}<p class="tiny muted">Not saved: download it to keep it.</p>{/if}
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</div>

<Modal open={!!confirm} title="Delete this result?">
  <p>The {confirm?.kind === 'video' ? 'video' : 'picture'} is deleted from this server. {confirm?.conversation_id ? 'Its conversation keeps the request, marked as no longer available.' : ''}</p>
  {#snippet footer()}<button class="btn" onclick={() => (confirm = null)}>Cancel</button><button class="btn danger solid" onclick={remove}>Delete</button>{/snippet}
</Modal>
<Modal bind:open={protOpen} title="How are pictures and videos stored?" wide><MediaProtection kind={kind || 'image'} />{#snippet footer()}<button class="btn" onclick={() => (protOpen = false)}>Close</button>{/snippet}</Modal>

<style>
  .lib { padding: 20px 24px 32px; max-width: 1200px; width: 100%; margin: 0 auto; display: grid; gap: 12px; align-content: start; }
  .lh { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 2px; gap: 2px; }
  .seg button { all: unset; padding: 0 10px; height: 28px; display: inline-flex; align-items: center; border-radius: calc(var(--radius-sm) - 2px); cursor: pointer; font-size: 13px; }
  .seg button.on { background: var(--selected); font-weight: 600; } .seg button:focus-visible { outline: 2px solid var(--accent); }
  .grid { list-style: none; margin: 0; padding: 0; display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
  .card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); overflow: hidden; display: flex; flex-direction: column; }
  .media { position: relative; display: block; aspect-ratio: 1; background: var(--bg-2); }
  .media img, .media video { width: 100%; height: 100%; object-fit: cover; display: block; }
  .badge { position: absolute; left: 8px; top: 8px; background: rgba(0, 0, 0, 0.6); color: #fff; border-radius: 4px; padding: 2px 4px; display: grid; }
  .meta { padding: 8px 10px; display: grid; gap: 2px; }
  .pr { margin: 0; font-size: 13px; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; }
  .meta p { margin: 0; }
  .acts { display: flex; gap: 2px; margin-top: 4px; }
  .emp { padding: 40px 0; display: grid; gap: 8px; justify-items: start; } .emp p { margin: 0; }
  @media (max-width: 719px) { .lib { padding: 12px; } .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; } }
</style>
