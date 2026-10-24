<script lang="ts">
  // The persistent header: the organisation's identity where the administrator placed it, the sidebar switch, and a
  // small account menu (theme, density, account, administration, sign out).
  import { tick } from 'svelte';
  import { goto } from '$app/navigation';
  import { app, loadMe, logout } from '$lib/session.svelte';
  import { patch } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { THEME_LABELS, PRESETS } from '$lib/theme/theme';
  import { openPalette } from '$lib/palette.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { errText } from '$lib/chat/thread.svelte';

  let { sidebar = true, collapsed = false, ontoggle, onnew, title = '' }:
    { sidebar?: boolean; collapsed?: boolean; ontoggle: () => void; onnew?: () => void; title?: string } = $props();

  const b = $derived<any>({ ...app.brand, ...(app.me?.ui.brand ?? {}) });
  const show = $derived<string>(b.header_content ?? 'both');
  const pos = $derived<string>(b.header_position ?? 'left');
  const logo = $derived(app.themeVariant === 'dark' && b.logo_dark_asset ? b.logo_dark_asset : b.logo_asset);
  const name = $derived<string>(b.header_text || b.app_name || 'Semechka');
  const theme = $derived(app.me?.ui.theme ?? {});
  const retention = $derived(app.me?.ui.retention?.show && app.me.ui.retention.place === 'header' ? app.me.ui.retention.text : '');
  const helpUrl = $derived(app.me?.ui.ui?.help_links !== false ? b.support_url : '');

  let open = $state(false), btn: HTMLButtonElement | undefined = $state();
  const initials = $derived((app.me?.user.display_name ?? '?').split(/\s+/).filter(Boolean).slice(0, 2).map((w: string) => w[0]).join('').toUpperCase());
  async function toggle() {
    open = !open;
    if (open) { await tick(); document.querySelector<HTMLElement>('.amenu [role^="menuitem"]')?.focus(); }
  }
  function close(focus = true) { open = false; if (focus) btn?.focus(); }
  function key(e: KeyboardEvent) {
    const items = [...document.querySelectorAll<HTMLElement>('.amenu [role^="menuitem"]')];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    else if (e.key === 'Tab') close(false);
  }
  async function pref(body: Record<string, unknown>) {
    try { await patch('/api/me', body); await loadMe(); } catch (e) { toast(errText(e), 'bad'); }
  }
  async function signOut() { close(false); await logout(); goto('/login'); }
  const themes = $derived<string[]>(theme.user_choice ? [...(theme.choices ?? []), 'system'] : []);
  const activeTheme = $derived(app.me?.user.prefs?.theme && themes.includes(app.me.user.prefs.theme) ? app.me.user.prefs.theme : theme.default);
  /** The swatch shows the theme's own page colour (Custom: its main colour; Match device: half light, half dark). */
  function swatch(t: string) {
    if (t === 'custom') return theme.custom?.light?.brand ?? theme.custom?.light?.bg;
    if (t === 'system') return PRESETS.light.values.bg;
    return (PRESETS as any)[t]?.values.bg;
  }
  const density = $derived(app.me?.user.prefs?.density ?? app.me?.ui.density ?? 'comfortable');
</script>

