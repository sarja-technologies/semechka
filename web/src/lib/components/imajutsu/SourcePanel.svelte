<script lang="ts">
  // Panel: Source. The picture to work on. Drop it anywhere on the page, paste it, choose a file or take one from the Library.
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import MediaAssetPicker from '$lib/components/media/MediaAssetPicker.svelte';
  import UploadRows from '$lib/components/studio/UploadRows.svelte';
  import { openViewer } from '$lib/media/viewer.svelte';
  import { fmtDims } from '$lib/media/assets';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s, n = 0 }: { s: Imajutsu; n?: number } = $props();
  import PanelCard from './PanelCard.svelte';
  let pick = $state(false), input: HTMLInputElement | undefined = $state();
  const src = $derived(s.source);
  const pending = $derived(s.uploads.filter((u) => s.targetOf(u) === 'source' && u.status !== 'ready'));
  function chosen(e: Event) { const i = e.currentTarget as HTMLInputElement; if (i.files) s.addFiles([...i.files], 'source'); i.value = ''; }
</script>

<PanelCard id="source" title="Your picture" {n} hint={s.spec.mode === 'outpaint' ? 'The picture to make bigger. It is never changed; the result is a new picture.' : 'The picture to work on. It is never changed; every result is a new picture.'}>
  {#if src}
    <div class="row" data-testid="source-card">
      <button type="button" class="th" onclick={() => openViewer(src, { siblings: [src] })} aria-label={`Open ${src.name ?? 'the source picture'} larger`}><MediaThumb id={src.id} kind="image" name={src.name ?? 'Source picture'} ratio={`${src.width || 1} / ${src.height || 1}`} fit="contain" /></button>
      <div class="tx"><strong title={src.name}>{src.name ?? 'Picture'}</strong><span class="tiny muted" data-testid="source-dims">{fmtDims(src)}</span></div>
      <div class="btns"><button type="button" class="btn sm" onclick={() => input?.click()}>Replace</button><button type="button" class="btn sm ghost" onclick={() => s.clearSource()} aria-label="Remove the source picture"><Icon name="x" size={14} /></button></div>
    </div>
  {:else}
    <div class="empty" data-testid="source-empty"><Icon name="image" size={22} /><strong>Drop a picture here, or paste one</strong><span class="tiny muted">PNG, JPEG or WebP</span>
      <div class="btns"><button type="button" class="btn" onclick={() => input?.click()}><Icon name="upload" size={15} /> Choose a picture</button><button type="button" class="btn" onclick={() => (pick = true)}><Icon name="library" size={15} /> From your Library</button></div></div>
  {/if}
  {#if src}<div class="btns"><button type="button" class="btn sm" onclick={() => (pick = true)}><Icon name="library" size={14} /> From your Library</button></div>{/if}
  <input bind:this={input} type="file" accept="image/*" hidden onchange={chosen} data-testid="source-file" />
  {#if pending.length}<UploadRows {s} uploads={pending} />{/if}
  {#if s.notice}<p class="st-note warn" role="status">{s.notice}</p>{/if}
</PanelCard>
<MediaAssetPicker bind:open={pick} accept="image" title="Choose the source picture" onpick={(l) => l[0] && s.setSource(l[0])} />

<style>
  .row { display: grid; grid-template-columns: 84px minmax(0, 1fr) auto; gap: 12px; align-items: center; }
  .th { all: unset; display: block; width: 84px; height: 84px; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); cursor: zoom-in; } .th:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .tx { display: grid; min-width: 0; } strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .btns { display: flex; gap: 8px; flex-wrap: wrap; }
  .empty { display: grid; justify-items: center; gap: 6px; text-align: center; padding: 18px 12px; border: 1.5px dashed var(--border-strong); border-radius: var(--radius); color: var(--fg-muted); } .empty strong { color: var(--fg); font-size: 13.5px; }
  @media (max-width: 560px) { .row { grid-template-columns: 64px minmax(0, 1fr); } .th { width: 64px; height: 64px; } .row .btns { grid-column: 1 / -1; } }
</style>
