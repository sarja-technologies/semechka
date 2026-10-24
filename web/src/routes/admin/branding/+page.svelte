<script lang="ts">
  import { onMount } from 'svelte';
  import { get, put, upload } from '$lib/api';
  import { app, loadPublic } from '$lib/session.svelte';
  import { toast } from '$lib/toast.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
  import { errMsg } from '$lib/components/admin/util';
  let b = $state<any>({}); let theme = $state<'paper' | 'carbon' | 'graphite'>('paper'); let err = $state('');
  async function refresh() { await loadPublic(); b = { ...app.brand }; }
  onMount(() => { refresh(); window.addEventListener('semechka:settings', refresh); return () => window.removeEventListener('semechka:settings', refresh); });
  async function up(e: Event, key: 'brand.logo_asset' | 'brand.favicon_asset') {
    const f = (e.target as HTMLInputElement).files?.[0]; if (!f) return; err = '';
    try { const r = await upload('/api/admin/assets', f); await put(`/api/admin/settings/${key}`, { value: r.id }); toast('Image uploaded.'); await refresh(); } catch (x) { err = errMsg(x); }
    (e.target as HTMLInputElement).value = '';
  }
  async function clear(key: string) { try { await put(`/api/admin/settings/${key}`, { value: '' }); await refresh(); } catch (x) { err = errMsg(x); } }
  const accent = $derived(theme === 'paper' ? b.accent_light : b.accent_dark);
</script>
<Page title="Identity & Branding" lead="Names, logo, colours and legal text. Saved values appear everywhere: sign-in, header, browser tab, error pages and emails.">
  <div class="top">
    <section class="panel"><div class="panel-h"><h3>Images</h3></div><div class="panel-b stack">
      {#each [['brand.logo_asset', 'Logo', 'logo_asset'], ['brand.favicon_asset', 'Favicon', 'favicon_asset']] as [key, label, f]}
        <div class="row"><div class="grow"><div class="label">{label}</div><span class="hint">PNG, WebP, JPEG or SVG up to 512 KB. Scripted SVGs are rejected.</span></div>
          {#if b[f]}<img src={`/api/assets/${b[f]}`} alt="Current {label.toLowerCase()}" style="height:36px;max-width:120px;object-fit:contain;border:1px solid var(--border);padding:2px;background:#fff" />{/if}
          <label class="btn sm">Upload<input type="file" accept="image/png,image/webp,image/jpeg,image/svg+xml" class="sr-only" onchange={(e) => up(e, key as any)} /></label>
          {#if b[f]}<button class="btn sm" onclick={() => clear(key)}>Remove</button>{/if}</div>
      {/each}
      {#if err}<p class="err" role="alert">{err}</p>{/if}
      <p class="hint">The favicon is stored and served; the browser tab title below applies immediately.</p>
    </div></section>
    <section class="panel" aria-label="Live preview"><div class="panel-h"><h3>Preview</h3>
      <div role="group" aria-label="Preview theme" class="row" style="gap:4px">{#each ['paper', 'carbon', 'graphite'] as t}<button class="btn sm" class:primary={theme === t} aria-pressed={theme === t} onclick={() => (theme = t as any)}>{t}</button>{/each}</div></div>
      <div class="pv" data-theme={theme} style="--accent:{accent}; --radius:{b.radius ?? 8}px">
        <div class="pvh"><strong>{b.app_name}</strong><span class="muted small">{b.short_name}</span></div>
        <div class="pvb"><div class="pvc"><div class="label">Sign in to {b.app_name}</div><div class="fake">Email</div><div class="fake">Password</div><button class="btn primary sm" tabindex="-1">Sign in</button>{#if b.signin_message}<p class="hint" style="margin:8px 0 0">{b.signin_message}</p>{/if}</div>
          <p class="tiny muted" style="margin:8px 0 0">{b.footer_text || b.org_name}{#if b.powered_by} · Powered by Semechka{/if}</p></div>
      </div>
      <p class="hint" style="padding:0 16px 12px;margin:0">Browser tab: <strong>{b.tab_title}</strong></p>
    </section>
  </div>
  <SettingsPanel categories={['Identity & Branding']} searchable={false} skip={['brand.logo_asset', 'brand.favicon_asset']} />
</Page>
<style>
  .top { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); margin-bottom: 8px; }
  .pv { background: var(--bg); color: var(--fg); border-top: 1px solid var(--border); }
  .pv[data-theme='paper'] { --bg: #f7f7f5; --surface: #fff; --fg: #202322; --border: #d9dbd7; --accent-fg: #fff; --fg-muted: #5b615f; --surface-2: #f0f1ee; --border-strong: #b9bdb8; }
  .pv[data-theme='carbon'] { --bg: #111415; --surface: #1a1e1f; --fg: #f0f2ef; --border: #343838; --accent-fg: #0b1a19; --fg-muted: #a3aaa7; --surface-2: #222728; --border-strong: #4a5050; }
  .pv[data-theme='graphite'] { --bg: #252a2a; --surface: #303637; --fg: #e9ece8; --border: #475150; --accent-fg: #0b1a19; --fg-muted: #aab1ae; --surface-2: #394041; --border-strong: #5d6967; }
  .pvh { display: flex; gap: 8px; align-items: baseline; padding: 10px 16px; border-bottom: 1px solid var(--border); background: var(--surface); }
  .pvb { padding: 16px; } .pvc { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; display: grid; gap: 8px; max-width: 280px; }
  .fake { border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 6px 10px; color: var(--fg-muted); font-size: 13px; }
</style>
