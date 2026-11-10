<script lang="ts">
  // REA, the video reverse engineer. Upload a video (or take one from the Library); the engine measures what is visible in it: shots, camera, light, colour.
  // It reports what it measures. It does not recover how the video was made, and this page says so.
  import { onMount } from 'svelte';
  import { get } from '$lib/api';
  import { app } from '$lib/session.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import ExpBack from '$lib/components/ExpBack.svelte';
  import MediaCard from '$lib/components/chat/MediaCard.svelte';
  import MediaAssetPicker from '$lib/components/media/MediaAssetPicker.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import { Upload, precheck } from '$lib/media/upload.svelte';
  import { fmtDuration, fmtSize, type Asset } from '$lib/media/assets';
  import { blocksOf, findReaProfile, loadAnalysis, startAnalysis, type Analysis, type ReaProfile } from '$lib/media/rea';

  const ui = $derived(app.me.ui);
  let profile = $state<ReaProfile | null>(null), loading = $state(true), loadErr = $state('');
  let video = $state<Asset | null>(null), up = $state<Upload | null>(null), pickerOpen = $state(false), note = $state(''), file: HTMLInputElement | undefined = $state();
  let job = $state<any>(null), starting = $state(false), err = $state('');
  let analysis = $state<Analysis | null>(null), analysisState = $state<'idle' | 'loading' | 'absent' | 'error'>('idle'), analysisErr = $state('');
  const maxMb = $derived<number>(ui.composer.media_upload_max_mb ?? 512);
  const busy = $derived(!!up && up.active);
  const running = $derived(starting || (!!job && (job.status === 'queued' || job.status === 'running')));

  onMount(async () => {
    if (!ui.operations?.video?.enabled) { loading = false; return; }
    try { profile = findReaProfile(await get('/api/media/options?kind=video')); } catch (e) { loadErr = errText(e); }
    loading = false;
  });
  function choose(e: Event) {
    const i = e.currentTarget as HTMLInputElement, f = i.files?.[0]; i.value = ''; if (!f) return;
    note = '';
    if (!/^video\//.test(f.type) && !/\.(mp4|m4v|mov|webm|mkv|avi)$/i.test(f.name)) { note = `${f.name} is not a video.`; return; }
    const why = precheck(f, maxMb); if (why) { note = why; return; }
    up?.cancel(); const u = (up = new Upload(f, false)); video = null;
    u.start().then((a) => { if (up === u) { if (a) video = a; else if (u.status === 'error') note = u.error; } });
  }
  async function go() {
    if (!profile || !video || running) return;
    err = ''; analysis = null; analysisState = 'idle'; starting = true;
    try { job = await startAnalysis(profile, video.id); if (job.status === 'completed') await changed(job); } catch (e) { err = errText(e); } finally { starting = false; }
  }
  async function changed(j: any) {
    job = j;
    if (j.status !== 'completed') return;
    analysisState = 'loading';
    try { analysis = await loadAnalysis(j.id); analysisState = analysis ? 'idle' : 'absent'; } catch (e) { analysisErr = errText(e); analysisState = 'error'; }
  }
  const blocks = $derived(analysis ? blocksOf(analysis) : []);
</script>
<svelte:head><title>REA · Experimental</title></svelte:head>
<div class="wrap">
  <div class="head"><ExpBack /><h1>REA, video reverse engineer</h1></div>
  <p class="muted lead">Upload a video and the engine measures what is visible in it: shots, camera movement, light, colour and transitions, with a plan you can read. It describes the result. It cannot recover the prompt or the model that made a video.</p>

  {#if loading}<p class="muted" role="status">Checking what this server offers…</p>
  {:else if !ui.operations?.video?.enabled}<div class="notice warn" role="status"><div><strong>Videos are not available to you.</strong> {ui.operations?.video?.reason}</div></div>
  {:else if loadErr}<div class="notice bad" role="alert"><div>{loadErr}</div></div>
  {:else if !profile}
    <div class="notice info" role="status" data-testid="rea-absent"><div><strong>REA is not available on this server.</strong> The video engine does not offer an analysis tool here. Ask your administrator whether it is planned.</div></div>
  {:else}
    {#if !profile.ready}<div class="notice warn" role="status"><div><strong>REA is set up but cannot run yet.</strong> {profile.reason ?? 'The server did not say why.'}</div></div>{/if}
    <section class="pick" aria-label="Video to analyse">
      {#if video || up}
        <div class="vcard">
          <span class="vt">{#if video}<MediaThumb id={video.id} kind="video" name="" duration={video.duration} ratio="16 / 9" />{:else if up?.local}<!-- svelte-ignore a11y_media_has_caption --><video src={up.local} muted preload="metadata"></video>{/if}</span>
          <span class="vn"><strong>{video?.name ?? up?.file.name}</strong>
            {#if busy}<span class="tiny muted" role="status">{up?.status === 'processing' ? 'Processing…' : `Uploading ${Math.round((up?.fraction ?? 0) * 100)}%`}</span>
            {:else if video}<span class="tiny muted">{[video.width && video.height ? `${video.width}×${video.height}` : '', video.duration ? fmtDuration(video.duration) : '', fmtSize(video.size)].filter(Boolean).join(' · ')}</span>{/if}</span>
          <button type="button" class="btn ghost sm" onclick={() => { up?.cancel(); up = null; video = null; job = null; analysis = null; }}><Icon name="x" size={14} /> Remove</button>
        </div>
      {:else}
        <div class="adds">
          <button type="button" class="btn" onclick={() => file?.click()} disabled={!ui.composer.media_library}><Icon name="upload" size={15} /> Upload a video</button>
          <button type="button" class="btn" onclick={() => (pickerOpen = true)} disabled={!ui.composer.media_library}><Icon name="library" size={15} /> From Library</button>
          <span class="tiny muted">Up to {maxMb} MB. Very long or very large videos may be refused by the engine, which says why.</span>
        </div>
      {/if}
      <input bind:this={file} type="file" accept="video/*" class="sr-only" tabindex="-1" aria-label="Choose a video" onchange={choose} />
      {#if note}<p class="err small" role="alert">{note}</p>{/if}
      <div class="go"><button type="button" class="btn primary" disabled={!video || busy || running || !profile.ready} onclick={go} data-testid="rea-go">{running ? 'Analysing…' : 'Analyse video'}</button>
        <span class="tiny muted" role="status">{!video ? 'Add a video first.' : busy ? 'Wait for the upload to finish.' : ''}</span></div>
      {#if err}<div class="notice bad" role="alert"><div>{err}</div></div>{/if}
    </section>

    {#if job}
      <section class="res" aria-label="Result" aria-live="polite">
        {#key job.id}<MediaCard {job} kind="image" large onchange={changed} />{/key}
        {#if analysisState === 'loading'}<p class="muted small" role="status">Reading the analysis…</p>
        {:else if analysisState === 'absent'}
          <div class="notice info" role="status" data-testid="rea-no-analysis"><div><strong>The full analysis is not available from this server yet.</strong> The contact sheet above is what the engine returned for this video. The shot-by-shot analysis and plan are ready on the engine, but this server does not pass them on.</div></div>
        {:else if analysisState === 'error'}<div class="notice bad" role="alert"><div>{analysisErr}</div></div>{/if}
        {#each blocks as b (b.title)}
          <div class="blk"><h2>{b.title}</h2>
            {#if b.kind === 'text'}<p>{b.text}</p>
            {:else if b.kind === 'pairs'}<dl class="kv">{#each b.pairs ?? [] as [k, v] (k)}<dt>{k}</dt><dd>{v}</dd>{/each}</dl>
            {:else if b.kind === 'list'}<ul>{#each b.items ?? [] as it}<li>{it}</li>{/each}</ul>
            {:else}<div class="tablewrap"><table class="t"><caption class="sr-only">{b.title}</caption><thead><tr>{#each b.columns ?? [] as c}<th scope="col">{c}</th>{/each}</tr></thead>
              <tbody>{#each b.rows ?? [] as r, i (i)}<tr>{#each r as c}<td style="white-space: normal">{c}</td>{/each}</tr>{/each}</tbody></table></div>{/if}
          </div>
        {/each}
      </section>
    {/if}
    <MediaAssetPicker bind:open={pickerOpen} accept="video" title="Choose a video to analyse" onpick={(a) => { up?.cancel(); up = null; video = a[0]; }} />
  {/if}
</div>
<style>
  .wrap { max-width: 900px; width: 100%; margin: 0 auto; padding: 16px 24px 48px; display: grid; gap: 14px; align-content: start; }
  .head { display: flex; align-items: center; } .lead { max-width: 66ch; margin: 0; }
  .pick { display: grid; gap: 12px; } .adds { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .vcard { display: grid; grid-template-columns: 128px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); }
  .vt { display: block; border-radius: var(--radius-sm); overflow: hidden; } .vt video { width: 100%; display: block; } .vn { display: grid; gap: 2px; min-width: 0; } .vn strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
  .go { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
  .res { display: grid; gap: 16px; } .blk h2 { font-size: 15px; margin-bottom: 6px; } .blk p { margin: 0; } .blk ul { margin: 0; padding-left: 20px; }
  @media (max-width: 899px) { .wrap { padding: 12px 16px 32px; } .vcard { grid-template-columns: 96px minmax(0, 1fr); } .vcard :global(.btn) { grid-column: 1 / -1; justify-self: start; } }
</style>
