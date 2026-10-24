<script lang="ts">
  // Identity: images (uploaded through the validating asset endpoint) and a preview of the header, sign-in card and
  // welcome screen painted with the real theme tokens. Every other identity setting is edited in the panel below.
  import { onMount } from 'svelte';
  import { put, upload } from '$lib/api';
  import { app, loadPublic, loadMe } from '$lib/session.svelte';
  import { toast } from '$lib/toast.svelte';
  import { PRESETS, THEME_LABELS, resolve, writeTokens, type ThemeBundle } from '$lib/theme/theme';
  import Page from '$lib/components/admin/Page.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { errMsg } from '$lib/components/admin/util';

  type Key = 'brand.logo_asset' | 'brand.logo_dark_asset' | 'brand.favicon_asset';
  const IMAGES: { key: Key; f: string; label: string; hint: string }[] = [
    { key: 'brand.logo_asset', f: 'logo_asset', label: 'Logo', hint: 'Header and sign-in page. A wide logo with a transparent background works best.' },
    { key: 'brand.logo_dark_asset', f: 'logo_dark_asset', label: 'Logo for dark themes', hint: 'Optional. Used on Dark, Graphite and the dark Custom version; without it the logo above is used everywhere.' },
    { key: 'brand.favicon_asset', f: 'favicon_asset', label: 'Favicon', hint: 'The browser tab icon. Square, at least 32 × 32.' }
  ];
  let b = $state<any>({}), err = $state(''), busy = $state<string>(''), dims = $state<Record<string, string>>({});
  let view = $state<string>('light'), box: HTMLDivElement | undefined = $state();

  async function refresh() { await loadPublic(); b = { ...app.brand }; }
  onMount(() => { refresh(); window.addEventListener('semechka:settings', refresh); return () => window.removeEventListener('semechka:settings', refresh); });

  async function up(e: Event, key: Key) {
    const inp = e.currentTarget as HTMLInputElement; const f = inp.files?.[0]; inp.value = ''; if (!f) return;
    err = ''; busy = key;
    try {
      if (f.size > 512 * 1024) throw new Error('Images are limited to 512 KB.');
      const r = await upload('/api/admin/assets', f);
      await put(`/api/admin/settings/${key}`, { value: r.id });
      toast('Image uploaded.'); await Promise.all([refresh(), loadMe()]);
    } catch (x) { err = errMsg(x); } finally { busy = ''; }
  }
  async function clear(key: Key) {
    err = ''; busy = key;
    try { await put(`/api/admin/settings/${key}`, { value: '' }); toast('Image removed.'); await Promise.all([refresh(), loadMe()]); }
    catch (x) { err = errMsg(x); } finally { busy = ''; }
  }
  function measured(f: string, e: Event) { const i = e.currentTarget as HTMLImageElement; dims[f] = i.naturalWidth ? `${i.naturalWidth} × ${i.naturalHeight}` : 'vector'; }

  // preview painted with the published theme's tokens, never the admin page's own
  const bundle = $derived<ThemeBundle>(app.publicTheme ?? app.me?.ui.theme ?? {});
  const views = $derived(['light', 'dark', 'graphite', ...(bundle.custom ? ['custom-light', 'custom-dark'] : [])]);
  const painted = $derived.by(() => {
    if (view.startsWith('custom-')) { const v = view.slice(7) as 'light' | 'dark'; return { values: { ...PRESETS[v].values, ...(bundle.custom?.[v] ?? {}) }, variant: v }; }
    const r = resolve(view, { ...bundle, follow_system: false }); return { values: r.values, variant: r.variant };
  });
  $effect(() => { if (box) writeTokens(box, painted.values, painted.variant, bundle); });
  const logo = $derived(painted.variant === 'dark' && b.logo_dark_asset ? b.logo_dark_asset : b.logo_asset);
  const show = $derived<string>(b.header_content ?? 'both');
  const name = $derived<string>(b.header_text || b.app_name || '');
  const label = (v: string) => (v.startsWith('custom-') ? `Custom (${v.slice(7)})` : THEME_LABELS[v]);
</script>

