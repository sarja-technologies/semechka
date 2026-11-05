<script lang="ts">
  // The first screen: choose a video. Drag one anywhere on the page, paste it, pick a file, or take one from the Library. Earlier projects wait below.
  import Icon from '$lib/components/Icon.svelte';
  import MediaAssetPicker from '$lib/components/media/MediaAssetPicker.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import UploadRows from './UploadRows.svelte';
  import type { Studio } from '$lib/studio/store.svelte';
  let { s }: { s: Studio } = $props();
  let input: HTMLInputElement | undefined = $state(), pickOpen = $state(false);
  const chosen = (e: Event) => { const i = e.currentTarget as HTMLInputElement; if (i.files) s.addFiles([...i.files]); i.value = ''; };
  const when = (t?: number | string) => (t ? new Date(typeof t === 'number' ? t * 1000 : t).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '');
</script>

<div class="start" data-testid="start">
  <div class="drop">
    <span class="ico"><Icon name="video" size={26} /></span>
    <h2>Start with a video</h2>
    <p>Drop it here, paste it, or choose it. Pictures, trimming and the rest come next.</p>
    <div class="st-row c">
      <button type="button" class="btn primary" onclick={() => input?.click()}><Icon name="upload" size={15} /> Choose a video</button>
      <button type="button" class="btn" onclick={() => (pickOpen = true)}><Icon name="library" size={15} /> From your Library</button>
    </div>
    <input bind:this={input} type="file" accept="video/*,.mkv,.mov" hidden onchange={chosen} aria-label="Choose a video file" />
    <UploadRows {s} kind="video" />
    {#if s.notice}<p class="st-note warn" role="status">{s.notice}</p>{/if}
  </div>
  {#if s.projects.length}
    <section class="rec" aria-label="Earlier projects">
      <h3>Continue an earlier project</h3>
      <ul>
        {#each s.projects.slice(0, 6) as p (p.id)}
          <li><button type="button" onclick={() => s.open(p.id)}>
            <span class="th">{#if p.thumbnail || p.thumb_asset}<MediaThumb id={(p.thumbnail || p.thumb_asset) as string} kind="video" ratio="16 / 9" badge={false} />{:else}<Icon name="film" size={18} />{/if}</span>
            <span class="tx"><strong>{p.title || 'Untitled project'}</strong><span class="tiny muted">{when(p.updated_at)}</span></span></button></li>
        {/each}
      </ul>
    </section>
  {/if}
</div>
<MediaAssetPicker bind:open={pickOpen} accept="video" title="Choose a video" onpick={(l) => { const v = l.find((x) => x.kind === 'video'); if (v) s.startProject(v); }} />

<style>
  .start { display: grid; gap: var(--s5); max-width: 720px; margin: var(--s6) auto; width: 100%; }
  .drop { display: grid; justify-items: center; gap: 10px; text-align: center; padding: 40px 24px; border: 1.5px dashed var(--border-strong); border-radius: calc(var(--radius) * 1.6); background: var(--m-card); }
  .ico { width: 52px; height: 52px; border-radius: 50%; display: grid; place-items: center; background: var(--m-ring-soft); color: var(--m-ring); } .drop p { margin: 0; color: var(--fg-muted); max-width: 46ch; } .drop :global(.up) { width: 100%; text-align: left; } .c { justify-content: center; }
  .rec h3 { font-size: 14px; margin-bottom: 8px; } ul { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px; }
  li button { all: unset; box-sizing: border-box; width: 100%; display: flex; gap: 10px; align-items: center; padding: 8px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card-bg); cursor: pointer; } li button:hover { background: var(--hover); } li button:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .th { flex: none; width: 64px; border-radius: 7px; overflow: hidden; background: var(--m-card); display: grid; place-items: center; aspect-ratio: 16 / 9; color: var(--fg-muted); } .tx { display: grid; min-width: 0; } strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
