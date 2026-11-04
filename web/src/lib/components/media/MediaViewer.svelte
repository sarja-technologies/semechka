<script lang="ts">
  // Full-screen viewer and editor for a picture or a video. Opens from anywhere (see lib/media/viewer.svelte.ts).
  //  - Pictures: fit, zoom (wheel, pinch, buttons), pan, 100%.
  //  - Videos: large player, scrubber, volume, speed, fullscreen, frame stepping, keyboard shortcuts.
  //  - Edit (only when asked for): trim with a frame timeline, crop with presets, rotate (pictures), remove sound. Saving makes a NEW file from
  //    the original with ffmpeg on the server; the original is never changed.
  import { tick } from 'svelte';
  import { toast } from '$lib/toast.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import MediaCropEditor, { type Rect } from './MediaCropEditor.svelte';
  import VideoTrimmer from './VideoTrimmer.svelte';
  import { viewer, closeViewer } from '$lib/media/viewer.svelte';
  import { contentUrl, fmtDuration, fmtSize, isLibraryId, playbackNote, type Asset } from '$lib/media/assets';
  import { PRESETS, fitRect, runEdit, type EditHandle } from '$lib/media/edit';
  import { playing, stopped } from '$lib/media/playback';

  const asset = $derived<Asset | null>(viewer.asset);
  const opts = $derived(viewer.opts);
  const isVideo = $derived(asset?.kind === 'video');
  const sw = $derived(asset?.width || 16), sh = $derived(asset?.height || 9);
  const gif = $derived(asset?.mime === 'image/gif');
  /** only Library files can be edited (trim, crop); a Work artifact or an older attachment is view-only */
  const editable = $derived(!!asset && isLibraryId(asset.id) && !gif);

  let root: HTMLDivElement | undefined = $state(), stage: HTMLDivElement | undefined = $state(), vid: HTMLVideoElement | undefined = $state();
  let stageW = $state(800), stageH = $state(500), full = $state(false);
  let editing = $state(false), cropOn = $state(false), previewCrop = $state(false);
  let crop = $state<Rect | null>(null), preset = $state('free'), rotate = $state(0), flip = $state(false), mute = $state(false);
  let trimStart = $state(0), trimEnd = $state(0);
  let saving = $state<{ p: number } | null>(null), err = $state(''), handle: EditHandle | null = null;
  // playback
  let cur = $state(0), dur = $state(0), paused = $state(true), vol = $state(1), muted = $state(false), rate = $state(1), buffered = $state(0), vErr = $state('');
  // zoom (pictures)
  let scale = $state(1), tx = $state(0), ty = $state(0), panning = $state(false);

  const duration = $derived(dur || asset?.duration || 0);
  const fps = $derived(asset?.media?.fps && asset.media.fps > 0 ? asset.media.fps : 30);
  const rw = $derived(rotate % 180 ? sh : sw), rh = $derived(rotate % 180 ? sw : sh);
  const cropEff = $derived<Rect | null>(cropOn && crop && !(crop.x === 0 && crop.y === 0 && crop.w >= rw && crop.h >= rh) ? crop : null);
  const trimmed = $derived(isVideo && editing && (trimStart > 0.001 || trimEnd < duration - 0.001));
  const dirty = $derived(!!cropEff || trimmed || (!isVideo && (rotate !== 0 || flip)) || (isVideo && mute));

  // what is drawn: the whole frame, or (preview) only the crop
  const shown = $derived(previewCrop && cropEff ? { w: cropEff.w, h: cropEff.h } : { w: rw, h: rh });
  const box = $derived.by(() => {
    const aw = Math.max(80, stageW - 32), ah = Math.max(80, stageH - 24);
    const k = Math.min(aw / shown.w, ah / shown.h, isVideo ? 4 : 1e9);
    return { w: Math.round(shown.w * k), h: Math.round(shown.h * k) };
  });
  const inner = $derived(previewCrop && cropEff
    ? { w: (rw / cropEff.w) * 100, h: (rh / cropEff.h) * 100, l: -(cropEff.x / cropEff.w) * 100, t: -(cropEff.y / cropEff.h) * 100 }
    : { w: 100, h: 100, l: 0, t: 0 });

  // ---- lifecycle: a new asset resets everything -------------------------------------------------------------------------
  let lastId = '';
  $effect(() => {
    const a = asset;
    if (!a) { lastId = ''; return; }
    if (a.id === lastId) return;
    lastId = a.id;
    editing = !!opts.edit && (a.mime !== 'image/gif'); cropOn = false; previewCrop = false; crop = null; preset = 'free'; rotate = 0; flip = false; mute = false;
    trimStart = 0; trimEnd = a.duration ?? 0; err = ''; saving = null; scale = 1; tx = ty = 0; cur = 0; dur = a.duration ?? 0; paused = true; vErr = '';
    if (editing && a.kind === 'video') tick().then(() => startCrop(false));
    tick().then(() => root?.focus());
  });
  $effect(() => {
    if (!stage) return;
    const ro = new ResizeObserver(() => { stageW = stage!.clientWidth; stageH = stage!.clientHeight; });
    ro.observe(stage); stageW = stage.clientWidth; stageH = stage.clientHeight;
    return () => ro.disconnect();
  });
  $effect(() => { const f = () => (full = document.fullscreenElement === root); document.addEventListener('fullscreenchange', f); return () => document.removeEventListener('fullscreenchange', f); });
  // the page behind does not scroll while the viewer is open
  $effect(() => { if (!asset) return; const o = document.body.style.overflow; document.body.style.overflow = 'hidden'; return () => { document.body.style.overflow = o; }; });

  function close() {
    handle?.cancel();
    if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
    if (vid) { vid.pause(); stopped(vid); }
    closeViewer();
  }

  // ---- video -----------------------------------------------------------------------------------------------------------
  function meta() { if (vid && isFinite(vid.duration)) { dur = vid.duration; if (!trimEnd || trimEnd > dur) trimEnd = dur; } }
  function tick_() {
    if (!vid) return;
    cur = vid.currentTime;
    if (vid.buffered.length) buffered = vid.buffered.end(vid.buffered.length - 1);
    if (editing && trimmed && !vid.paused && cur >= trimEnd - 0.02) { vid.pause(); vid.currentTime = trimStart; cur = trimStart; }
  }
  function toggle() {
    if (!vid) return;
    if (vid.paused) {
      if (editing && trimmed && (vid.currentTime < trimStart - 0.02 || vid.currentTime >= trimEnd - 0.05)) vid.currentTime = trimStart;
      vid.play().catch(() => {});
    } else vid.pause();
  }
  function seek(t: number) { if (!vid) return; const v = Math.max(0, Math.min(duration, t)); vid.currentTime = v; cur = v; }
  function step(frames: number) { if (!vid) return; vid.pause(); seek(vid.currentTime + frames / fps); }
  function setVol(v: number) { if (!vid) return; vid.volume = v; vid.muted = v === 0; vol = v; muted = vid.muted; }
  function toggleMute() { if (!vid) return; vid.muted = !vid.muted; muted = vid.muted; if (!vid.muted && vid.volume === 0) setVol(0.6); }
  function setRate(r: number) { rate = r; if (vid) vid.playbackRate = r; }
  const RATES = [0.5, 1, 1.5, 2];
  function fs() { if (document.fullscreenElement) document.exitFullscreen().catch(() => {}); else root?.requestFullscreen?.().catch(() => {}); }

  // ---- pictures: zoom and pan -----------------------------------------------------------------------------------------
  const pointers = new Map<number, { x: number; y: number }>();
  let pinch = 0, panned = false;
  function zoomAt(next: number, cx = 0, cy = 0) {
    const s2 = Math.max(1, Math.min(10, next));
    tx = cx - (cx - tx) * (s2 / scale); ty = cy - (cy - ty) * (s2 / scale);
    scale = s2;
    if (scale === 1) tx = ty = 0;
  }
  function rel(e: { clientX: number; clientY: number }) { const b = stage!.getBoundingClientRect(); return { x: e.clientX - b.left - b.width / 2, y: e.clientY - b.top - b.height / 2 }; }
  function wheel(e: WheelEvent) { if (isVideo) return; e.preventDefault(); const p = rel(e); zoomAt(scale * Math.exp(-e.deltaY * 0.002), p.x, p.y); }
  function pdown(e: PointerEvent) {
    if (isVideo || (e.target as HTMLElement).closest('.cropwrap')) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    panned = false;
    if (pointers.size === 2) { const [a, b] = [...pointers.values()]; pinch = Math.hypot(a.x - b.x, a.y - b.y); }
  }
  function pmove(e: PointerEvent) {
    const p = pointers.get(e.pointerId);
    if (!p) return;
    if (pointers.size === 2) {
      pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const [a, b] = [...pointers.values()], d = Math.hypot(a.x - b.x, a.y - b.y);
      if (pinch) { const c = rel({ clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2 }); zoomAt(scale * (d / pinch), c.x, c.y); }
      pinch = d; return;
    }
    if (scale > 1) { tx += e.clientX - p.x; ty += e.clientY - p.y; panning = true; panned = true; }
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
  }
  function pup(e: PointerEvent) { pointers.delete(e.pointerId); pinch = 0; panning = false; }
  function dbl(e: MouseEvent) { if (isVideo || (e.target as HTMLElement).closest('.cropwrap')) return; const p = rel(e); if (scale > 1) zoomAt(1); else zoomAt(2.5, p.x, p.y); }
  const actual = () => zoomAt(Math.max(1, sw / Math.max(1, box.w)));

  // ---- editing ------------------------------------------------------------------------------------------------------
  function startCrop(on = true) {
    cropOn = on;
    if (on) { crop = crop ?? { x: 0, y: 0, w: rw, h: rh }; if (preset !== 'free') applyPreset(preset); }
    else { previewCrop = false; }
  }
  function applyPreset(id: string) {
    preset = id; cropOn = true;
    const p = PRESETS.find((x) => x.id === id)!;
    if (p.ratio === null) { crop = crop ?? { x: 0, y: 0, w: rw, h: rh }; return; }
    crop = fitRect(rw, rh, p.ratio === -1 ? rw / rh : p.ratio, isVideo);
  }
  const aspect = $derived.by(() => { const p = PRESETS.find((x) => x.id === preset); return !p || p.ratio === null ? null : p.ratio === -1 ? rw / rh : p.ratio; });
  function rotateBy(d: number) {
    rotate = (rotate + d + 360) % 360;
    crop = null; if (cropOn) tick().then(() => { crop = { x: 0, y: 0, w: rw, h: rh }; if (preset !== 'free') applyPreset(preset); });
  }
  function leaveEdit() { editing = false; cropOn = false; previewCrop = false; crop = null; rotate = 0; flip = false; mute = false; trimStart = 0; trimEnd = duration; err = ''; }
  function enterEdit() {
    if (gif) { err = 'Animated GIFs cannot be edited here.'; return; }
    editing = true; err = ''; scale = 1; tx = ty = 0; trimEnd = duration;
    if (isVideo) startCrop(false);
  }

  async function save(mode: 'copy' | 'use') {
    if (!asset || saving) return;
    err = '';
    saving = { p: 0 };
    if (vid) vid.pause();
    const h = (handle = runEdit(asset.id, {
      trim: trimmed ? { start: trimStart, end: trimEnd } : null, crop: cropEff,
      rotate: isVideo ? 0 : rotate, flip: isVideo ? false : flip, mute: isVideo ? mute : false
    }, (p) => saving && (saving.p = p)));
    try {
      const n = await h.done;
      opts.onsaved?.(n);
      if (mode === 'use' && opts.onuse) { opts.onuse(n); close(); return; }
      toast('Saved to your Library as a new file');
      viewer.opts = { ...viewer.opts, edit: false };
      viewer.asset = n; // look at the result; the original is unchanged
    } catch (e: any) {
      if (e?.code !== 'cancelled') err = errText(e);
    } finally { saving = null; handle = null; }
  }
  function cancelSave() { handle?.cancel(); }

  // ---- keys ---------------------------------------------------------------------------------------------------------
  function key(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.stopPropagation(); if (saving) cancelSave(); else close(); return; }
    if (e.key === 'Tab' && root) {
      const f = [...root.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex]:not([tabindex="-1"])')].filter((x) => x.offsetParent !== null);
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && (document.activeElement === first || document.activeElement === root)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      return;
    }
    const t = e.target as HTMLElement;
    if (t.closest('input, textarea, select, [role="slider"], .rect') || e.ctrlKey || e.metaKey || e.altKey) return;
    const k = e.key;
    if (isVideo) {
      if (k === ' ' || k === 'k') { e.preventDefault(); toggle(); }
      else if (k === 'ArrowLeft') { e.preventDefault(); seek(cur - (e.shiftKey ? 1 : 5)); }
      else if (k === 'ArrowRight') { e.preventDefault(); seek(cur + (e.shiftKey ? 1 : 5)); }
      else if (k === 'j') seek(cur - 10); else if (k === 'l') seek(cur + 10);
      else if (k === ',') step(-1); else if (k === '.') step(1);
      else if (k === 'ArrowUp') { e.preventDefault(); setVol(Math.min(1, vol + 0.1)); } else if (k === 'ArrowDown') { e.preventDefault(); setVol(Math.max(0, vol - 0.1)); }
      else if (k === 'm') toggleMute(); else if (k === 'f') fs();
      else if (k === 'Home') seek(0); else if (k === 'End') seek(duration);
      else if (editing && k === 'i') { trimStart = Math.min(cur, trimEnd - 0.1); } else if (editing && k === 'o') { trimEnd = Math.max(cur, trimStart + 0.1); }
    } else {
      if (k === '+' || k === '=') zoomAt(scale * 1.25); else if (k === '-') zoomAt(scale / 1.25); else if (k === '0') zoomAt(1);
      else if (k === 'ArrowLeft' && !editing) nav(-1); else if (k === 'ArrowRight' && !editing) nav(1);
      else if (k === 'f') fs();
    }
    if (k === 'e' && !editing && editable) enterEdit();
  }
  const sibs = $derived((opts.siblings ?? []).filter((a) => a.kind === 'image' || a.kind === 'video'));
  const idx = $derived(sibs.findIndex((a) => a.id === asset?.id));
  function nav(d: number) { if (editing || sibs.length < 2) return; const n = sibs[(idx + d + sibs.length) % sibs.length]; if (n) viewer.asset = n; }

  const pctPlayed = $derived(duration ? (cur / duration) * 100 : 0);
  const pctBuf = $derived(duration ? (buffered / duration) * 100 : 0);
  const note = $derived(asset ? playbackNote(asset) : '');
  const origin = $derived(asset?.origin === 'edit' ? 'Edited copy' : asset?.origin === 'upload' ? 'Uploaded' : '');
  const exactCrop = $derived(cropEff ? `${Math.round(cropEff.w)} × ${Math.round(cropEff.h)}` : `${rw} × ${rh}`);
  const outDur = $derived(isVideo ? (trimmed ? trimEnd - trimStart : duration) : 0);