{#snippet brandmark()}
  <span class="brand" style:gap="{b.logo_gap ?? 8}px">
    {#if logo && show !== 'text'}<img src={`/api/assets/${logo}`} alt="" style:height="{b.logo_height ?? 24}px" />{/if}
    {#if (show !== 'logo' || !logo) && name}<span class="bn" style:font-size="{b.header_text_size ?? 15}px" style:font-weight={b.header_text_weight ?? '600'}>{name}</span>{/if}
  </span>
{/snippet}

<Page title="Identity & Branding" lead="Names, logos, the header and the sign-in page. Colours, fonts and corners are on the Theme page. Nothing here requires the Semechka name to be shown.">
  <div class="top">
    <section class="panel"><div class="panel-h"><h3>Images</h3></div><div class="panel-b stack">
      {#each IMAGES as im (im.key)}
        <div class="img">
          <div class="grow"><div class="label">{im.label}</div><span class="hint">{im.hint}</span>
            {#if b[im.f] && dims[im.f]}<span class="hint mono dims">Current file: {dims[im.f]}</span>{/if}</div>
          {#if b[im.f]}
            <span class="thumbs" role="img" aria-label={`Current ${im.label.toLowerCase()}`}>
              <span class="th lt"><img src={`/api/assets/${b[im.f]}`} alt="" onload={(e) => measured(im.f, e)} /></span>
              <span class="th dk"><img src={`/api/assets/${b[im.f]}`} alt="" /></span>
            </span>
          {/if}
          <div class="row">
            <label class="btn sm" class:disabled={!!busy}><Icon name="upload" size={14} /> {b[im.f] ? 'Replace' : 'Upload'}<input type="file" accept="image/png,image/webp,image/jpeg,image/svg+xml" class="sr-only" disabled={!!busy} onchange={(e) => up(e, im.key)} /></label>
            {#if b[im.f]}<button class="btn ghost sm" onclick={() => clear(im.key)} disabled={!!busy}>Remove</button>{/if}
          </div>
        </div>
      {/each}
      {#if err}<p class="err" role="alert">{err}</p>{/if}
      <p class="hint">PNG, WebP, JPEG or SVG, up to 512 KB. The file type is checked from its contents; SVGs with scripts, event handlers, external references or embedded documents are refused, and images are served with a sandboxing policy.</p>
    </div></section>

    <section class="panel" aria-label="Preview"><div class="panel-h"><h3>Preview</h3>
      <div class="seg" role="radiogroup" aria-label="Preview theme">
        {#each views as v (v)}<button role="radio" aria-checked={view === v} class:on={view === v} onclick={() => (view = v)}>{label(v)}</button>{/each}
      </div></div>
      <div class="pv" bind:this={box} aria-label="Preview of the header, welcome screen and sign-in card with the saved settings" role="img">
        <div class="hdr" class:center={b.header_position === 'center'} style:height="{b.header_height ?? 52}px" style:padding-inline="{b.header_padding ?? 16}px">
          <div class="l"><span class="ic" aria-hidden="true"><Icon name="sidebar" size={16} /></span>{#if (b.header_position ?? 'left') === 'left'}{@render brandmark()}{/if}</div>
          {#if b.header_position === 'center'}<div class="c">{@render brandmark()}</div>{/if}
          <div class="r">{#if b.header_position === 'right'}{@render brandmark()}{/if}<span class="av">AM</span></div>
        </div>
        <div class="split">
          <div class="welcome">
            <strong class="wt">{b.welcome_title || 'How can I help?'}</strong>
            {#if b.welcome_text}<p class="small">{b.welcome_text}</p>{/if}
            <div class="comp">Message</div>
          </div>
          <div class="signin">
            {#if b.login_logo !== false && logo}<img src={`/api/assets/${logo}`} alt="" style:height="{Math.max(24, b.logo_height ?? 24)}px" />{/if}
            <strong>{b.login_title || (b.app_name ? `Sign in to ${b.app_name}` : 'Sign in')}</strong>
            <span class="fake">Email</span><span class="fake">Password</span>
            <span class="pbtn">Sign in</span>
            {#if b.signin_message}<p class="tiny">{b.signin_message}</p>{/if}
          </div>
        </div>
        {#if b.footer_text || b.powered_by}<div class="foot tiny">{#if b.footer_text}<span>{b.footer_text}</span>{/if}{#if b.powered_by}<span>Powered by Semechka</span>{/if}</div>{/if}
      </div>
      <p class="hint pad">Shows saved values: change a setting below and the preview follows. Browser tab: <strong>{b.tab_title || '(empty)'}</strong></p>
    </section>
  </div>
  <SettingsPanel categories={['Identity & Branding']} searchable={false} skip={['brand.logo_asset', 'brand.logo_dark_asset', 'brand.favicon_asset', 'brand.radius', 'brand.density', 'brand.theme_default']} />
</Page>

<style>
  .top { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); margin-bottom: 16px; align-items: start; }
  .img { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; padding-bottom: 12px; border-bottom: 1px solid var(--border); }
  .img:last-of-type { border-bottom: 0; }
  .dims { display: block; }
  .thumbs { display: inline-flex; gap: 4px; }
  .th { display: grid; place-items: center; width: 72px; height: 40px; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 3px; }
  .th.lt { background: #FFFFFF; } .th.dk { background: #1D2021; }
  .th img { max-width: 100%; max-height: 100%; object-fit: contain; }
  label.disabled { opacity: 0.6; pointer-events: none; }
  .seg { display: inline-flex; flex-wrap: wrap; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 2px; gap: 2px; }
  .seg button { all: unset; padding: 0 8px; height: 24px; display: inline-flex; align-items: center; border-radius: calc(var(--radius-sm) - 2px); cursor: pointer; font-size: 12px; }
  .seg button.on { background: var(--selected); font-weight: 600; } .seg button:focus-visible { outline: 2px solid var(--accent); }
  .pv { --surface: var(--card-bg); background: var(--bg); color: var(--fg); font-family: var(--font); border-top: 1px solid var(--border); user-select: none; }
  .hdr { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; background: var(--header-bg); border-bottom: 1px solid var(--border); }
  .hdr.center { grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); }
  .l, .r { display: flex; align-items: center; gap: 8px; min-width: 0; } .r { justify-content: flex-end; } .c { display: flex; justify-content: center; }
  .ic { color: var(--fg-muted); display: grid; }
  .brand { display: inline-flex; align-items: center; min-width: 0; }
  .brand img { display: block; max-width: 180px; object-fit: contain; }
  .bn { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; letter-spacing: -0.01em; }
  .av { width: 26px; height: 26px; border-radius: 50%; display: grid; place-items: center; font-size: 10px; font-weight: 600; background: var(--btn-primary-bg); color: var(--btn-primary-fg); }
  .split { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 16px; }
  .welcome { display: grid; gap: 8px; align-content: center; justify-items: center; text-align: center; }
  .welcome p { margin: 0; color: var(--fg-2); }
  .wt { font-size: 17px; }
  .comp { justify-self: stretch; border: 1px solid var(--border-strong); background: var(--composer-bg); border-radius: calc(var(--radius) + 4px); padding: 8px 10px; color: var(--fg-muted); font-size: 12px; text-align: left; }
  .signin { background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius); padding: 14px; display: grid; gap: 6px; justify-items: start; font-size: 13px; }
  .signin img { max-width: 160px; object-fit: contain; }
  .signin p { margin: 0; color: var(--fg-2); }
  .fake { justify-self: stretch; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 4px 8px; color: var(--fg-muted); font-size: 12px; }
  .pbtn { background: var(--btn-primary-bg); color: var(--btn-primary-fg); border-radius: var(--radius-sm); padding: 3px 12px; font-size: 12px; }
  .foot { display: flex; gap: 12px; justify-content: center; padding: 4px 12px; border-top: 1px solid var(--border); background: var(--header-bg); color: var(--fg-muted); }
  .pad { padding: 8px 16px 12px; margin: 0; }
  @media (max-width: 560px) { .split { grid-template-columns: 1fr; } }
</style>
