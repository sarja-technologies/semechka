<script lang="ts">
  // The model picker inside the composer: the administrator's groups and order (never re-sorted here), pinned models
  // first, a search box, and what is known about each model. Unknown details are left out, never guessed.
  import { tick } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import ModelIcon from './ModelIcon.svelte';

  let { catalog, value = $bindable(), disabled = false, label = 'Model', needVision = false }:
    { catalog: any; value: string | undefined; disabled?: boolean; label?: string; needVision?: boolean } = $props();

  let open = $state(false), q = $state(''), sel = $state(0);
  let btn: HTMLButtonElement | undefined = $state(), box: HTMLInputElement | undefined = $state();
  const uid = Math.random().toString(36).slice(2, 8);

  const byId = $derived(new Map<string, any>((catalog?.models ?? []).map((m: any) => [m.id, m])));
  const current = $derived(byId.get(value ?? ""));
  const collapsedInit = $derived(Object.fromEntries((catalog?.groups ?? []).map((g: any) => [g.id, !!g.collapsed])));
  let collapsed = $state<Record<string, boolean>>({});
  $effect(() => { collapsed = { ...collapsedInit }; });

  /** Sections in display order. A pinned model also stays in its group: pinning adds a shortcut, it does not move it. */
  const sections = $derived.by(() => {
    if (!catalog) return [];
    const t = q.trim().toLowerCase();
    const hit = (id: string) => { const m = byId.get(id); if (!m) return false; if (!t) return true;
      const p = m.profile ?? {}; return `${m.alias} ${p.provider_label ?? ''} ${p.family ?? ''} ${p.parameters ?? ''}`.toLowerCase().includes(t); };
    const out: { id: string; name: string; description?: string; ids: string[]; foldable: boolean }[] = [];
    if (!t && catalog.pinned?.length && (catalog.groups?.length || catalog.ungrouped?.length > catalog.pinned.length))
      out.push({ id: '_pinned', name: 'Pinned', ids: catalog.pinned, foldable: false });
    for (const g of catalog.groups ?? []) { const ids = g.models.filter(hit); if (ids.length) out.push({ id: g.id, name: g.name, description: g.description, ids, foldable: !t }); }
    const rest = (catalog.ungrouped ?? []).filter(hit);
    if (rest.length) out.push({ id: '_rest', name: catalog.groups?.length ? 'Other models' : '', ids: rest, foldable: false });
    return out;
  });
  const flat = $derived(sections.flatMap((s) => (s.foldable && collapsed[s.id] ? [] : s.ids.map((id) => ({ sec: s.id, id })))));
  const count = $derived((catalog?.models ?? []).length);

  async function show() {
    if (disabled) return;
    open = true; q = ''; await tick();
    sel = Math.max(0, flat.findIndex((f) => f.id === value));
    box?.focus(); scrollSel();
  }
  function hide(focusBtn = true) { open = false; if (focusBtn) btn?.focus(); }
  function pick(id: string) { value = id; hide(); }
  function key(e: KeyboardEvent) {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(flat.length - 1, sel + 1); scrollSel(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); scrollSel(); }
    else if (e.key === 'Home') { e.preventDefault(); sel = 0; scrollSel(); }
    else if (e.key === 'End') { e.preventDefault(); sel = flat.length - 1; scrollSel(); }
    else if (e.key === 'Enter' && flat[sel]) { e.preventDefault(); pick(flat[sel].id); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); hide(); }
    else if (e.key === 'Tab') hide(false);
  }
  function scrollSel() { tick().then(() => document.getElementById(`mc-${uid}-${sel}`)?.scrollIntoView({ block: 'nearest' })); }
  function outside(e: PointerEvent) { if (open && !(e.target as HTMLElement).closest(`[data-mc="${uid}"]`)) hide(false); }

  const ctx = (n: number) => (n >= 1_000_000 ? `${Math.round(n / 100_000) / 10}M` : n >= 1024 ? `${Math.round(n / 1024)}K` : String(n));
  function line(m: any) {
    const p = m.profile ?? {};
    return [p.provider_label, p.parameters, p.quantization, p.context_length ? `${ctx(p.context_length)} context` : null].filter(Boolean).join(' · ');
  }
  const sees = (m: any) => (m.capabilities ?? []).includes('vision');
  const idxOf = (sec: string, id: string) => flat.findIndex((f) => f.sec === sec && f.id === id);
</script>