</script>

{#if asset}
  <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
  <div class="mv" class:full bind:this={root} role="dialog" aria-modal="true" aria-label={`${asset.name ?? (isVideo ? 'Video' : 'Picture')}${editing ? ', editing' : ''}`} tabindex="-1" onkeydown={key}>
    <header class="top">
      <div class="ttl">
        <Icon name={isVideo ? 'video' : 'image'} size={16} />
        <strong title={asset.name}>{asset.name ?? (isVideo ? 'Video' : 'Picture')}</strong>
        <span class="facts">{[`${sw}×${sh}`, isVideo && duration ? fmtDuration(duration) : '', isVideo && asset.media?.fps ? `${Math.round(asset.media.fps * 100) / 100} fps` : '', fmtSize(asset.size)].filter(Boolean).join(' · ')}</span>
        {#if origin}<span class="tag">{origin}</span>{/if}
      </div>
      <div class="acts">
        {#if sibs.length > 1 && !editing}<span class="facts">{idx + 1} / {sibs.length}</span>{/if}
        {#if !editing}
          {#if editable}<button type="button" class="tb" onclick={enterEdit} title="Edit: trim, crop{isVideo ? '' : ', rotate'} (E)"><Icon name={isVideo ? 'scissors' : 'crop'} size={15} /> Edit</button>{/if}
          <a class="tb ico" href={contentUrl(asset, { download: true })} download aria-label="Download the original" title="Download the original"><Icon name="download" size={16} /></a>
        {/if}
        <button type="button" class="tb ico" onclick={close} aria-label="Close (Esc)" title="Close (Esc)"><Icon name="x" size={17} /></button>
      </div>
    </header>

    <!-- svelte-ignore a11y_no_static_element_interactions, a11y_click_events_have_key_events -->
    <div class="stage" bind:this={stage} onwheel={wheel} onpointerdown={pdown} onpointermove={pmove} onpointerup={pup} onpointercancel={pup} ondblclick={dbl}
      class:pan={!isVideo && scale > 1} class:panning
      onclick={(e) => { if (e.target === stage && !editing && !panned) close(); panned = false; }}>
      <div class="zoomer" style:transform={`translate(${tx}px, ${ty}px) scale(${scale})`}>
        <div class="box" style:width={`${box.w}px`} style:height={`${box.h}px`}>
          <div class="clip">
            <div class="inner" style:width={`${inner.w}%`} style:height={`${inner.h}%`} style:left={`${inner.l}%`} style:top={`${inner.t}%`}>
              {#if isVideo}
                <!-- svelte-ignore a11y_media_has_caption -->
                <video bind:this={vid} src={contentUrl(asset)} playsinline preload="metadata" class="media" class:noptr={cropOn && editing}
                  onloadedmetadata={meta} ontimeupdate={tick_} onprogress={tick_} ondurationchange={meta} onplay={() => { paused = false; if (vid) playing(vid); }}
                  onpause={() => { paused = true; if (vid) stopped(vid); }} onended={() => (paused = true)} onvolumechange={() => { if (vid) { vol = vid.volume; muted = vid.muted; } }}
                  onerror={() => (vErr = 'This browser cannot play this video.')} onclick={toggle}></video>
              {:else}
                <img src={contentUrl(asset)} alt={asset.name ?? 'Picture'} draggable="false" class="media pic"
                  style:width={`${(sw / rw) * 100}%`} style:height={`${(sh / rh) * 100}%`} style:transform={`translate(-50%, -50%) ${flip ? 'scaleX(-1) ' : ''}rotate(${rotate}deg)`} />
              {/if}
            </div>
          </div>
          {#if editing && cropOn && !previewCrop && crop}
            <div class="cropwrap"><MediaCropEditor w={rw} h={rh} bind:rect={crop} {aspect} zoom={scale} even={isVideo} /></div>
          {/if}
        </div>
      </div>
      {#if sibs.length > 1 && !editing}
        <button type="button" class="nv l" onclick={() => nav(-1)} aria-label="Previous"><Icon name="chevron" size={20} /></button>
        <button type="button" class="nv r" onclick={() => nav(1)} aria-label="Next"><Icon name="chevron" size={20} /></button>
      {/if}
      {#if vErr || note}<p class="note" role="status"><Icon name="info" size={14} /> {vErr || note}</p>{/if}
      {#if saving}
        <div class="busy" role="status" aria-live="polite">
          <div class="bar"><i style:width={`${Math.max(4, saving.p * 100)}%`}></i></div>
          <span>{isVideo ? 'Making your video' : 'Making your picture'}{saving.p > 0 ? ` · ${Math.round(saving.p * 100)}%` : ''}</span>
          <button type="button" class="tb" onclick={cancelSave}>Cancel</button>
        </div>
      {/if}
    </div>

    <footer class="dock">
      {#if isVideo}
        <div class="ctl" class:dis={!!saving}>
          <button type="button" class="cb" onclick={toggle} aria-label={paused ? 'Play (Space)' : 'Pause (Space)'} title={paused ? 'Play (Space)' : 'Pause (Space)'}><Icon name={paused ? 'play' : 'pause'} size={18} /></button>
          <button type="button" class="cb sm" onclick={() => step(-1)} aria-label="Back one frame (,)" title="Back one frame ( , )"><Icon name="stepback" size={15} /></button>
          <button type="button" class="cb sm" onclick={() => step(1)} aria-label="Forward one frame (.)" title="Forward one frame ( . )"><Icon name="stepfwd" size={15} /></button>
          <span class="time" aria-label="Current time">{fmtDuration(cur, editing)}</span>
          <label class="scrub" style:--p={`${pctPlayed}%`} style:--b={`${pctBuf}%`}>
            <span class="sr-only">Seek</span>
            <input type="range" min="0" max={duration || 1} step="0.01" value={cur} oninput={(e) => seek(Number(e.currentTarget.value))} aria-valuetext={`${fmtDuration(cur)} of ${fmtDuration(duration)}`} />
          </label>
          <span class="time dim">{fmtDuration(duration, editing)}</span>
          <button type="button" class="cb sm" onclick={toggleMute} aria-label={muted ? 'Unmute (M)' : 'Mute (M)'} title={muted ? 'Unmute (M)' : 'Mute (M)'}><Icon name={muted || vol === 0 ? 'volumeoff' : 'volume'} size={16} /></button>
          <label class="vol"><span class="sr-only">Volume</span><input type="range" min="0" max="1" step="0.05" value={muted ? 0 : vol} oninput={(e) => setVol(Number(e.currentTarget.value))} style:--p={`${(muted ? 0 : vol) * 100}%`} /></label>
          <button type="button" class="cb rate" onclick={() => setRate(RATES[(RATES.indexOf(rate) + 1) % RATES.length])} aria-label={`Speed ${rate}×`} title="Playback speed">{rate}×</button>
          <button type="button" class="cb sm" onclick={fs} aria-label={full ? 'Leave full screen (F)' : 'Full screen (F)'} title="Full screen (F)"><Icon name={full ? 'exitfs' : 'fullscreen'} size={16} /></button>
        </div>
      {:else}
        <div class="ctl pic" class:dis={!!saving}>
          <button type="button" class="cb sm" onclick={() => zoomAt(scale / 1.25)} aria-label="Zoom out (-)" title="Zoom out (-)"><Icon name="zoomout" size={16} /></button>
          <span class="time">{Math.round(scale * 100)}%</span>
          <button type="button" class="cb sm" onclick={() => zoomAt(scale * 1.25)} aria-label="Zoom in (+)" title="Zoom in (+)"><Icon name="zoomin" size={16} /></button>
          <button type="button" class="cb txt" onclick={() => zoomAt(1)}>Fit</button>
          <button type="button" class="cb txt" onclick={actual}>100%</button>
          {#if editing}
            <span class="sep"></span>
            <button type="button" class="cb sm" onclick={() => rotateBy(-90)} aria-label="Rotate left" title="Rotate left"><span class="flipx"><Icon name="rotate" size={16} /></span></button>
            <button type="button" class="cb sm" onclick={() => rotateBy(90)} aria-label="Rotate right" title="Rotate right"><Icon name="rotate" size={16} /></button>
            <button type="button" class="cb sm" class:on={flip} onclick={() => (flip = !flip)} aria-pressed={flip} aria-label="Mirror" title="Mirror"><Icon name="flip" size={16} /></button>
          {/if}
          <span class="grow"></span>
          <button type="button" class="cb sm" onclick={fs} aria-label="Full screen (F)"><Icon name={full ? 'exitfs' : 'fullscreen'} size={16} /></button>
        </div>
      {/if}

      {#if editing}
        <div class="edit" class:dis={!!saving}>
          {#if isVideo && duration}
            <VideoTrimmer {asset} {duration} bind:start={trimStart} bind:end={trimEnd} current={cur} onseek={seek} />
          {/if}
          <div class="crop">
            <span class="lab"><Icon name="crop" size={14} /> Crop</span>
            <div class="chips" role="group" aria-label="Crop shape">
              {#each PRESETS as p (p.id)}
                <button type="button" class="chip" class:on={cropOn && preset === p.id} aria-pressed={cropOn && preset === p.id} onclick={() => applyPreset(p.id)}>{p.label}</button>
              {/each}
              {#if cropOn}<button type="button" class="chip off" onclick={() => { startCrop(false); crop = null; preset = 'free'; }}>No crop</button>{/if}
            </div>
            <span class="out" title="Size of the result"><Icon name="film" size={13} /> {exactCrop}{isVideo && outDur ? ` · ${fmtDuration(outDur, true)}` : ''}</span>
            {#if cropEff}<button type="button" class="chip" class:on={previewCrop} aria-pressed={previewCrop} onclick={() => (previewCrop = !previewCrop)}><Icon name="eye" size={13} /> Preview</button>{/if}
            {#if isVideo && asset.media?.audio}<button type="button" class="chip" class:on={mute} aria-pressed={mute} onclick={() => (mute = !mute)}><Icon name="volumeoff" size={13} /> Remove sound</button>{/if}
          </div>
          {#if err}<p class="err" role="alert">{err}</p>{/if}
          <div class="save">
            <span class="hint">{dirty ? 'Your original is kept. The result is saved as a new file in your Library.' : 'Trim, crop or change something to save a new version.'}</span>
            <button type="button" class="btn2" onclick={leaveEdit} disabled={!!saving}>Cancel</button>
            <button type="button" class="btn2" onclick={() => save('copy')} disabled={!dirty || !!saving}>Save copy</button>
            {#if opts.onuse}<button type="button" class="btn2 pri" onclick={() => save('use')} disabled={!dirty || !!saving}>{opts.useLabel ?? 'Use this'}</button>{/if}
          </div>
        </div>
      {:else if err}<p class="err pad" role="alert">{err}</p>{/if}
    </footer>
  </div>
{/if}

<style>
  .mv { position: fixed; inset: 0; z-index: 120; background: var(--m-stage); color: var(--m-chrome-fg); display: grid; grid-template-rows: auto minmax(0, 1fr) auto; outline: none;
    animation: mvin var(--medium) var(--ease); font-size: 13px; }
  @keyframes mvin { from { opacity: 0; } to { opacity: 1; } }
  @media (prefers-reduced-motion: reduce) { .mv { animation: none; } }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 14px; background: var(--m-chrome); border-bottom: 1px solid var(--m-chrome-line); min-width: 0; }
  .ttl { display: flex; align-items: center; gap: 8px; min-width: 0; color: var(--m-chrome-muted); }
  .ttl strong { color: var(--m-chrome-fg); font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 36ch; }
  .facts { color: var(--m-chrome-muted); font-size: 12px; white-space: nowrap; font-variant-numeric: tabular-nums; }
  .tag { font-size: 11px; padding: 2px 7px; border-radius: 999px; background: rgba(255,255,255,.09); color: var(--m-chrome-fg); white-space: nowrap; }
  .acts { display: flex; align-items: center; gap: 6px; flex: none; }
  .tb { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; justify-content: center; gap: 6px; height: 32px; padding: 0 12px; border-radius: 8px; cursor: pointer; color: var(--m-chrome-fg);
    background: rgba(255,255,255,.07); transition: background var(--fast) var(--ease); }
  .tb.ico { width: 32px; padding: 0; } .tb:hover { background: rgba(255,255,255,.14); } .tb:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }

  .stage { position: relative; min-height: 0; overflow: hidden; display: grid; place-items: center; touch-action: none; }
  .stage.pan { cursor: grab; } .stage.panning { cursor: grabbing; }
  .zoomer { transform-origin: center; will-change: transform; }
  .box { position: relative; border-radius: 4px; box-shadow: 0 10px 50px rgba(0,0,0,.55); background: #000; }
  .clip { position: absolute; inset: 0; overflow: hidden; border-radius: inherit; }
  .inner { position: absolute; }
  .media { display: block; user-select: none; }
  video.media { width: 100%; height: 100%; object-fit: fill; cursor: pointer; background: #000; } video.noptr { pointer-events: none; }
  img.pic { position: absolute; left: 50%; top: 50%; max-width: none; transform-origin: center; }
  .cropwrap { position: absolute; inset: 0; }
  .nv { all: unset; position: absolute; top: 50%; width: 40px; height: 40px; margin-top: -20px; border-radius: 50%; display: grid; place-items: center; cursor: pointer; background: rgba(14,15,16,.6); color: #fff; z-index: 3; }
  .nv.l { left: 14px; transform: scaleX(-1); } .nv.r { right: 14px; } .nv:hover { background: rgba(14,15,16,.85); } .nv:focus-visible { outline: 2px solid var(--m-ring); }
  .note { position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%); margin: 0; max-width: min(92%, 560px); display: flex; gap: 7px; align-items: flex-start; padding: 8px 12px; border-radius: 9px;
    background: var(--m-chrome); border: 1px solid var(--m-chrome-line); color: var(--m-chrome-fg); font-size: 12.5px; z-index: 4; }
  .busy { position: absolute; inset: 0; z-index: 6; display: grid; place-content: center; justify-items: center; gap: 12px; background: rgba(8,9,10,.72); backdrop-filter: blur(3px); }
  .bar { width: min(280px, 70vw); height: 4px; border-radius: 2px; background: rgba(255,255,255,.16); overflow: hidden; } .bar i { display: block; height: 100%; background: var(--m-ring); transition: width .3s linear; }

  .dock { background: var(--m-chrome); border-top: 1px solid var(--m-chrome-line); padding: 8px 14px 12px; display: grid; gap: 10px; max-height: 58vh; overflow: auto; }
  .dis { opacity: .55; pointer-events: none; }
  .ctl { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .cb { all: unset; box-sizing: border-box; min-width: 34px; height: 34px; display: inline-grid; place-items: center; border-radius: 8px; cursor: pointer; color: var(--m-chrome-fg); flex: none; }
  .cb.sm { min-width: 30px; height: 30px; color: var(--m-chrome-muted); } .cb.txt, .cb.rate { padding: 0 10px; font-size: 12px; font-variant-numeric: tabular-nums; color: var(--m-chrome-muted); }
  .cb:hover { background: rgba(255,255,255,.1); color: var(--m-chrome-fg); } .cb.on { background: var(--m-ring-soft); color: #fff; } .cb:focus-visible { outline: 2px solid var(--m-ring); }
  .time { font-size: 12px; color: var(--m-chrome-fg); font-variant-numeric: tabular-nums; min-width: 4.2ch; text-align: right; flex: none; } .time.dim { color: var(--m-chrome-muted); text-align: left; }
  .sep { width: 1px; height: 18px; background: var(--m-chrome-line); margin: 0 4px; } .grow { flex: 1; } .flipx { display: inline-grid; transform: scaleX(-1); }
  .scrub { flex: 1; min-width: 80px; display: block; }
  input[type='range'] { -webkit-appearance: none; appearance: none; width: 100%; height: 18px; background: transparent; margin: 0; cursor: pointer; }
  .scrub input { --p: 0%; --b: 0%; }
  .scrub input::-webkit-slider-runnable-track { height: 4px; border-radius: 2px;
    background: linear-gradient(to right, var(--m-ring) 0 var(--p), rgba(255,255,255,.34) var(--p) var(--b), rgba(255,255,255,.14) var(--b)); }
  .scrub input::-moz-range-track { height: 4px; border-radius: 2px; background: rgba(255,255,255,.14); }
  .scrub input::-moz-range-progress { height: 4px; border-radius: 2px; background: var(--m-ring); }
  .vol { width: 72px; flex: none; } .vol input::-webkit-slider-runnable-track { height: 4px; border-radius: 2px; background: linear-gradient(to right, #fff 0 var(--p), rgba(255,255,255,.18) var(--p)); }
  input[type='range']::-webkit-slider-thumb { -webkit-appearance: none; width: 13px; height: 13px; margin-top: -4.5px; border-radius: 50%; background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.4); }
  input[type='range']::-moz-range-thumb { width: 13px; height: 13px; border: 0; border-radius: 50%; background: #fff; }
  input[type='range']:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; border-radius: 3px; }

  .edit { display: grid; gap: 12px; padding-top: 10px; border-top: 1px solid var(--m-chrome-line); }
  .crop { display: flex; align-items: center; gap: 8px 10px; flex-wrap: wrap; }
  .lab { display: inline-flex; align-items: center; gap: 6px; color: var(--m-chrome-muted); font-size: 12px; }
  .chips { display: inline-flex; gap: 4px; flex-wrap: wrap; }
  .chip { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 5px; height: 28px; padding: 0 11px; border-radius: 999px; cursor: pointer; font-size: 12px; color: var(--m-chrome-fg);
    background: rgba(255,255,255,.07); border: 1px solid transparent; transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease); }
  .chip:hover { background: rgba(255,255,255,.13); } .chip.on { background: var(--m-ring-soft); border-color: var(--m-ring); color: #fff; } .chip.off { color: var(--m-chrome-muted); }
  .chip:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .out { margin-left: auto; display: inline-flex; align-items: center; gap: 5px; color: var(--m-chrome-fg); font-size: 12px; font-variant-numeric: tabular-nums; }
  .save { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; justify-content: flex-end; }
  .hint { margin-right: auto; color: var(--m-chrome-muted); font-size: 12px; }
  .btn2 { all: unset; box-sizing: border-box; height: 34px; padding: 0 16px; border-radius: 9px; cursor: pointer; display: inline-flex; align-items: center; background: rgba(255,255,255,.09); color: var(--m-chrome-fg); font-weight: 500; }
  .btn2:hover:not(:disabled) { background: rgba(255,255,255,.16); } .btn2:disabled { opacity: .4; cursor: default; } .btn2:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .btn2.pri { background: #f1f2f2; color: #141516; } .btn2.pri:hover:not(:disabled) { background: #fff; }
  .err { margin: 0; color: #f4a6a2; font-size: 12.5px; } .err.pad { padding: 0 2px; }
  @media (max-width: 720px) {
    .top { padding: 8px 10px; } .ttl strong { max-width: 18ch; } .ttl .facts { display: none; }
    .dock { padding: 6px 10px 10px; } .vol, .cb.rate { display: none; } .time.dim { display: none; } .out { margin-left: 0; }
    .hint { display: none; } .save { justify-content: stretch; } .btn2 { flex: 1; justify-content: center; }
  }
</style>
