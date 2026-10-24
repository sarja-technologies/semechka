<script lang="ts">
  // Conversation history: new conversation, search, pinned, folders, date groups, and the secondary pages.
  // Every action here is a request the server checks; switches the administrator turned off are not offered.
  import { onMount, tick } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { app } from '$lib/session.svelte';
  import { get, post, patch, del, qs } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { list, loadList, loadFolders, dateGroups, drop, upsert, type ConvRow } from '$lib/chat/store.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import { navHref } from '$lib/nav';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Wordmark from '$lib/components/Wordmark.svelte';

  let { activeId = null, onnew, ondeleted, oncollapse }: { activeId?: string | null; onnew: () => void; ondeleted: (id: string) => void; oncollapse: () => void } = $props();

  const ui = $derived(app.me.ui);
  const f = $derived(ui.ui ?? {});
  const base = $derived(list.workspace === 'code' ? '/code' : '/chat');
  const path = $derived(page.url.pathname);
  const pages = $derived<any[]>((ui.pages ?? []).filter((p: any) => p.available && !(list.workspace === 'code' && p.id === 'code')));

  // ---- list --------------------------------------------------------------------------------------------------
  let timer: ReturnType<typeof setTimeout>;
  function onSearch() { clearTimeout(timer); timer = setTimeout(() => loadList(true), 220); }
  const loose = $derived(list.items.filter((c) => !c.pinned && (!f.folders || !c.folder_id)));
  const pinned = $derived(f.pinning ? list.items.filter((c) => c.pinned) : []);
  const groups = $derived<[string, ConvRow[]][]>(list.archived ? (list.items.length ? [['Archived', list.items]] : []) : list.q ? [['Results', list.items]] : f.history_grouping === false ? (loose.length ? [['Recent', loose]] : []) : dateGroups(loose));
  let sentinel: HTMLDivElement | undefined = $state(), scroller: HTMLDivElement | undefined = $state();
  onMount(() => {
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting) && list.hasMore && !list.loading) loadList(false); }, { root: scroller, rootMargin: '200px' });
    if (sentinel) io.observe(sentinel);
    return () => io.disconnect();
  });

  // ---- folders -----------------------------------------------------------------------------------------------
  const openKey = 'sem.folders.open';
  let open = $state<Record<string, boolean>>(readOpen()), inFolder = $state<Record<string, ConvRow[] | undefined>>({});
  function readOpen() { try { return JSON.parse(localStorage.getItem(openKey) ?? '{}'); } catch { return {}; } }
  async function toggleFolder(id: string) {
    open[id] = !open[id];
    try { localStorage.setItem(openKey, JSON.stringify(open)); } catch { /* storage unavailable */ }
    if (open[id]) await loadFolder(id);
  }
  async function loadFolder(id: string) {
    try { inFolder[id] = await get(`/api/chat/conversations${qs({ folder_id: id, mode: list.workspace === 'code' ? 'code' : undefined, limit: 200 })}`); }
    catch (e) { toast(errText(e), 'bad'); }
  }
  $effect(() => { if (f.folders) for (const fd of list.folders) if (open[fd.id] && !inFolder[fd.id]) loadFolder(fd.id); });
  let newFolder = $state<string | null>(null), renamingFolder = $state<{ id: string; name: string } | null>(null);
  async function createFolder() {
    const name = newFolder?.trim(); newFolder = null; if (!name) return;
    try { const fd = await post('/api/chat/folders', { name }); list.folders = [...list.folders, fd]; } catch (e) { toast(errText(e), 'bad'); }
  }
  async function renameFolder() {
    const r = renamingFolder; renamingFolder = null; if (!r || !r.name.trim()) return;
    try { await patch(`/api/chat/folders/${r.id}`, { name: r.name.trim() }); const fd = list.folders.find((x) => x.id === r.id); if (fd) fd.name = r.name.trim(); } catch (e) { toast(errText(e), 'bad'); }
  }
  async function deleteFolder(id: string) {
    menu = null;
    try { await del(`/api/chat/folders/${id}`); list.folders = list.folders.filter((x) => x.id !== id); delete inFolder[id]; await loadList(true); toast('Folder removed. Its conversations are back in the main list.'); }
    catch (e) { toast(errText(e), 'bad'); }
  }

  // ---- item actions ------------------------------------------------------------------------------------------
  let menu = $state<{ id: string; kind: 'conv' | 'folder'; x: number; y: number } | null>(null);
  let renaming = $state<{ id: string; title: string } | null>(null), confirmDel = $state<ConvRow | null>(null);
  const find = (id: string) => list.items.find((c) => c.id === id) ?? Object.values(inFolder).flat().find((c) => c?.id === id);
  function openMenu(e: MouseEvent, id: string, kind: 'conv' | 'folder') {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    menu = menu?.id === id ? null : { id, kind, x: Math.min(r.left, innerWidth - 230), y: Math.min(r.bottom + 4, innerHeight - 280) };
    tick().then(() => document.querySelector<HTMLElement>('.cmenu [role="menuitem"]')?.focus());
  }
  function menuKey(e: KeyboardEvent) {
    const items = [...document.querySelectorAll<HTMLElement>('.cmenu [role="menuitem"]')];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); menu = null; }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
  }
  async function change(id: string, body: Record<string, unknown>, done?: string) {
    menu = null;
    try {
      const r = await patch(`/api/chat/conversations/${id}`, body);
      const { ok: _ok, ...row } = r;
      if (list.items.some((c) => c.id === id)) upsert(row); else if (row.folder_id === null || body.pinned !== undefined) upsert(row);
      for (const k of Object.keys(inFolder)) inFolder[k] = inFolder[k]?.filter((c) => c.id !== id || row.folder_id === k).map((c) => (c.id === id ? row : c));
      if (row.folder_id && inFolder[row.folder_id] && !inFolder[row.folder_id]!.some((c) => c.id === id)) inFolder[row.folder_id] = [row, ...inFolder[row.folder_id]!];
      if (done) toast(done);
    } catch (e) { toast(errText(e), 'bad'); }
  }
  async function saveRename() {
    const r = renaming; renaming = null;
    if (r && r.title.trim()) await change(r.id, { title: r.title.trim() });
  }
  async function remove() {
    const c = confirmDel; confirmDel = null; if (!c) return;
    try {
      await del(`/api/chat/conversations/${c.id}`);
      drop(c.id); for (const k of Object.keys(inFolder)) inFolder[k] = inFolder[k]?.filter((x) => x.id !== c.id);
      toast('Conversation deleted'); ondeleted(c.id);
    } catch (e) { toast(errText(e), 'bad'); }
  }

  // ---- keyboard: arrows move between conversations, Home/End jump ---------------------------------------------
  function listKey(e: KeyboardEvent) {
    if (!(e.target as HTMLElement).matches('a.ci')) return;
    const links = [...(scroller?.querySelectorAll<HTMLAnchorElement>('a.ci') ?? [])];
    const i = links.indexOf(e.target as HTMLAnchorElement);
    const go = (j: number) => { e.preventDefault(); links[Math.max(0, Math.min(links.length - 1, j))]?.focus(); };
    if (e.key === 'ArrowDown') go(i + 1); else if (e.key === 'ArrowUp') go(i - 1); else if (e.key === 'Home') go(0); else if (e.key === 'End') go(links.length - 1);
    else if (e.key === 'F2') { const c = find(links[i]?.dataset.id ?? ''); if (c) { e.preventDefault(); renaming = { id: c.id, title: c.title }; } }
  }

  // ---- width -------------------------------------------------------------------------------------------------
  function startResize(e: PointerEvent) {
    const el = (e.currentTarget as HTMLElement); el.setPointerCapture(e.pointerId);
    const root = document.documentElement;
    const move = (ev: PointerEvent) => { const w = Math.max(220, Math.min(420, ev.clientX)); root.style.setProperty('--sidebar-user-w', `${w}px`); };
    const up = () => {
      el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', up);
      try { localStorage.setItem('sem.sidebar.w', getComputedStyle(root).getPropertyValue('--sidebar-user-w').trim()); } catch { /* storage unavailable */ }
    };
    el.addEventListener('pointermove', move); el.addEventListener('pointerup', up);
  }
  function resizeKey(e: KeyboardEvent) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const root = document.documentElement, cur = parseInt(getComputedStyle(root).getPropertyValue('--sidebar-user-w') || getComputedStyle(root).getPropertyValue('--sidebar-w')) || 272;
    const w = Math.max(220, Math.min(420, cur + (e.key === 'ArrowRight' ? 16 : -16)));
    root.style.setProperty('--sidebar-user-w', `${w}px`);
    try { localStorage.setItem('sem.sidebar.w', `${w}px`); } catch { /* storage unavailable */ }
  }
  const retention = $derived(ui.retention?.show && ui.retention.place === 'sidebar' ? ui.retention.text : '');
  const close = () => (list.sidebarOpen = false);