{#snippet brand()}
  {#if show !== 'none'}
    <a href="/" class="brand" aria-label={`${b.app_name || name}: home`} style:gap="{b.logo_gap ?? 8}px">
      {#if logo && show !== 'text'}<img src={`/api/assets/${logo}`} alt="" style:height="{b.logo_height ?? 24}px" />{/if}
      {#if show !== 'logo' || !logo}<span class="bn" style:font-size="{b.header_text_size ?? 15}px" style:font-weight={b.header_text_weight ?? '600'}>{name}</span>{/if}
    </a>
  {/if}
{/snippet}

<header class="hdr" class:center={pos === 'center'} style:height="{b.header_height ?? 48}px" style:padding-inline="{b.header_padding ?? 12}px">
  <div class="l">
    {#if sidebar}
      <button class="btn ghost icon sm sbt" class:desk={collapsed} onclick={ontoggle} aria-label={collapsed ? 'Show the sidebar' : 'Show conversations'} title="Conversations (Ctrl+Shift+S)"><Icon name="sidebar" size={18} /></button>
      {#if collapsed && onnew}<button class="btn ghost icon sm desk-only" onclick={onnew} aria-label="New conversation" title="New conversation (Ctrl+Shift+O)"><Icon name="edit" size={17} /></button>{/if}
    {/if}
    {#if pos === 'left'}{@render brand()}{/if}
    {#if title}<span class="ttl" title={title}>{title}</span>{/if}
  </div>
  {#if pos === 'center'}<div class="c">{@render brand()}</div>{/if}
  <div class="r">
    {#if retention}<span class="tiny muted ret">{retention}</span>{/if}
    {#if pos === 'right'}{@render brand()}{/if}
    <button class="btn ghost icon sm" onclick={openPalette} aria-label="Search and commands" title="Search and commands (Ctrl+K)"><Icon name="search" size={17} /></button>
    <div class="acct">
      <button bind:this={btn} class="av" onclick={toggle} aria-haspopup="menu" aria-expanded={open} ><span aria-hidden="true">{initials}</span><span class="sr-only">Account: {app.me?.user.display_name}</span></button>
      {#if open}
        <div class="mscrim" role="presentation" onclick={() => close(false)}></div>
        <div class="amenu fade" role="menu" tabindex="-1" aria-label="Account" onkeydown={key}>
          <div class="who"><strong>{app.me?.user.display_name}</strong><span class="tiny muted">{app.me?.user.email}</span></div>
          {#if themes.length}
            <div class="sec" role="group" aria-label="Theme">
              <span class="lab tiny muted">Theme</span>
              {#each themes as t (t)}
                <button role="menuitemradio" aria-checked={activeTheme === t} onclick={() => pref({ theme: t })}>
                  <span class="sw" data-sw={t} aria-hidden="true" style:background={swatch(t)}></span>{THEME_LABELS[t] ?? t}{#if activeTheme === t}<span class="ck"><Icon name="check" size={14} /></span>{/if}
                </button>
              {/each}
            </div>
          {/if}
          <div class="sec" role="group" aria-label="Density">
            <span class="lab tiny muted">Density</span>
            {#each [['comfortable', 'Comfortable'], ['compact', 'Compact']] as [v, l] (v)}
              <button role="menuitemradio" aria-checked={density === v} onclick={() => pref({ density: v })}>{l}{#if density === v}<span class="ck"><Icon name="check" size={14} /></span>{/if}</button>
            {/each}
          </div>
          <div class="sec">
            <a role="menuitem" href="/account" onclick={() => close(false)}><Icon name="user" size={15} /> Account and security</a>
            {#if app.me?.admin}<a role="menuitem" href="/admin" onclick={() => close(false)}><Icon name="settings" size={15} /> Administration</a>{/if}
            {#if helpUrl}<a role="menuitem" href={helpUrl} target="_blank" rel="noopener noreferrer"><Icon name="help" size={15} /> Help and support</a>{/if}
            <button role="menuitem" onclick={signOut}><Icon name="external" size={15} /> Sign out</button>
          </div>
        </div>
      {/if}
    </div>
  </div>
</header>

<style>
  .hdr { flex: none; display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 8px; background: var(--header-bg); min-height: 40px; }
  .hdr.center { grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr); }
  .l, .r { display: flex; align-items: center; gap: 6px; min-width: 0; }
  .r { justify-content: flex-end; }
  .c { display: flex; justify-content: center; min-width: 0; }
  .brand { display: inline-flex; align-items: center; color: var(--fg); text-decoration: none; min-width: 0; border-radius: var(--radius-sm); }
  .brand img { display: block; max-width: 200px; object-fit: contain; }
  .bn { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; letter-spacing: -0.01em; }
  .ttl { color: var(--fg); font-size: 14px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-left: 4px; }
  .brand + .ttl { color: var(--fg-muted); font-weight: 400; padding-left: 10px; margin-left: 2px; border-left: 1px solid var(--border); }
  .sbt:not(.desk) { display: none; }
  .ret { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 40ch; }
  .acct { position: relative; }
  .av { all: unset; box-sizing: border-box; width: 30px; height: 30px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 600;
    background: var(--selected); color: var(--fg); cursor: pointer; }
  .av:hover { background: var(--hover); box-shadow: inset 0 0 0 1px var(--border); }
  .av:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .mscrim { position: fixed; inset: 0; z-index: 80; }
  .amenu { position: absolute; right: 0; top: calc(100% + 6px); z-index: 81; width: 260px; max-height: calc(100dvh - 72px); overflow: auto; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-menu); box-shadow: var(--shadow); padding: 4px; }
  .who { display: grid; padding: 8px 10px; gap: 1px; overflow-wrap: anywhere; }
  .sec { border-top: 1px solid var(--border); padding: 4px 0; display: grid; }
  .lab { padding: 4px 10px 2px; }
  .amenu button, .amenu a { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 8px; padding: 0 10px; min-height: 32px; border-radius: var(--radius-sm); cursor: pointer; font-size: 14px; color: var(--fg); }
  .amenu button:hover, .amenu a:hover, .amenu button:focus-visible, .amenu a:focus-visible { background: var(--hover); outline: none; }
  .ck { margin-left: auto; display: grid; }
  .sw { width: 14px; height: 14px; border-radius: 50%; border: 1px solid var(--border-strong); flex: none; }
  .sw[data-sw='system'] { box-shadow: inset -7px 0 0 #141617; }
  @media (max-width: 899px) {
    .sbt { display: inline-flex !important; } .desk-only { display: none; }
    .ttl, .ret { display: none; }
  }
  @media (pointer: coarse) { .amenu button, .amenu a { min-height: 44px; } .av { width: 40px; height: 40px; } }
</style>
