<script lang="ts">
  // Everything this person keeps: pictures and videos they generated, uploaded or edited. Open any of it in the viewer (play, zoom, trim, crop),
  // use it in a new message, download it or delete it. Files can be dropped here to add them.
  import { onMount, untrack } from 'svelte';
  import { goto } from '$app/navigation';
  import { del } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { app } from '$lib/session.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import MediaDropzone from '$lib/components/media/MediaDropzone.svelte';
  import MediaProtection from '$lib/components/media/MediaProtection.svelte';
  import { contentUrl, facts, listAssets, type Asset } from '$lib/media/assets';
  import { Upload, isMedia, precheck } from '$lib/media/upload.svelte';
  import { trayFor } from '$lib/media/tray.svelte';
  import { openViewer } from '$lib/media/viewer.svelte';

  const ui = $derived(app.me.ui);
  const ops = $derived(ui.operations ?? {});
  const allowed = $derived(!!ui.composer.media_library);
  let kind = $state<'' | 'image' | 'video'>(''), origin = $state<'' | 'upload' | 'edit' | 'generated'>(''), items = $state<Asset[]>([]), loading = $state(true), more = $state(false), error = $state('');
  let confirm = $state<Asset | null>(null), protOpen = $state(false), ups = $state<Upload[]>([]), note = $state('');

  async function load(reset = true) {
    loading = true;
    try {
      const got = await listAssets({ kind: kind || undefined, origin: origin || undefined, limit: 60, before: reset ? undefined : items[items.length - 1]?.created_at });
      items = reset ? got : [...items, ...got]; more = got.length >= 60; error = '';
    } catch (e) { error = errText(e); if (reset) items = []; } finally { loading = false; }
  }
  onMount(() => { if (allowed) load(); else { loading = false; error = 'Pictures and video are not available to you, so there is nothing to show here.'; } });

  async function addFiles(files: File[]) {
    note = '';
    for (const f of files) {
      if (!isMedia(f)) { note = `${f.name} is not a picture or a video.`; continue; }
      const why = precheck(f, ui.composer.media_upload_max_mb ?? 512);
      if (why) { note = why; continue; }
      const u = new Upload(f, false);
      ups = [u, ...ups];
      u.start().then((a) => {
        if (a) { items = [a, ...items]; setTimeout(() => (ups = ups.filter((x) => x !== u)), 600); }
        else if (u.status === 'cancelled') ups = ups.filter((x) => x !== u);
      });
    }
  }
  function pick(e: Event) { const i = e.currentTarget as HTMLInputElement; if (i.files) addFiles([...i.files]); i.value = ''; }

  const openAsset = (a: Asset, edit = false) => openViewer(a, { edit, siblings: items, useLabel: 'Use in a new message', onsaved: (n) => { items = [n, ...items.filter((x) => x.id !== n.id)]; }, onuse: (n) => useIn(n) });
  /** Start a new message with this file attached. The tray is kept for the whole session, so it is waiting when the composer opens. */
  function useIn(a: Asset) {
    trayFor('chat:new', app.me.user.id).addAsset(a, []);
    const op = a.kind === 'video' && ops.video?.enabled ? 'video' : ops.image?.enabled ? 'image' : '';
    goto(`/chat?new=${Date.now()}${op ? `&op=${op}` : ''}`);
  }
  async function remove() {
    const a = confirm; confirm = null; if (!a) return;
    try {
      if (a.job?.id && a.origin === 'generated') await del(`/api/media/jobs/${a.job.id}`); else await del(`/api/media/assets/${a.id}`);
      items = items.filter((x) => (a.job?.id && a.origin === 'generated' ? x.job?.id !== a.job.id : x.id !== a.id)); toast('Deleted');
    } catch (e) { toast(errText(e), 'bad'); }
  }
  const when = (t?: number) => (t ? new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '');
  const label = (a: Asset) => a.origin === 'generated' ? a.job?.prompt || a.prompt || (a.kind === 'video' ? 'Generated video' : 'Generated picture') : a.name ?? (a.kind === 'video' ? 'Video' : 'Picture');
  const originLabel = (a: Asset) => ({ upload: 'Uploaded', edit: 'Edited', generated: 'Generated', prepared: 'Prepared', export: 'Exported' })[a.origin ?? 'generated'];