</script>

{#snippet item(c: ConvRow)}
  <li class="it" class:on={c.id === activeId}>
    {#if renaming?.id === c.id}
      <!-- svelte-ignore a11y_autofocus -->
      <input class="input ren" bind:value={renaming.title} maxlength="200" aria-label="Conversation name" autofocus
        onkeydown={(e) => { if (e.key === 'Enter') saveRename(); else if (e.key === 'Escape') { e.stopPropagation(); renaming = null; } }} onblur={saveRename} />
    {:else}
      <a class="ci" href={`${base}/${c.id}`} data-id={c.id} aria-current={c.id === activeId ? 'page' : undefined} onclick={close} title={c.title}>
        {#if c.pinned && f.pinning}<span class="pi" aria-label="Pinned"><Icon name="pin" size={12} /></span>{/if}<span class="tt">{c.title}</span>
      </a>
      <button class="more" aria-label={`Actions for ${c.title}`} aria-haspopup="menu" aria-expanded={menu?.id === c.id} onclick={(e) => openMenu(e, c.id, 'conv')}><Icon name="more" size={16} /></button>
    {/if}
  </li>
{/snippet}

<aside class="side" class:sheet={list.sidebarOpen} aria-label="Conversations">
  <div class="sh">
    {#if ui.brand?.sidebar_brand}<a class="sb" href="/" aria-label="Home"><Wordmark size={15} /></a>{:else}<span class="grow"></span>{/if}
    <button class="btn ghost icon sm" onclick={oncollapse} aria-label="Hide the sidebar" title="Hide the sidebar (Ctrl+Shift+S)"><Icon name="sidebar" size={17} /></button>
    <button class="btn ghost icon sm only-sheet" onclick={close} aria-label="Close"><Icon name="x" size={17} /></button>
  </div>
  <button class="btn new" onclick={() => { close(); onnew(); }} aria-keyshortcuts="Control+Shift+O"><Icon name="edit" size={16} /> New conversation</button>
  {#if f.search !== false}
    <div class="search"><Icon name="search" size={15} />
      <label class="sr-only" for="side-q">Search conversations</label>
      <input id="side-q" data-search type="search" placeholder="Search" bind:value={list.q} oninput={onSearch} />
    </div>
  {/if}

  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="lists" bind:this={scroller} onkeydown={listKey}>
    {#if list.error}<div class="notice bad small" role="alert"><div>{list.error} <button class="btn sm" onclick={() => loadList(true)}>Retry</button></div></div>{/if}
    {#if list.archived}<button class="btn ghost sm back" onclick={() => { list.archived = false; loadList(true); }}><span class="flip"><Icon name="chevron" size={14} /></span> Back to conversations</button>{/if}
    {#if pinned.length && !list.q && !list.archived}
      <h2 class="gh">Pinned</h2><ul>{#each pinned as c (c.id)}{@render item(c)}{/each}</ul>
    {/if}
    {#if f.folders && !list.q && !list.archived}
      <div class="fh"><h2 class="gh">Folders</h2><button class="btn ghost icon sm" aria-label="New folder" title="New folder" onclick={() => (newFolder = '')}><Icon name="plus" size={14} /></button></div>
      {#if newFolder !== null}
        <!-- svelte-ignore a11y_autofocus -->
        <input class="input ren" bind:value={newFolder} maxlength="80" placeholder="Folder name" aria-label="New folder name" autofocus
          onkeydown={(e) => { if (e.key === 'Enter') createFolder(); else if (e.key === 'Escape') { e.stopPropagation(); newFolder = null; } }} onblur={createFolder} />
      {/if}
      <ul>
        {#each list.folders as fd (fd.id)}
          <li class="fd">
            {#if renamingFolder?.id === fd.id}
              <!-- svelte-ignore a11y_autofocus -->
              <input class="input ren" bind:value={renamingFolder.name} maxlength="80" aria-label="Folder name" autofocus
                onkeydown={(e) => { if (e.key === 'Enter') renameFolder(); else if (e.key === 'Escape') { e.stopPropagation(); renamingFolder = null; } }} onblur={renameFolder} />
            {:else}
              <div class="it">
                <button class="ci fbtn" aria-expanded={!!open[fd.id]} onclick={() => toggleFolder(fd.id)}><span class="chev" class:shut={!open[fd.id]}><Icon name="chevdown" size={13} /></span><Icon name="folder" size={15} /><span class="tt">{fd.name}</span></button>
                <button class="more" aria-label={`Actions for folder ${fd.name}`} aria-haspopup="menu" aria-expanded={menu?.id === fd.id} onclick={(e) => openMenu(e, fd.id, 'folder')}><Icon name="more" size={16} /></button>
              </div>
            {/if}
            {#if open[fd.id]}
              <ul class="sub">{#each inFolder[fd.id] ?? [] as c (c.id)}{@render item(c)}{:else}<li class="tiny muted emp">{inFolder[fd.id] ? 'Empty. Move conversations here from their menu.' : 'Loading…'}</li>{/each}</ul>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
    {#each groups as [label, rows] (label)}
      <h2 class="gh">{label}</h2><ul>{#each rows as c (c.id)}{@render item(c)}{/each}</ul>
    {/each}
    {#if !list.loading && !list.items.length && !list.error}
      <p class="tiny muted emp">{list.q ? 'No conversation matches that search.' : list.archived ? 'Nothing is archived.' : 'No conversations yet.'}</p>
    {/if}
    {#if !list.archived && !list.q && !list.hasMore && !list.loading}<button class="linkish tiny" onclick={() => { list.archived = true; loadList(true); }}>Archived conversations</button>{/if}
    <div bind:this={sentinel} class="sent">{#if list.loading}<span class="tiny muted" role="status">Loading…</span>{/if}</div>
  </div>

  {#if pages.length || app.me.admin || retention}
    <nav class="pages" aria-label="More">
      {#if retention}<p class="tiny muted ret">{retention}</p>{/if}
      {#each pages as p (p.id)}
        <a href={navHref(p)} class="pg" class:on={path.startsWith(navHref(p))} aria-current={path.startsWith(navHref(p)) ? 'page' : undefined} onclick={close}><Icon name={p.id} size={16} />{p.label}</a>
      {/each}
    </nav>
  {/if}
  <!-- a focusable splitter (WAI-ARIA window splitter): arrow keys resize it -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <div class="grip" role="separator" aria-orientation="vertical" aria-label="Resize the sidebar" aria-valuemin="220" aria-valuemax="420" tabindex="0" onpointerdown={startResize} onkeydown={resizeKey}></div>
</aside>
{#if list.sidebarOpen}<div class="scrim" role="presentation" onclick={close}></div>{/if}

{#if menu}
  {@const c = menu.kind === 'conv' ? find(menu.id) : null}
  {@const fd = menu.kind === 'folder' ? list.folders.find((x) => x.id === menu?.id) : null}
  <div class="mscrim" role="presentation" onclick={() => (menu = null)}></div>
  <div class="cmenu fade" role="menu" tabindex="-1" style:left="{menu.x}px" style:top="{menu.y}px" onkeydown={menuKey}>
    {#if c}
      <button role="menuitem" onclick={() => { menu = null; renaming = { id: c.id, title: c.title }; }}><Icon name="edit" size={15} /> Rename</button>
      {#if f.pinning}<button role="menuitem" onclick={() => change(c.id, { pinned: !c.pinned })}><Icon name="pin" size={15} /> {c.pinned ? 'Unpin' : 'Pin to top'}</button>{/if}
      {#if f.folders}
        {#each list.folders.filter((x) => x.id !== c.folder_id) as x (x.id)}<button role="menuitem" onclick={() => change(c.id, { folder_id: x.id }, `Moved to ${x.name}`)}><Icon name="folder" size={15} /> Move to {x.name}</button>{/each}
        {#if c.folder_id}<button role="menuitem" onclick={() => change(c.id, { folder_id: '' })}><Icon name="folder" size={15} /> Remove from folder</button>{/if}
      {/if}
      <button role="menuitem" onclick={() => change(c.id, { archived: !c.archived }, c.archived ? 'Restored' : 'Archived').then(() => drop(c.id))}><Icon name="history" size={15} /> {c.archived ? 'Restore from archive' : 'Archive'}</button>
      {#if ui.composer.delete}<hr /><button role="menuitem" class="danger" onclick={() => { menu = null; confirmDel = c; }}><Icon name="trash" size={15} /> Delete…</button>{/if}
    {:else if fd}
      <button role="menuitem" onclick={() => { menu = null; renamingFolder = { id: fd.id, name: fd.name }; }}><Icon name="edit" size={15} /> Rename folder</button>
      <button role="menuitem" class="danger" onclick={() => deleteFolder(fd.id)}><Icon name="trash" size={15} /> Remove folder (keeps its conversations)</button>
    {/if}
  </div>
{/if}

<Modal open={!!confirmDel} title="Delete this conversation?">
  <p>“{confirmDel?.title}”, every version of every reply in it, its attachments and the pictures and videos made in it will be deleted from this server. This cannot be undone.</p>
  <p class="small muted">Copies in server backups are removed when those backups expire on their own schedule. Pictures sent to the inference engine to be edited or animated are kept by the engine under its own policy.</p>
  {#snippet footer()}<button class="btn" onclick={() => (confirmDel = null)}>Cancel</button><button class="btn danger solid" onclick={remove}>Delete conversation</button>{/snippet}
</Modal>

<style>
  .side { position: relative; width: var(--sidebar-user-w, var(--sidebar-w)); flex: none; display: flex; flex-direction: column; min-height: 0; background: var(--sidebar-bg); border-right: 1px solid var(--border); padding: 8px; gap: 6px; }
  .sh { display: flex; align-items: center; gap: 4px; min-height: 32px; padding-left: 4px; }
  .sb { flex: 1; min-width: 0; overflow: hidden; color: var(--fg); text-decoration: none; }
  .only-sheet { display: none; }
  .new { justify-content: flex-start; width: 100%; background: transparent; border-color: var(--border-strong); }
  .search { display: flex; align-items: center; gap: 8px; padding: 0 10px; height: 34px; border-radius: var(--radius-sm); background: var(--bg); border: 1px solid var(--border); color: var(--fg-muted); }
  .search:focus-within { border-color: var(--fg-muted); }
  .search input { all: unset; flex: 1; min-width: 0; color: var(--fg); font-size: 14px; }
  .lists { flex: 1; overflow-y: auto; min-height: 0; margin: 0 -4px; padding: 0 4px; overscroll-behavior: contain; }
  ul { list-style: none; margin: 0; padding: 0; }
  .gh { font-size: 12px; font-weight: 600; color: var(--fg-muted); margin: 14px 8px 4px; }
  .fh { display: flex; align-items: center; justify-content: space-between; } .fh .gh { margin-right: 0; }
  .it { position: relative; display: flex; align-items: center; border-radius: var(--radius-sm); }
  .it:hover, .it:focus-within { background: var(--hover); }
  .it.on { background: var(--selected); }
  .ci { all: unset; box-sizing: border-box; flex: 1; min-width: 0; display: flex; align-items: center; gap: 6px; padding: 0 8px; min-height: 34px; color: var(--fg); cursor: pointer; font-size: 14px; border-radius: var(--radius-sm); }
  .ci:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .it.on .ci { font-weight: 600; }
  .tt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .pi { color: var(--fg-muted); display: grid; }
  .more { all: unset; display: grid; place-items: center; width: 28px; height: 28px; margin-right: 3px; border-radius: var(--radius-sm); color: var(--fg-muted); cursor: pointer; opacity: 0; }
  .more:hover { background: var(--bg); color: var(--fg); } .more:focus-visible { opacity: 1; outline: 2px solid var(--accent); }
  .it:hover .more, .it:focus-within .more, .more[aria-expanded='true'] { opacity: 1; }
  @media (hover: none) { .more { opacity: 1; } }
  .fbtn { gap: 6px; }
  .chev { display: grid; color: var(--fg-muted); transition: transform 120ms ease-out; } .chev.shut { transform: rotate(-90deg); }
  .sub { padding-left: 14px; }
  .emp { padding: 4px 8px 8px; margin: 0; }
  .ren { min-height: 32px; margin: 1px 0; font-size: 14px; }
  .sent { min-height: 8px; padding: 4px 8px; }
  .linkish { all: unset; cursor: pointer; color: var(--fg-muted); text-decoration: underline; text-underline-offset: 2px; margin: 4px 8px; }
  .linkish:hover { color: var(--fg); } .linkish:focus-visible { outline: 2px solid var(--accent); }
  .back { margin: 4px 0; } .flip { display: grid; transform: rotate(180deg); }
  .pages { border-top: 1px solid var(--border); padding-top: 6px; display: grid; gap: 1px; }
  .ret { margin: 0 8px 6px; }
  .pg { display: flex; align-items: center; gap: 10px; padding: 0 8px; min-height: 34px; color: var(--fg); text-decoration: none; border-radius: var(--radius-sm); font-size: 14px; }
  .pg:hover { background: var(--hover); } .pg.on { background: var(--selected); font-weight: 600; }
  .grip { position: absolute; top: 0; right: -3px; width: 6px; height: 100%; cursor: col-resize; z-index: 2; }
  .grip:hover, .grip:focus-visible { background: var(--border-strong); outline: none; }
  .mscrim { position: fixed; inset: 0; z-index: 70; }
  .cmenu { position: fixed; z-index: 71; min-width: 200px; max-width: 260px; max-height: 60vh; overflow: auto; background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow); padding: 4px; }
  .cmenu button { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 8px; width: 100%; padding: 0 10px; min-height: 34px; border-radius: var(--radius-sm); cursor: pointer; font-size: 14px; color: var(--fg); }
  .cmenu button:hover, .cmenu button:focus-visible { background: var(--hover); outline: none; }
  .cmenu .danger { color: var(--danger); }
  .cmenu hr { margin: 4px 0; }
  .scrim { display: none; }
  @media (max-width: 899px) {
    .side { position: fixed; z-index: 60; top: 0; bottom: 0; left: 0; width: min(320px, 88vw); transform: translateX(-102%); visibility: hidden; box-shadow: var(--shadow); }
    .side.sheet { transform: none; visibility: visible; }
    .only-sheet { display: inline-flex; } .sh > .btn:not(.only-sheet) { display: none; }
    .grip { display: none; }
    .scrim { display: block; position: fixed; inset: 0; background: rgba(10, 12, 12, 0.5); z-index: 55; }
  }
  @media (prefers-reduced-motion: no-preference) { .side { transition: transform 160ms ease-out, visibility 160ms; } }
  @media (pointer: coarse) { .ci, .pg { min-height: 44px; } .more { width: 40px; height: 40px; } }
</style>
