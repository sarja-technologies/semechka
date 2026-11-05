<script lang="ts">
  // The video stage: one player that stays put while the person moves through the steps. What it draws depends on the step:
  //  - Video / Prepare (trim, crop): the whole picture, with the trim timeline under it or the crop frame over it.
  //  - Everything after that, and the Aspect / Resolution tools: the finished framing (what the model will receive), with the first frame
  //    available for marking the subject.
  // Playback is always limited to the kept part of the clip, because that is the only part that is ever used.
  import { onDestroy } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import MediaCropEditor, { type Rect } from '$lib/components/media/MediaCropEditor.svelte';
  import VideoTrimmer from '$lib/components/media/VideoTrimmer.svelte';
  import SubjectOverlay from './SubjectOverlay.svelte';
  import { contentUrl, fmtDuration, playbackNote } from '$lib/media/assets';
  import { openViewer } from '$lib/media/viewer.svelte';
  import { playing, stopped } from '$lib/media/playback';
  import { fitInside, framing, fullRect, isFull, targetRatio } from '$lib/studio/spec';
  import { subjectSupport } from '$lib/studio/caps';
  import type { Studio } from '$lib/studio/store.svelte';

  let { s }: { s: Studio } = $props();

  const a = $derived(s.source);
  const sw = $derived(s.sw || 16), sh = $derived(s.sh || 9);
  const dur = $derived(s.duration);
  const early = $derived(s.step === 'video' || s.step === 'prepare');
  const showTrim = $derived(s.step === 'prepare' && s.tool === 'trim' && !!a && dur > 0);
  const showCrop = $derived(s.step === 'prepare' && s.tool === 'crop' && !!a);
  const framed = $derived(!(early && (s.tool === null || s.tool === 'trim' || s.tool === 'crop')));
  const support = $derived(subjectSupport(s.mode, s.entry));
  const picking = $derived(s.step === 'operation' && s.usesVideo);
  const frozen = $derived(picking && !support.scrub);
  const fr = $derived(framed ? framing(s.spec, sw, sh) : { ratio: sw / sh, content: fullRect(sw, sh), treatment: 'crop' as const, fill: { w: 1, h: 1 } });
  const c = $derived(fr.content);
  const videoStyle = $derived(`width:${(sw / c.w) * 100}%;height:${(sh / c.h) * 100}%;left:${-(c.x / c.w) * 100}%;top:${-(c.y / c.h) * 100}%`);
  const contentStyle = $derived(`left:${((1 - fr.fill.w) / 2) * 100}%;top:${((1 - fr.fill.h) / 2) * 100}%;width:${fr.fill.w * 100}%;height:${fr.fill.h * 100}%`);

  let el: HTMLVideoElement | undefined = $state();
  let cur = $state(0), paused = $state(true), muted = $state(false), loop = $state(true), vErr = $state('');
  let raf = 0;
  const lo = $derived(s.trimStart), hi = $derived(s.trimEnd || dur);
  const trimmed = $derived(!!s.spec.trim);
  const note = $derived(a ? playbackNote(a) : '');

  // ---- playback limited to the kept part -------------------------------------------------------------------------------
  function seek(t: number) { if (!el) return; const v = Math.max(0, Math.min(dur || t, t)); el.currentTime = v; cur = v; }
  function frame() {
    raf = 0;
    if (!el) return;
    cur = el.currentTime;
    if (!el.paused && trimmed && hi > 0 && cur >= hi - 0.03) {
      if (loop) { el.currentTime = lo; cur = lo; } else { el.pause(); el.currentTime = lo; cur = lo; }
    }
    if (!el.paused) raf = requestAnimationFrame(frame);
  }
  function toggle() {
    if (!el) return;
    if (el.paused) {
      if (trimmed && (el.currentTime < lo - 0.02 || el.currentTime >= hi - 0.05)) seek(lo);
      el.play().catch(() => {});
    } else el.pause();
  }
  function onplay() { paused = false; if (el) playing(el); if (!raf) raf = requestAnimationFrame(frame); }
  function onpause() { paused = true; if (el) stopped(el); cancelAnimationFrame(raf); raf = 0; cur = el?.currentTime ?? cur; }
  onDestroy(() => { cancelAnimationFrame(raf); if (el) { el.pause(); stopped(el); } });

  // A new video starts at its first kept frame. The operation step shows exactly the first frame the engine will start from.
  $effect(() => { void a?.id; vErr = ''; cur = 0; });
  $effect(() => { if (frozen && el) { el.pause(); const t = lo; if (Math.abs(el.currentTime - t) > 0.02) seek(t); } });
  $effect(() => { if (!frozen && !showTrim && el && trimmed && el.paused && (cur < lo - 0.02 || cur > hi + 0.02)) seek(lo); });
  $effect(() => { s.cur = cur; });
  const metadata = () => { if (el && frozen) seek(lo); };

  // ---- trim timeline ---------------------------------------------------------------------------------------------------------
  let ts = $state(0), te = $state(0);
  $effect(() => { ts = s.trimStart; te = s.trimEnd || dur; });
  function trimmedTo(which: 'start' | 'end') {
    void which;
    const whole = ts <= 0.001 && te >= dur - 0.001;
    s.edit((sp) => { sp.trim = whole ? null : { start: round(ts), end: round(te) }; });
  }
  const round = (v: number) => Math.round(v * 1000) / 1000;
  function useFirst() { const m = s.maxSeconds; if (!m) return; te = Math.min(dur, ts + m); s.edit((sp) => { sp.trim = { start: round(ts), end: round(te) }; }); seek(ts); }
  const tooLong = $derived(!!s.maxSeconds && s.usesVideo && s.keptSeconds > s.maxSeconds + 0.01);

  // ---- crop frame --------------------------------------------------------------------------------------------------------------
  const lockedRatio = $derived(s.spec.frame.lock && s.spec.frame.treatment === 'crop' ? targetRatio(s.spec.frame) : null);
  function cropped(r: Rect) { s.edit((sp) => { sp.crop = isFull(r, sw, sh) ? null : { x: r.x, y: r.y, w: r.w, h: r.h }; }); }
  $effect(() => { // entering the crop tool with a shape chosen: start from the largest frame of that shape
    if (showCrop && !s.spec.crop && lockedRatio) s.edit((sp) => { sp.crop = fitInside(fullRect(sw, sh), lockedRatio); });
  });

  const openFull = () => { if (a) openViewer(a, {}); };
  const orient = $derived(sw === sh ? 'square' : sw > sh ? 'landscape' : 'portrait');
