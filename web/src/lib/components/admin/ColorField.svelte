<script lang="ts">
  // One colour: a picker, a text field that accepts HEX, RGB or HSL, opacity where the token allows it, and swatches.
  // The value is always stored as HEX (#RRGGBB or #RRGGBBAA); the text field can show it in any of the three forms.
  import { parseColor, toHex, rgbToHsl } from '$lib/theme/theme';

  let { id, label, hint = '', value, alpha = false, swatches = [], overridden = false, onchange, onreset }:
    { id: string; label: string; hint?: string; value: string; alpha?: boolean; swatches?: string[]; overridden?: boolean;
      onchange: (hex: string) => void; onreset?: () => void } = $props();

  let fmt = $state<'hex' | 'rgb' | 'hsl'>('hex');
  let text = $state(''), bad = $state(false), editing = false;
  const rgba = $derived(parseColor(value) ?? [0, 0, 0, 1]);
  const show = (c: [number, number, number, number], f: string) => {
    const a = Math.round(c[3] * 100) / 100;
    if (f === 'rgb') return a < 1 ? `rgba(${c[0]}, ${c[1]}, ${c[2]}, ${a})` : `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
    if (f === 'hsl') { const [h, s, l] = rgbToHsl(c[0], c[1], c[2]); return a < 1 ? `hsla(${h}, ${s}%, ${l}%, ${a})` : `hsl(${h}, ${s}%, ${l}%)`; }
    return toHex(c);
  };
  // read the value before checking `editing`: an effect that reads nothing reactive is never re-run
  $effect(() => { const t = show(rgba as any, fmt); if (!editing) { text = t; bad = false; } });
  function typed() {
    const c = parseColor(text);
    bad = !c || (!alpha && c[3] < 0.999);
    if (!bad && c) onchange(toHex(c));
  }
  const opaqueHex = $derived(toHex([rgba[0], rgba[1], rgba[2], 1]).slice(0, 7));
  function picked(e: Event) {
    const c = parseColor((e.currentTarget as HTMLInputElement).value);
    if (c) onchange(toHex([c[0], c[1], c[2], rgba[3]]));
  }
  function opacity(e: Event) {
    const a = Number((e.currentTarget as HTMLInputElement).value) / 100;
    onchange(toHex([rgba[0], rgba[1], rgba[2], a]));
  }
</script>

<div class="cf" class:bad>
  <div class="top">
    <label class="lab" for={id}>{label}</label>
    {#if overridden && onreset}<button type="button" class="linkish tiny" onclick={onreset} title="Go back to the derived value">Reset</button>{/if}
  </div>
  <div class="ctl">
    <span class="sw" style:background={value} aria-hidden="true"><input type="color" value={opaqueHex} oninput={picked} aria-label={`${label}: picker`} /></span>
    <input {id} class="input mono" bind:value={text} onfocus={() => (editing = true)} onblur={() => { editing = false; text = show(rgba as any, fmt); bad = false; }}
      oninput={typed} aria-invalid={bad} aria-describedby={hint ? `${id}-h` : undefined} spellcheck="false" autocomplete="off" />
    <select class="input fmt" bind:value={fmt} aria-label={`${label}: format`}><option value="hex">HEX</option><option value="rgb">RGB</option><option value="hsl">HSL</option></select>
  </div>
  {#if alpha}
    <label class="op tiny muted">Opacity <input type="range" min="0" max="100" value={Math.round(rgba[3] * 100)} oninput={opacity} aria-label={`${label}: opacity`} /> <span class="mono">{Math.round(rgba[3] * 100)}%</span></label>
  {/if}
  {#if swatches.length}
    <div class="sws" role="group" aria-label={`${label}: suggested colours`}>
      {#each swatches as s (s)}<button type="button" class="s" style:background={s} title={s} aria-label={`Use ${s}`} aria-pressed={s.toUpperCase() === value.toUpperCase()} onclick={() => onchange(s.toUpperCase())}></button>{/each}
    </div>
  {/if}
  {#if bad}<p class="err tiny">Use #RRGGBB, rgb(…) or hsl(…){alpha ? '' : ' without transparency'}.</p>{:else if hint}<p class="hint tiny" id={`${id}-h`}>{hint}</p>{/if}
</div>

<style>
  .cf { display: grid; gap: 4px; }
  .top { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; }
  .lab { font-size: 13px; font-weight: 500; }
  .ctl { display: flex; gap: 6px; align-items: center; }
  .sw { position: relative; width: 34px; height: 34px; flex: none; border-radius: var(--radius-sm); border: 1px solid var(--border-strong); overflow: hidden; }
  .sw input { position: absolute; inset: 0; opacity: 0; width: 100%; height: 100%; cursor: pointer; border: 0; padding: 0; }
  .sw:focus-within { outline: 2px solid var(--accent); outline-offset: 2px; }
  .ctl .input { min-height: 34px; font-size: 13px; }
  .fmt { width: 76px; flex: none; }
  .op { display: flex; align-items: center; gap: 8px; } .op input { flex: 1; accent-color: var(--accent); }
  .sws { display: flex; flex-wrap: wrap; gap: 4px; }
  .s { all: unset; width: 18px; height: 18px; border-radius: 4px; border: 1px solid var(--border-strong); cursor: pointer; }
  .s[aria-pressed='true'] { outline: 2px solid var(--fg); outline-offset: 1px; }
  .s:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .hint, .err { margin: 0; }
  .linkish { all: unset; cursor: pointer; color: var(--link); text-decoration: underline; }
</style>
