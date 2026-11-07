<script lang="ts">
  // Shown until a mode is chosen: how to begin, and the latest projects.
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import { MODES, modeState } from '$lib/imajutsu/caps';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s }: { s: Imajutsu } = $props();
  let input: HTMLInputElement | undefined = $state();
  const first = $derived(MODES.find((m) => modeState(s.caps, m.key).ok));
  function chosen(e: Event) { const i = e.currentTarget as HTMLInputElement; if (i.files) s.addFiles([...i.files], 'source'); i.value = ''; }
  const edit = $derived(modeState(s.caps, 'edit').ok);
</script>
<section class="start" data-testid="start" aria-labelledby="start-h">
  <Icon name="imajutsu" size={28} />
  <h2 id="start-h">What do you want to make?</h2>
  <p>Pick a mode above. {first ? 'Most people start with ' + (modeState(s.caps, 'iphone_photo').ok ? 'iPhone Photo or Generate Image' : first.label) + ' to make a picture from words' : 'No mode is available on this server right now'}{edit ? ', or Edit Image to change one of their own.' : '.'}</p>
  {#if edit}
    <div class="st-row"><button type="button" class="btn" onclick={() => input?.click()} data-testid="start-file"><Icon name="upload" size={15} /> Choose a picture to edit</button><span class="tiny muted">or drop one anywhere on this page</span></div>
    <input bind:this={input} type="file" accept="image/*" hidden onchange={chosen} />
  {/if}
  {#if s.notice}<p class="st-note warn" role="status">{s.notice}</p>{/if}
  {#if s.projects.length}
    <h3>Pick up where you left off</h3>
    <ul class="recent" aria-label="Recent projects">
      {#each s.projects.slice(0, 4) as p (p.id)}
        <li><span class="th">{#if p.thumbnail || p.thumb_asset}<MediaThumb id={(p.thumbnail || p.thumb_asset) as string} kind="image" ratio="1 / 1" badge={false} />{:else}<Icon name="image" size={18} />{/if}</span>
          <span class="tx"><strong>{p.title || 'Untitled project'}</strong></span><button type="button" class="btn sm" onclick={() => s.open(p.id)}>Open</button></li>
      {/each}
    </ul>
  {/if}
</section>
<style>
  .start { display: grid; justify-items: start; gap: 10px; padding: var(--s5); border: 1px dashed var(--border-strong); border-radius: var(--radius-menu); background: var(--card-bg); max-width: 720px; margin: 0 auto; width: 100%; } p { margin: 0; color: var(--fg-2); max-width: 60ch; line-height: 1.5; } h3 { font-size: 13px; margin-top: 8px; }
  .recent { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; width: 100%; } li { display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; gap: 10px; align-items: center; padding: 6px; border: 1px solid var(--border); border-radius: var(--radius); }
  .th { width: 44px; height: 44px; border-radius: 8px; overflow: hidden; background: var(--m-card); display: grid; place-items: center; color: var(--fg-muted); } strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; display: block; }
</style>