</script>

<svelte:head><title>Library</title></svelte:head>
<MediaDropzone onfiles={addFiles} disabled={!allowed} hint="Drop pictures or videos to add them to your Library" sub={`Up to ${ui.composer.media_upload_max_mb ?? 512} MB each`}>
  <div class="lib">
    <header class="lh">
      <h1>Library</h1>
      <div class="row">
        <div class="seg" role="radiogroup" aria-label="Show">
          {#each [['', 'All'], ['image', 'Pictures'], ['video', 'Videos']] as [k, l] (k)}
            <button role="radio" aria-checked={kind === k} class:on={kind === k} onclick={() => { kind = k as typeof kind; load(); }}>{l}</button>
          {/each}
        </div>
        <div class="seg" role="radiogroup" aria-label="Made by">
          {#each [['', 'Any'], ['upload', 'Uploaded'], ['edit', 'Edited'], ['generated', 'Generated']] as [k, l] (k)}
            <button role="radio" aria-checked={origin === k} class:on={origin === k} onclick={() => { origin = k as typeof origin; load(); }}>{l}</button>
          {/each}
        </div>
        {#if allowed}
          <label class="btn sm"><Icon name="upload" size={14} /> Add<input type="file" multiple accept="image/*,video/*" class="sr-only" onchange={pick} /></label>
        {/if}
        <button class="btn ghost sm" onclick={() => (protOpen = true)}>How is this stored?</button>
      </div>
    </header>
    {#if note}<p class="small err" role="status">{note}</p>{/if}
    {#if loading && !items.length}<p class="muted" role="status">Loading…</p>
    {:else if error}<div class="notice bad" role="alert"><div>{error}</div></div>
    {:else if !items.length && !ups.length}
      <div class="emp"><p>Nothing here yet.</p><p class="small muted">Drop a picture or video anywhere on this page, or choose Picture or Video in the message box to make one.</p><a class="btn" href="/chat">Open a conversation</a></div>
    {:else}
      <ul class="grid">
        {#each ups as u (u.key)}
          <li class="card up">
            <div class="media"><MediaThumb kind={u.file.type.startsWith('video/') ? 'video' : 'image'} src={u.local} name={u.file.name} badge={false} ratio="1 / 1" /></div>
            <div class="meta">
              <p class="pr">{u.file.name}</p>
              <p class="tiny muted" role="status">{u.status === 'error' ? u.error : u.status === 'processing' ? 'Preparing…' : `Uploading ${Math.round(u.fraction * 100)}%`}</p>
              <div class="acts">
                {#if u.status === 'error'}<button class="btn ghost sm" onclick={() => u.retry()}>Retry</button>{/if}
                <button class="btn ghost sm" onclick={() => { u.cancel(); ups = ups.filter((x) => x !== u); }}>{u.status === 'error' ? 'Dismiss' : 'Cancel'}</button>
              </div>
            </div>
          </li>
        {/each}
        {#each items as a (a.id)}
          <li class="card">
            <button class="media" onclick={() => openAsset(a)} aria-label={`Open ${label(a)}`}>
              <MediaThumb id={a.id} kind={a.kind} name={label(a)} duration={a.duration} ratio="1 / 1" />
              {#if a.kind === 'video'}<span class="play" aria-hidden="true"><Icon name="play" size={16} /></span>{/if}
              <span class="badge">{originLabel(a)}</span>
            </button>
            <div class="meta">
              <p class="pr" title={label(a)}>{label(a)}</p>
              <p class="tiny muted">{[...facts(a).slice(0, 2), when(a.created_at)].filter(Boolean).join(' · ')}</p>
              <div class="acts">
                <button class="btn ghost sm" onclick={() => useIn(a)} title="Start a message with this"><Icon name="plus" size={14} /> Use</button>
                {#if a.mime !== 'image/gif'}<button class="btn ghost icon sm" onclick={() => openAsset(a, true)} aria-label="Edit" title={a.kind === 'video' ? 'Trim or crop' : 'Crop or rotate'}><Icon name={a.kind === 'video' ? 'scissors' : 'crop'} size={15} /></button>{/if}
                {#if a.job?.conversation_id}<a class="btn ghost icon sm" href={`/chat/${a.job.conversation_id}`} aria-label="Open its conversation" title="Conversation"><Icon name="chat" size={15} /></a>{/if}
                <a class="btn ghost icon sm" href={contentUrl(a, { download: true })} download aria-label="Download" title="Download"><Icon name="download" size={15} /></a>
                <button class="btn ghost icon sm" aria-label="Delete" title="Delete" onclick={() => (confirm = a)}><Icon name="trash" size={15} /></button>
              </div>
              {#if !a.saved}<p class="tiny muted">Not saved: download it to keep it.</p>{/if}
            </div>
          </li>
        {/each}
      </ul>
      {#if more}<button class="btn" onclick={() => load(false)} disabled={loading}>{loading ? 'Loading…' : 'Show more'}</button>{/if}
    {/if}
  </div>
</MediaDropzone>

<Modal open={!!confirm} title="Delete this file?">
  <p>The {confirm?.kind === 'video' ? 'video' : 'picture'} is deleted from this server.{confirm?.origin === 'generated' && confirm?.job?.conversation_id ? ' Its conversation keeps the request, marked as no longer available.' : ''} {confirm?.origin !== 'generated' ? 'Copies you edited from it are separate files and stay.' : ''}</p>
  {#snippet footer()}<button class="btn" onclick={() => (confirm = null)}>Cancel</button><button class="btn danger solid" onclick={remove}>Delete</button>{/snippet}
</Modal>
<Modal bind:open={protOpen} title="How are pictures and videos stored?" wide><MediaProtection kind={kind || 'image'} />{#snippet footer()}<button class="btn" onclick={() => (protOpen = false)}>Close</button>{/snippet}</Modal>

<style>
  .lib { padding: 20px 24px 32px; max-width: 1200px; width: 100%; margin: 0 auto; display: grid; gap: 12px; align-content: start; }
  .lh { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }
  .lh .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .seg { display: inline-flex; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 2px; gap: 2px; }
  .seg button { all: unset; padding: 0 10px; height: 28px; display: inline-flex; align-items: center; border-radius: calc(var(--radius-sm) - 2px); cursor: pointer; font-size: 13px; }
  .seg button.on { background: var(--selected); font-weight: 600; } .seg button:focus-visible { outline: 2px solid var(--m-ring); }
  .grid { list-style: none; margin: 0; padding: 0; display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(212px, 1fr)); }
  .card { border: 1px solid var(--border); border-radius: 14px; background: var(--card-bg); overflow: hidden; display: flex; flex-direction: column; transition: border-color var(--fast) var(--ease), box-shadow var(--fast) var(--ease); }
  .card:hover { border-color: var(--border-strong); box-shadow: var(--shadow-sm); }
  .media { all: unset; position: relative; display: block; cursor: pointer; aspect-ratio: 1; background: var(--bg-2); overflow: hidden; }
  .media:focus-visible { outline: 2px solid var(--m-ring); outline-offset: -2px; }
  .play { position: absolute; left: 50%; top: 50%; width: 38px; height: 38px; margin: -19px 0 0 -19px; border-radius: 50%; display: grid; place-items: center; background: rgba(14, 15, 16, 0.62); color: #fff; backdrop-filter: blur(6px); transition: transform var(--fast) var(--ease); }
  .play :global(svg) { margin-left: 2px; fill: currentColor; } .media:hover .play { transform: scale(1.08); }
  .badge { position: absolute; left: 8px; top: 8px; font-size: 11px; line-height: 1; padding: 4px 7px; border-radius: 999px; background: rgba(14, 15, 16, 0.66); color: #fff; backdrop-filter: blur(4px); }
  .meta { padding: 10px 12px 8px; display: grid; gap: 2px; }
  .pr { margin: 0; font-size: 13px; font-weight: 500; overflow: hidden; display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; }
  .meta p { margin: 0; } .acts { display: flex; gap: 2px; margin-top: 6px; flex-wrap: wrap; }
  .up .media { cursor: default; } .up { opacity: 0.9; }
  .emp { padding: 40px 0; display: grid; gap: 8px; justify-items: start; } .emp p { margin: 0; }
  @media (max-width: 719px) { .lib { padding: 12px; } .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; } .acts .btn.sm { padding: 0 6px; } }
</style>