<svelte:window onpointerdown={outside} />
<div class="mc" data-mc={uid}>
  <button bind:this={btn} type="button" class="trigger" {disabled} onclick={() => (open ? hide() : show())} aria-haspopup="listbox" aria-expanded={open}
    aria-label={`${label}: ${current?.alias ?? 'none chosen'}`} title={current ? line(current) || current.alias : 'Choose a model'}>
    {#if current}<ModelIcon icon={current.profile?.icon} label={current.alias} size={18} />{/if}
    <span class="name">{current?.alias ?? (count ? 'Choose a model' : 'No model available')}</span>
    <Icon name="chevdown" size={14} />
  </button>
  {#if open}
    <div class="pop fade" role="dialog" aria-label={`Choose ${label.toLowerCase()}`}>
      {#if count > 6}
        <div class="search"><Icon name="search" size={15} />
          <input bind:this={box} bind:value={q} oninput={() => (sel = 0)} onkeydown={key} placeholder="Find a model" aria-label="Find a model"
            role="combobox" aria-expanded="true" aria-controls={`mc-${uid}-list`} aria-activedescendant={flat[sel] ? `mc-${uid}-${sel}` : undefined} />
        </div>
      {:else}
        <!-- a focus target that carries the keyboard handling when there is no search box -->
        <input bind:this={box} class="sr-only" onkeydown={key} aria-label="Models: use the arrow keys" role="combobox" aria-expanded="true"
          aria-controls={`mc-${uid}-list`} aria-activedescendant={flat[sel] ? `mc-${uid}-${sel}` : undefined} readonly />
      {/if}
      <div class="list" id={`mc-${uid}-list`} role="listbox" aria-label="Models">
        {#each sections as s (s.id)}
          {#if s.name}
            {#if s.foldable}
              <button type="button" class="gh" aria-expanded={!collapsed[s.id]} onclick={() => (collapsed[s.id] = !collapsed[s.id])} tabindex="-1">
                <span class="chev" class:shut={collapsed[s.id]}><Icon name="chevdown" size={13} /></span>{s.name}<span class="n">{s.ids.length}</span>
              </button>
            {:else}<div class="gh static" role="presentation">{s.name}</div>{/if}
            {#if s.description && !collapsed[s.id]}<p class="gd">{s.description}</p>{/if}
          {/if}
          {#if !(s.foldable && collapsed[s.id])}
            {#each s.ids as id (s.id + id)}
              {@const m = byId.get(id)}
              {@const i = idxOf(s.id, id)}
              {#if m}
                <!-- svelte-ignore a11y_click_events_have_key_events -->
                <div id={`mc-${uid}-${i}`} role="option" tabindex="-1" aria-selected={id === value} class="opt" class:on={i === sel} class:cur={id === value}
                  onclick={() => pick(id)} onpointermove={() => (sel = i)}>
                  <ModelIcon icon={m.profile?.icon} label={m.alias} size={24} />
                  <span class="txt">
                    <span class="al">{m.alias}{#if m.external}<span class="chip warn tiny">External service</span>{/if}</span>
                    {#if line(m)}<span class="ln">{line(m)}</span>{/if}
                    {#if m.profile?.description}<span class="ds">{m.profile.description}</span>{/if}
                    {#if needVision && !sees(m)}<span class="ln warn">Cannot read images: it receives only their file names</span>{/if}
                  </span>
                  {#if id === value}<Icon name="check" size={16} />{/if}
                </div>
              {/if}
            {/each}
          {/if}
        {:else}<p class="empty">{q ? 'No model matches.' : 'No model is available to you for this.'}</p>{/each}
      </div>
    </div>
  {/if}
</div>

<style>
  .mc { position: relative; min-width: 0; }
  .trigger { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 8px; border-radius: var(--radius-sm);
    color: var(--fg); cursor: pointer; max-width: 280px; font-size: 13px; font-weight: 500; }
  .trigger:hover:not(:disabled) { background: var(--hover); }
  .trigger:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .trigger:disabled { color: var(--fg-muted); cursor: default; }
  .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pop { position: absolute; bottom: calc(100% + 8px); left: 0; z-index: 50; width: min(420px, calc(100vw - 24px)); max-height: min(480px, 60vh);
    display: flex; flex-direction: column; background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow); }
  .search { display: flex; align-items: center; gap: 8px; padding: 8px 12px; border-bottom: 1px solid var(--border); color: var(--fg-muted); }
  .search input { all: unset; flex: 1; color: var(--fg); font-size: 14px; min-height: 28px; }
  .list { overflow: auto; padding: 4px; overscroll-behavior: contain; }
  .gh { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 6px; width: 100%; padding: 8px 8px 4px; font-size: 12px; font-weight: 600; color: var(--fg-muted); cursor: pointer; }
  .gh.static { cursor: default; } .gh .n { margin-left: auto; font-weight: 400; }
  .chev { display: grid; transition: transform 120ms ease-out; } .chev.shut { transform: rotate(-90deg); }
  .gd { margin: 0 8px 4px; font-size: 12px; color: var(--fg-muted); }
  .opt { display: flex; align-items: flex-start; gap: 10px; padding: 8px; border-radius: var(--radius-sm); cursor: pointer; }
  .opt.on { background: var(--hover); } .opt.cur { background: var(--selected); }
  .txt { display: grid; gap: 1px; flex: 1; min-width: 0; }
  .al { font-size: 14px; font-weight: 500; display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  .ln { font-size: 12px; color: var(--fg-muted); } .ln.warn { color: var(--warn); }
  .ds { font-size: 12px; color: var(--fg-muted); display: -webkit-box; -webkit-line-clamp: 2; line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .empty { padding: 12px; color: var(--fg-muted); font-size: 13px; margin: 0; }
  @media (max-width: 719px) {
    .pop { position: fixed; left: 8px; right: 8px; bottom: 8px; width: auto; max-height: 70vh; }
    .trigger { max-width: 180px; }
  }
  @media (prefers-reduced-motion: reduce) { .chev { transition: none; } }
</style>
