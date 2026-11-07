<script lang="ts">
  // The stage while adjusting: the preview the SERVER renders from the recipe (the same code path as the final file). It is a little behind the
  // sliders, and a spinner says so; nothing here pretends to be the result. While the crop tool is open the untouched picture is shown with the frame.
  import MediaCropEditor, { type Rect } from '$lib/components/media/MediaCropEditor.svelte';
  import { cleanRecipe, previewUrl, turned } from '$lib/imajutsu/adjust';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s }: { s: Imajutsu } = $props();
  const t = $derived(s.adjustTarget);
  // the crop is measured on the picture AFTER it is turned and mirrored, so that is the picture the frame sits on
  const tn = $derived(t?.width && t?.height ? turned(t.width, t.height, s.recipe) : { width: 0, height: 0 });
  const W = $derived(tn.width), H = $derived(tn.height);
  let shown = $state(''), want = $state(''), crop = $state<Rect | null>(null), aspect = $derived(s.cropAspect);

  $effect(() => {
    const id = t?.id, rec = cleanRecipe(s.recipe);
    if (!id) { want = ''; shown = ''; return; }
    const r = s.cropOn ? { rotate: rec.rotate, flip: rec.flip } : rec;
    const url = previewUrl(id, r as any);
    if (url === want) return;
    const timer = setTimeout(() => {
      want = url; s.previewBusy = true; s.previewError = '';
      const im = new Image();
      im.onload = () => { if (want === url) { shown = url; s.previewBusy = false; } };
      im.onerror = () => { if (want === url) { s.previewBusy = false; s.previewError = 'The server could not render the preview.'; } };
      im.src = url;
    }, 220);
    return () => clearTimeout(timer);
  });
  $effect(() => { if (s.cropOn) crop = s.recipe.crop ? { ...s.recipe.crop } : { x: 0, y: 0, w: W, h: H }; });
  function cropped(r: Rect) { s.setRecipe((x) => { x.crop = r.w >= W && r.h >= H ? undefined : { x: r.x, y: r.y, w: r.w, h: r.h }; }); }
</script>

<div class="as" data-testid="adjust-stage">
  {#if !t}
    <p class="st-note">Choose a picture to adjust.</p>
  {:else if s.cropOn && W && H}
    <div class="crop" style:aspect-ratio={`${W} / ${H}`} style:--r={W / H}>{#if shown}<img src={shown} alt="Your original with the crop frame on it" draggable="false" />{/if}
      <MediaCropEditor w={W} h={H} bind:rect={crop} aspect={aspect} onchange={cropped} /></div>
  {:else}
    <div class="pv" aria-busy={s.previewBusy}>
      {#if shown}<img src={shown} alt="Adjusted version as rendered by the server" data-testid="adjust-preview" data-src={shown} />{/if}
      {#if s.previewBusy || !shown}<span class="spin" role="status" aria-label="Rendering the preview" data-testid="adjust-spinner"><i></i></span>{/if}
    </div>
    {#if s.previewError}<p class="st-note bad" role="alert">{s.previewError}</p>{/if}
    <p class="st-note">This preview is rendered by the server with the same code as the file you get when you press Apply. Your original is not changed.</p>
  {/if}
</div>

<style>
  .as { display: grid; gap: 8px; justify-items: center; min-width: 0; }
  .pv, .crop { position: relative; max-width: 100%; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); background: var(--m-card); }
  .pv img { display: block; max-width: 100%; max-height: min(70vh, 760px); width: auto; height: auto; }
  .crop { width: min(100%, calc(70vh * var(--r))); } .crop img { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
  .spin { position: absolute; right: 10px; top: 10px; width: 30px; height: 30px; border-radius: 50%; background: rgba(14, 15, 16, .75); display: grid; place-items: center; }
  .spin i { width: 16px; height: 16px; border-radius: 50%; border: 2px solid rgba(255, 255, 255, .3); border-top-color: #fff; animation: sp .8s linear infinite; }
  @keyframes sp { to { transform: rotate(360deg); } } @media (prefers-reduced-motion: reduce) { .spin i { animation: none; border-top-color: #fff; border-right-color: #fff; } }
  .pv:not(:has(img)) { min-height: 200px; min-width: 200px; }
</style>
