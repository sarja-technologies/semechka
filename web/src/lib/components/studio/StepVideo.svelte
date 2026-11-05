<script lang="ts">
  // Step 1: the video. It is chosen once per project; choosing another one starts a new project (this one stays in Projects).
  import Icon from '$lib/components/Icon.svelte';
  import MediaAssetPicker from '$lib/components/media/MediaAssetPicker.svelte';
  import UploadRows from './UploadRows.svelte';
  import { facts, fmtDuration, fmtSize, playbackNote } from '$lib/media/assets';
  import type { Studio } from '$lib/studio/store.svelte';
  let { s }: { s: Studio } = $props();
  const a = $derived(s.source);
  let pickOpen = $state(false), input: HTMLInputElement | undefined = $state();
  const orient = $derived(!a?.width || !a?.height ? '' : a.width === a.height ? 'Square' : a.width > a.height ? 'Landscape' : 'Portrait');
  function chosen(e: Event) { const i = e.currentTarget as HTMLInputElement; if (i.files) s.addFiles([...i.files]); i.value = ''; }
  async function pickLib(list: any[]) { const v = list.find((x) => x.kind === 'video'); if (v) await s.startProject(v); }
</script>

<div class="st-h"><h2>Your video</h2><p>This is the video you will edit. You can drop another one anywhere on this page, or paste it.</p></div>
{#if a}
  <dl class="facts" data-testid="video-facts">
    <div><dt>Name</dt><dd title={a.name}>{a.name ?? 'Video'}</dd></div>
    <div><dt>Length</dt><dd>{fmtDuration(a.duration, true)}</dd></div>
    <div><dt>Picture</dt><dd>{a.width}×{a.height} · {orient}</dd></div>
    <div><dt>File size</dt><dd>{fmtSize(a.size)}</dd></div>
    {#if a.media?.fps}<div><dt>Frame rate</dt><dd>{Math.round(a.media.fps * 100) / 100} fps</dd></div>{/if}
    <div><dt>Sound</dt><dd>{a.media?.audio === false ? 'None' : a.media?.audio ? 'Yes' : '–'}</dd></div>
  </dl>
  {#if playbackNote(a)}<p class="st-note warn">{playbackNote(a)}</p>{/if}
  <p class="st-note">{facts(a).join(' · ')}. The original stays untouched: trimming and cropping make copies.</p>
{/if}
<UploadRows {s} kind="video" />
<div class="st-row">
  <button type="button" class="btn" onclick={() => input?.click()}><Icon name="upload" size={15} /> Use a different video</button>
  <button type="button" class="btn" onclick={() => (pickOpen = true)}><Icon name="library" size={15} /> From your Library</button>
  <input bind:this={input} type="file" accept="video/*,.mkv,.mov" hidden onchange={chosen} />
</div>
<MediaAssetPicker bind:open={pickOpen} accept="video" title="Choose a video" onpick={pickLib} />
{#if s.notice}<p class="st-note warn" role="status">{s.notice}</p>{/if}
<div class="st-foot"><span></span><button type="button" class="btn primary" onclick={() => (s.step = 'prepare')}>Continue to Prepare <Icon name="chevron" size={14} /></button></div>

<style>
  .facts { margin: 0 0 var(--s3); display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; }
  .facts div { padding: 8px 10px; border-radius: 9px; background: var(--m-card); border: 1px solid var(--border); min-width: 0; }
  dt { font-size: 11px; color: var(--fg-muted); } dd { margin: 0; font-weight: 600; font-size: 13px; font-variant-numeric: tabular-nums; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
</style>
