<script lang="ts">
  // The model picker inside the composer. The server arranges the list (the administrator's names, grouping and order,
  // or this person's own where they changed them) and sends only what this person may see; the picker shows it,
  // searches it and saves personal choices. Unknown details are left out, never guessed. Models listed as not
  // available cannot be chosen, and every use is checked again by the server anyway.
  import { tick } from 'svelte';
  import { patch } from '$lib/api';
  import { refreshCatalogs, type Operation } from '$lib/chat/store.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import ModelIcon from './ModelIcon.svelte';

  let { catalog, op = 'text', value = $bindable(), disabled = false, label = 'Model', needVision = false }:
    { catalog: any; op?: Operation; value: string | undefined; disabled?: boolean; label?: string; needVision?: boolean } = $props();

  let open = $state(false), q = $state(''), sel = $state(0), arrange = $state(false), note = $state('');
  let btn: HTMLButtonElement | undefined = $state(), box: HTMLInputElement | undefined = $state();
  const uid = Math.random().toString(36).slice(2, 8);

  const byId = $derived(new Map<string, any>([...(catalog?.models ?? []), ...(catalog?.unusable ?? [])].map((m: any) => [m.id, m])));
  const current = $derived(byId.get(value ?? ''));
  const favs = $derived(new Set<string>(catalog?.favourites ?? []));
  const detailed = $derived(catalog?.display?.view === 'detailed');
  let collapsed = $state<Record<string, boolean>>({});
  $effect(() => { collapsed = Object.fromEntries((catalog?.sections ?? []).map((s: any) => [s.id, !!s.collapsed])); });

  const hay = (m: any) => [m.label, m.sub, m.alias, m.provider_label, m.based_on, m.real_id, m.engine, m.details?.parameters, m.details?.family]
    .filter(Boolean).join(' ').toLowerCase();
  /** Sections in display order; while searching every match shows and headings cannot be folded. */
  const sections = $derived.by(() => {
    if (!catalog) return [];
    const t = q.trim().toLowerCase();
    const hit = (id: string) => { const m = byId.get(id); return !!m && (!t || hay(m).includes(t)); };
    const out: { id: string; title: string; description?: string; ids: string[]; foldable: boolean; off?: boolean }[] = [];
    const secs = catalog.sections ?? [];
    for (const s of secs) {
      const ids = s.models.filter(hit);
      if (ids.length) out.push({ id: s.id, title: secs.length > 1 || s.id === 'favourites' ? s.title : '', description: s.description, ids, foldable: !t && secs.length > 1 });
    }
    const off = (catalog.unusable ?? []).map((m: any) => m.id).filter(hit);
    if (off.length) out.push({ id: '_off', title: 'Not available to you', ids: off, foldable: !t, off: true });
    return out;
  });
  $effect(() => { if (catalog?.unusable?.length && collapsed._off === undefined) collapsed._off = true; });
  const flat = $derived(sections.flatMap((s) => (s.foldable && collapsed[s.id] ? [] : s.ids.map((id) => ({ sec: s.id, id, off: !!s.off })))));
  const count = $derived((catalog?.models ?? []).length);

  async function show() {
    if (disabled) return;
    open = true; q = ''; arrange = false; note = ''; await tick();
    sel = Math.max(0, flat.findIndex((f) => f.id === value));
    box?.focus(); scrollSel();
  }
  function hide(focusBtn = true) { open = false; if (focusBtn) btn?.focus(); }
  function pick(id: string) { const m = byId.get(id); if (!m || m.usable === false) return; value = id; hide(); }
  function key(e: KeyboardEvent) {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(flat.length - 1, sel + 1); scrollSel(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); scrollSel(); }
    else if (e.key === 'Home' && !q) { e.preventDefault(); sel = 0; scrollSel(); }
    else if (e.key === 'End' && !q) { e.preventDefault(); sel = flat.length - 1; scrollSel(); }
    else if (e.key === 'Enter' && e.shiftKey && flat[sel] && !flat[sel].off) { e.preventDefault(); star(flat[sel].id); }
    else if (e.key === 'Enter' && flat[sel]) { e.preventDefault(); pick(flat[sel].id); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); hide(); }
    else if (e.key === 'Tab' && !arrange) hide(false);
  }
  function scrollSel() { tick().then(() => document.getElementById(`mc-${uid}-${sel}`)?.scrollIntoView({ block: 'nearest' })); }
  function outside(e: PointerEvent) { if (open && !(e.target as HTMLElement).closest(`[data-mc="${uid}"]`)) hide(false); }

  // ---- personal choices (saved on the account; the administrator's settings stay the default) -------------------
  async function save(body: Record<string, unknown>) {
    note = '';
    try { await patch('/api/me', body); await refreshCatalogs(op); }
    catch (e) { note = errText(e); }
  }
  function star(id: string) {
    const next = favs.has(id) ? [...favs].filter((x) => x !== id) : [...favs, id];
    save({ favourite_models: next });
  }
  const GROUPS: Record<string, string> = { groups: 'Administrator’s groups', family: 'Developer', capability: 'What it can do', engine: 'Engine', size: 'Size', none: 'No grouping' };
  const SORTS: Record<string, string> = { custom: 'Administrator’s order', name: 'Name', size_desc: 'Largest first', size_asc: 'Smallest first', context: 'Longest context' };

  // ---- what is known about a model ---------------------------------------------------------------------------
  const ctx = (n: number) => (n >= 1_000_000 ? `${Math.round(n / 100_000) / 10}M` : n >= 1024 ? `${Math.round(n / 1024)}K` : String(n));
  const gb = (mb: number) => (mb >= 1024 ? `${Math.round(mb / 102.4) / 10} GB` : `${mb} MB`);
  const MODES: Record<string, string> = { txt2img: 'Makes pictures', edit: 'Edits pictures', img2img: 'Reworks pictures', t2v: 'Text to video', i2v: 'Animates a picture', ti2v: 'Text or picture to video' };
  const who = (m: any) => (m.provider_label ? (m.based_on ? `${m.provider_label}, based on ${m.based_on}` : m.provider_label) : '');
  function facts(m: any) {
    const d = m.details ?? {};
    return [d.parameters, d.quantization, d.context_length ? `${ctx(d.context_length)} context` : null, d.memory_mb ? `about ${gb(d.memory_mb)} memory` : null,
      d.reasoning ? 'Reasons step by step' : null, ...(m.media_modes ?? []).map((x: string) => MODES[x]).filter(Boolean)].filter(Boolean).join(' · ');
  }
  const sees = (m: any) => (m.capabilities ?? []).includes('vision');
  const STATE: Record<string, string> = { loaded: 'Loaded', loading: 'Loading', error: 'Failed to load' };
  const idxOf = (sec: string, id: string) => flat.findIndex((f) => f.sec === sec && f.id === id);