</script>

<div class="stage" data-testid="stage">
  {#if !a}
    <div class="empty"><Icon name="video" size={30} /><p>Your video appears here.</p></div>
  {:else}
    <div class="frame" class:pad={fr.treatment === 'pad' && framed} style:--ratio={fr.ratio} data-testid="stage-frame" data-framed={framed}>
      <div class="content" style={contentStyle}>
        <!-- svelte-ignore a11y_media_has_caption -->
        <video bind:this={el} src={contentUrl(a)} playsinline preload="metadata" {muted} aria-label="Video preview" style={videoStyle}
          onplay={onplay} onpause={onpause} onseeked={() => (cur = el?.currentTime ?? 0)} onloadedmetadata={metadata} onended={() => { paused = true; }}
          onerror={() => (vErr = 'This browser cannot play this video here.')}></video>
      </div>
      {#if showCrop}<MediaCropEditor w={sw} h={sh} rect={s.spec.crop} aspect={lockedRatio} even min={32} onchange={cropped} />{/if}
      {#if picking}
        <SubjectOverlay mode={s.pick} bind:box={s.spec.subject.box} bind:points={s.spec.subject.points}
          onchange={() => s.edit(() => {})} />
      {/if}
      {#if !s.usesVideo}<span class="unused" role="status">This operation does not use the video</span>{/if}
      {#if vErr}<span class="unused bad" role="alert">{vErr}</span>{/if}
    </div>

    {#if note}<p class="pnote" role="status">{note}</p>{/if}

    {#if frozen}
      <div class="bar"><span class="tiny cap"><Icon name="film" size={13} /> First frame of the clip, as the model will see it</span>
        <button type="button" class="ib" onclick={openFull} aria-label="Open the full player" title="Open the full player"><Icon name="expand" size={15} /></button></div>
    {:else}
      <div class="bar">
        <button type="button" class="ib pp" onclick={toggle} aria-label={paused ? 'Play' : 'Pause'}><Icon name={paused ? 'play' : 'pause'} size={16} /></button>
        <span class="time" aria-label="Playback position">{fmtDuration(cur, true)} <i>/ {fmtDuration(trimmed ? hi : dur, true)}</i></span>
        <input class="scrub" type="range" min={trimmed && !showTrim ? lo : 0} max={trimmed && !showTrim ? hi : dur || 1} step="0.01" value={cur} aria-label="Seek"
          oninput={(e) => seek(Number(e.currentTarget.value))} />
        {#if trimmed}<button type="button" class="ib txt" aria-pressed={loop} onclick={() => (loop = !loop)} title="Repeat the kept part"><Icon name="refresh" size={14} /> Loop</button>{/if}
        <button type="button" class="ib" aria-pressed={muted} onclick={() => (muted = !muted)} aria-label={muted ? 'Turn sound on' : 'Mute'} title={muted ? 'Turn sound on' : 'Mute'}><Icon name={muted ? 'volumeoff' : 'volume'} size={15} /></button>
        <button type="button" class="ib" onclick={openFull} aria-label="Open the full player" title="Open the full player"><Icon name="expand" size={15} /></button>
      </div>
    {/if}

    {#if showTrim}
      <div class="dock" data-testid="trim-dock">
        <VideoTrimmer asset={a} duration={dur} bind:start={ts} bind:end={te} current={cur} onseek={seek} oninput={trimmedTo} />
        <p class="max" class:warn={tooLong} role="status">
          {#if s.maxSeconds}
            {#if tooLong}Your selection is {s.keptSeconds.toFixed(1)} s but this model takes up to {s.maxSeconds} s. Shorten it, or <button type="button" class="linkish" onclick={useFirst}>use {s.maxSeconds} s from the start</button>.
            {:else}This model takes up to {s.maxSeconds} s of video. {#if dur > s.maxSeconds}Your video is {dur.toFixed(1)} s, so trim it down.{/if}{/if}
          {:else}No maximum length is advertised for the chosen model.{/if}
        </p>
      </div>
    {/if}

    {#if early && !showTrim}
      <p class="facts tiny">{orient} · {sw}×{sh}{dur ? ` · ${fmtDuration(dur, true)}` : ''}</p>
    {/if}
  {/if}
</div>

<style>
  .stage { background: var(--m-stage); color: var(--m-chrome-fg); border-radius: 14px; padding: 12px; display: grid; grid-template-columns: minmax(0, 1fr); gap: 10px; min-width: 0; --sh: min(54vh, 560px); }
  .empty { min-height: 240px; display: grid; place-content: center; justify-items: center; gap: 6px; color: var(--m-chrome-muted); }
  .frame { position: relative; margin: 0 auto; aspect-ratio: var(--ratio); width: min(100%, calc(var(--sh) * var(--ratio))); background: #000; border-radius: 8px; touch-action: manipulation; }
  .content { position: absolute; overflow: hidden; border-radius: inherit; }
  video { position: absolute; display: block; object-fit: fill; background: #000; max-width: none; }
  .unused { position: absolute; left: 8px; bottom: 8px; right: 8px; z-index: 4; padding: 5px 9px; border-radius: 7px; font-size: 12px; background: rgba(14, 15, 16, 0.82); color: #f2f2f2; text-align: center; }
  .unused.bad { background: rgba(140, 36, 32, 0.9); }
  .pnote { margin: 0; font-size: 12.5px; color: #e6c27a; }
  .bar { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; min-height: 36px; }
  .cap { display: inline-flex; align-items: center; gap: 6px; color: var(--m-chrome-muted); margin-right: auto; }
  .time { font-variant-numeric: tabular-nums; font-size: 12.5px; white-space: nowrap; } .time i { color: var(--m-chrome-muted); font-style: normal; }
  .scrub { flex: 1 1 120px; min-width: 60px; accent-color: #fff; height: 28px; }
  .ib { all: unset; box-sizing: border-box; min-width: 36px; height: 36px; border-radius: 9px; display: inline-flex; align-items: center; justify-content: center; gap: 5px; cursor: pointer; color: var(--m-chrome-fg); font-size: 12.5px; padding: 0 6px; }
  .ib.txt { padding: 0 10px; border: 1px solid var(--m-chrome-line); }
  .ib:hover { background: rgba(255, 255, 255, 0.1); } .ib[aria-pressed='true'].txt { background: rgba(255, 255, 255, 0.16); border-color: rgba(255, 255, 255, 0.35); }
  .ib:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 1px; } .ib.pp { background: rgba(255, 255, 255, 0.12); min-width: 40px; }
  .ib :global(svg) { flex: none; } .ib.pp :global(svg) { fill: currentColor; }
  @media (pointer: coarse) { .ib { min-width: 44px; height: 44px; } .scrub { height: 44px; } }
  .dock { display: grid; gap: 8px; padding-top: 6px; border-top: 1px solid var(--m-chrome-line); }
  .max { margin: 0; font-size: 12.5px; color: var(--m-chrome-muted); } .max.warn { color: #f0c36a; } .max :global(.linkish) { color: #fff; }
  .facts { margin: 0; color: var(--m-chrome-muted); text-transform: capitalize; }
  @media (max-width: 899px) { .stage { --sh: min(40vh, 420px); padding: 10px; } }
</style>
