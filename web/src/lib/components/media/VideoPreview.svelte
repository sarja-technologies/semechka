<script lang="ts">
  // A video as a card: its picture with a play button. Play starts it right here, small; the expand button (or a click on the picture while
  // it plays) opens the full viewer. Nothing is downloaded until play is pressed.
  import { onDestroy } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from './MediaThumb.svelte';
  import { contentUrl, playbackNote, type Asset } from '$lib/media/assets';
  import { playing, stopped } from '$lib/media/playback';

  let { asset, src, onopen, controls = true, ratio = '16 / 9' }:
    { asset: Asset; src?: string | null; onopen?: () => void; controls?: boolean; ratio?: string } = $props();
  let on = $state(false), el: HTMLVideoElement | undefined = $state(), err = $state('');
  const playable = $derived(asset.preview !== 'none' && asset.preview !== 'pending');
  const note = $derived(playbackNote(asset));

  function play() {
    if (!playable) { onopen?.(); return; }
    on = true; err = '';
  }
  $effect(() => { if (on && el) { el.play().catch(() => {}); } });
  onDestroy(() => { if (el) stopped(el); });
</script>

<div class="vp">
  {#if on}
    <!-- svelte-ignore a11y_media_has_caption -->
    <video bind:this={el} src={src ?? contentUrl(asset)} {controls} playsinline preload="metadata" onplay={() => el && playing(el)} onpause={() => el && stopped(el)}
      onended={() => (on = false)} onerror={() => { err = 'This video cannot be played in the browser.'; on = false; }}></video>
    {#if onopen}<button class="exp" type="button" onclick={onopen} aria-label="Open the video larger" title="Open larger"><Icon name="expand" size={14} /></button>{/if}
  {:else}
    <button class="face" type="button" onclick={onopen ?? play} aria-label={`Open ${asset.name ?? 'video'}`}>
      <MediaThumb id={asset.id} kind="video" name={asset.name} duration={asset.duration} {ratio} />
    </button>
    <button class="play" type="button" onclick={play} aria-label={`Play ${asset.name ?? 'video'}`} title={note || 'Play'}><Icon name="play" size={18} /></button>
  {/if}
  {#if err}<span class="err tiny" role="status">{err}</span>{/if}
</div>

<style>
  .vp { position: relative; width: 100%; height: 100%; background: #000; border-radius: inherit; overflow: hidden; }
  video { width: 100%; height: 100%; object-fit: contain; display: block; background: #000; }
  .face { all: unset; display: block; width: 100%; height: 100%; cursor: pointer; }
  .face:focus-visible { outline: 2px solid var(--m-ring); outline-offset: -2px; }
  .play { all: unset; position: absolute; left: 50%; top: 50%; width: 38px; height: 38px; margin: -19px 0 0 -19px; border-radius: 50%; display: grid; place-items: center; cursor: pointer;
    background: rgba(14, 15, 16, 0.62); color: #fff; backdrop-filter: blur(6px); box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.18) inset;
    transition: transform var(--fast) var(--ease), background var(--fast) var(--ease); }
  .play :global(svg) { margin-left: 2px; fill: currentColor; }
  .vp:hover .play, .play:focus-visible { transform: scale(1.08); background: rgba(14, 15, 16, 0.78); }
  .play:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .exp { all: unset; position: absolute; right: 6px; top: 6px; width: 26px; height: 26px; border-radius: 7px; display: grid; place-items: center; cursor: pointer; color: #fff;
    background: rgba(14, 15, 16, 0.66); opacity: 0; transition: opacity var(--fast) var(--ease); }
  .vp:hover .exp, .exp:focus-visible { opacity: 1; }
  .exp:focus-visible { outline: 2px solid var(--m-ring); }
  .err { position: absolute; left: 6px; bottom: 6px; right: 6px; color: #fff; background: rgba(120, 30, 30, 0.8); padding: 3px 6px; border-radius: 5px; }
  @media (pointer: coarse) { .exp { opacity: 1; } .play { width: 44px; height: 44px; margin: -22px 0 0 -22px; } }
</style>