</script>

<svelte:window onpointerdown={outside} />
<div class="mc" data-mc={uid}>
  <button bind:this={btn} type="button" class="trigger" {disabled} onclick={() => (open ? hide() : show())} aria-haspopup="dialog" aria-expanded={open}
    title={current ? [current.label, who(current)].filter(Boolean).join(' · ') : 'Choose a model'}>
    {#if current}<ModelIcon icon={current.icon} label={current.label} size={16} />{/if}
    <span class="sr-only">{label}: </span><span class="name">{current?.label ?? (count ? 'Choose a model' : 'No model available')}</span>
    <Icon name="chevdown" size={14} />
  </button>
  {#if open}
    <div class="pop pop-in" role="dialog" aria-label={`Choose ${label.toLowerCase()}`}>
      <div class="search"><Icon name="search" size={15} />
        <input bind:this={box} bind:value={q} oninput={() => (sel = 0)} onkeydown={key} placeholder="Search models" aria-label="Search models"
          role="combobox" aria-expanded="true" aria-controls={`mc-${uid}-list`} aria-activedescendant={flat[sel] ? `mc-${uid}-${sel}` : undefined} />
        <button type="button" class="tool" class:on={arrange} aria-expanded={arrange} aria-controls={`mc-${uid}-arr`} title="Arrange this list" aria-label="Arrange this list"
          onclick={() => (arrange = !arrange)}><Icon name="sliders" size={15} /></button>
      </div>
      {#if arrange && catalog?.display}
        <div class="arr" id={`mc-${uid}-arr`}>
          <div class="seg" role="radiogroup" aria-label="Detail">
            {#each [['compact', 'Compact'], ['detailed', 'Detailed']] as [v, t] (v)}
              <button type="button" role="radio" aria-checked={catalog.display.view === v} class:on={catalog.display.view === v} onclick={() => save({ picker: { view: v } })}>{t}</button>
            {/each}
          </div>
          <label>Group by <select class="input" value={catalog.display.group_by} onchange={(e) => save({ picker: { group_by: e.currentTarget.value } })}>
            {#each catalog.choices?.group_by ?? [] as g (g)}<option value={g}>{GROUPS[g] ?? g}</option>{/each}</select></label>
          <label>Sort <select class="input" value={catalog.display.sort} onchange={(e) => save({ picker: { sort: e.currentTarget.value } })}>
            {#each catalog.choices?.sort ?? [] as s (s)}<option value={s}>{SORTS[s] ?? s}</option>{/each}</select></label>
          <button type="button" class="linkish" onclick={() => save({ picker: { view: null, group_by: null, sort: null } })}>Use the defaults</button>
        </div>
      {/if}
      {#if note}<p class="note err" role="alert">{note}</p>{/if}
      <div class="list" id={`mc-${uid}-list`} role="listbox" aria-label="Models">
        {#each sections as s (s.id)}
          {#if s.title}
            {#if s.foldable}
              <button type="button" class="gh" aria-expanded={!collapsed[s.id]} onclick={() => (collapsed[s.id] = !collapsed[s.id])} tabindex="-1">
                <span class="chev" class:shut={collapsed[s.id]}><Icon name="chevdown" size={12} /></span>{s.title}<span class="n">{s.ids.length}</span>
              </button>
            {:else}<div class="gh static" role="presentation">{s.title}</div>{/if}
            {#if s.description && !collapsed[s.id]}<p class="gd">{s.description}</p>{/if}
          {/if}
          {#if !(s.foldable && collapsed[s.id])}
            {#each s.ids as id (s.id + id)}
              {@const m = byId.get(id)}
              {@const i = idxOf(s.id, id)}
              {#if m}
                <!-- svelte-ignore a11y_click_events_have_key_events -->
                <div id={`mc-${uid}-${i}`} role="option" tabindex="-1" aria-selected={id === value} aria-disabled={s.off || undefined}
                  class="opt" class:on={i === sel} class:cur={id === value} class:off={s.off} class:det={detailed}
                  onclick={() => pick(id)} onpointermove={() => (sel = i)}>
                  <ModelIcon icon={m.icon} label={m.label} size={detailed ? 24 : 18} />
                  <span class="txt">
                    <span class="al"><span class="lb">{m.label}</span>
                      {#if m.state && STATE[m.state]}<span class="st" data-state={m.state} title={STATE[m.state]}><span class="sr-only">{STATE[m.state]}</span></span>{/if}
                      {#if m.restricted && !s.off}<span class="tag" title="Not offered to everyone; you can use it because of your role">Restricted</span>{/if}
                      {#if m.external}<span class="tag warn">External service</span>{/if}
                    </span>
                    {#if m.sub}<span class="ln">{m.sub}</span>{/if}
                    {#if detailed}
                      {#if who(m) || facts(m)}<span class="ln">{[who(m), facts(m)].filter(Boolean).join(' · ')}</span>{/if}
                      {#if m.description}<span class="ds">{m.description}</span>{/if}
                      {#if m.real_id && m.real_id !== m.label}<span class="ln mono">{m.real_id}</span>{/if}
                    {:else if who(m) && !m.sub}<span class="ln">{who(m)}</span>{/if}
                    {#if s.off && m.reason}<span class="ln">{m.reason}</span>{/if}
                    {#if needVision && !s.off && !sees(m)}<span class="ln warn">Cannot read images: it receives only their file names</span>{/if}
                  </span>
                  {#if !s.off}
                    {@const fl = favs.has(id) ? `Remove ${m.label} from favourites` : `Add ${m.label} to favourites`}
                    <button type="button" class="fav" class:starred={favs.has(id)} tabindex="-1" aria-label={fl} title={`${fl} (Shift+Enter)`}
                      onclick={(e) => { e.stopPropagation(); star(id); }}><Icon name="star" size={14} /></button>
                  {/if}
                  {#if id === value}<span class="ck"><Icon name="check" size={15} /></span>{/if}
                </div>
              {/if}
            {/each}
          {/if}
        {:else}<p class="empty">{q ? 'No model matches.' : 'No model is available to you for this.'}</p>{/each}
      </div>
      <p class="foot">{count} {count === 1 ? 'model' : 'models'} · Enter to choose · Shift+Enter to favourite</p>
    </div>
  {/if}
</div>

<style>
  .mc { position: relative; min-width: 0; }
  .trigger { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 8px 0 10px; border-radius: 999px;
    color: var(--fg-2); cursor: pointer; max-width: 260px; font-size: 13px; font-weight: 500; transition: background var(--fast) var(--ease); }
  .trigger:hover:not(:disabled) { background: var(--hover); color: var(--fg); }
  .trigger:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .trigger:disabled { color: var(--fg-muted); cursor: default; }
  .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pop { position: absolute; bottom: calc(100% + 10px); right: -44px; z-index: 50; width: min(400px, calc(100vw - 24px)); max-height: min(520px, 64vh);
    display: flex; flex-direction: column; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-menu); box-shadow: var(--shadow); }
  .search { display: flex; align-items: center; gap: 8px; padding: 6px 6px 6px 12px; border-bottom: 1px solid var(--border); color: var(--fg-muted); }
  .search input { all: unset; flex: 1; color: var(--fg); font-size: 14px; min-height: 30px; }
  .search input::placeholder { color: var(--fg-muted); }
  .tool { all: unset; display: grid; place-items: center; width: 28px; height: 28px; border-radius: var(--radius-sm); cursor: pointer; color: var(--fg-muted); }
  .tool:hover, .tool.on { background: var(--hover); color: var(--fg); } .tool:focus-visible { outline: 2px solid var(--accent); }
  .arr { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 12px; padding: 8px 12px; border-bottom: 1px solid var(--border); font-size: 12px; color: var(--fg-muted); }
  .arr label { display: inline-flex; align-items: center; gap: 6px; }
  .arr .input { min-height: 28px; padding: 2px 6px; font-size: 12px; width: auto; }
  .seg { display: inline-flex; background: var(--bg-2); border-radius: 999px; padding: 2px; }
  .seg button { all: unset; padding: 3px 10px; border-radius: 999px; cursor: pointer; font-size: 12px; color: var(--fg-muted); }
  .seg button.on { background: var(--surface); color: var(--fg); box-shadow: var(--shadow-sm); } .seg button:focus-visible { outline: 2px solid var(--accent); }
  .linkish { all: unset; cursor: pointer; text-decoration: underline; text-underline-offset: 2px; } .linkish:hover { color: var(--fg); }
  .note { margin: 6px 12px 0; font-size: 12px; }
  .list { overflow: auto; padding: 4px; overscroll-behavior: contain; flex: 1; min-height: 0; }
  .gh { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 6px; width: 100%; padding: 10px 8px 4px; font-size: 12px; font-weight: 500; color: var(--fg-muted); cursor: pointer; }
  .gh.static { cursor: default; } .gh .n { margin-left: auto; font-weight: 400; }
  .chev { display: grid; transition: transform var(--fast) var(--ease); } .chev.shut { transform: rotate(-90deg); }
  .gd { margin: 0 8px 4px; font-size: 12px; color: var(--fg-muted); }
  .opt { display: flex; align-items: center; gap: 10px; padding: 6px 8px; min-height: 36px; border-radius: var(--radius-sm); cursor: pointer; }
  .opt.det { align-items: flex-start; padding: 8px; }
  .opt.on { background: var(--hover); }
  .opt.off { cursor: default; } .opt.off .lb { color: var(--fg-muted); }
  .txt { display: grid; gap: 1px; flex: 1; min-width: 0; }
  .al { font-size: 14px; display: flex; gap: 6px; align-items: center; min-width: 0; }
  .lb { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ln { font-size: 12px; color: var(--fg-muted); overflow-wrap: anywhere; } .ln.warn { color: var(--warn); }
  .mono { font-family: var(--mono); font-size: 11px; }
  .ds { font-size: 12px; color: var(--fg-muted); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .tag { flex: none; font-size: 11px; padding: 0 6px; border-radius: 999px; background: var(--bg-2); color: var(--fg-muted); line-height: 18px; }
  .tag.warn { color: var(--warn); }
  .st { flex: none; width: 7px; height: 7px; border-radius: 999px; background: var(--success); }
  .st[data-state='loading'] { background: var(--warn); } .st[data-state='error'] { background: var(--danger); }
  .fav { all: unset; display: grid; place-items: center; width: 26px; height: 26px; border-radius: 6px; color: var(--fg-muted); cursor: pointer; opacity: 0; flex: none; }
  .opt:hover .fav, .opt.on .fav, .fav.starred { opacity: 1; }
  .fav.starred { color: var(--fg); } .fav.starred :global(svg) { fill: currentColor; }
  .fav:hover { background: var(--selected); color: var(--fg); }
  .ck { display: grid; color: var(--fg); flex: none; }
  .empty { padding: 12px; color: var(--fg-muted); font-size: 13px; margin: 0; }
  .foot { margin: 0; padding: 6px 12px; border-top: 1px solid var(--border); font-size: 11px; color: var(--fg-muted); }
  @media (max-width: 719px) {
    .pop { position: fixed; left: 8px; right: 8px; bottom: 8px; width: auto; max-height: 72vh; }
    .trigger { max-width: min(220px, 52vw); }
    .foot { display: none; }
  }
  @media (prefers-reduced-motion: reduce) { .chev { transition: none; } }
  @media (pointer: coarse) { .trigger { height: 40px; } .opt { min-height: 44px; } .fav { opacity: 1; width: 36px; height: 36px; } }
</style>
