<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // Contexts: named, persistent knowledge collections. A context is read through ITS home workspace's access rules, so
  // attaching it elsewhere never widens who can read it. Search results always show where each passage came from.
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { get, post, patch, del, put, upload } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { cur, can } from '$lib/ws/current.svelte';
  import { msg, ago, indexChip } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import State from '$lib/components/admin/State.svelte';

  const wid = $derived(page.params.wid ?? '');
  const sel = $derived(page.url.searchParams.get('c') ?? '');
  let list = $state<any[]>([]), loading = $state(true), error = $state('');
  let d = $state<any>(null), dLoading = $state(false), dError = $state('');
  const manage = $derived(can('ctx.manage') && !cur.ws?.archived);
  const homeHere = $derived(d ? d.workspace_id === wid : true);
  const mayWrite = $derived(can('ctx.write') && homeHere && !cur.ws?.archived);

  async function loadList() {
    try { list = await get(`/api/workspaces/${wid}/contexts`); error = ''; } catch (e) { error = msg(e); } finally { loading = false; }
  }
  async function loadDetail() {
    if (!sel) { d = null; return; }
    dLoading = true; dError = ''; res = null;
    try { d = await get(`/api/contexts/${sel}`); } catch (e) { dError = msg(e); d = null; } finally { dLoading = false; }
  }
  $effect(() => { wid; loadList(); });
  $effect(() => { sel; loadDetail(); });
  const choose = (id: string) => goto(id ? `/workspaces/${wid}/contexts?c=${id}` : `/workspaces/${wid}/contexts`);

  // ---- dialogs ----
  type M = '' | 'new' | 'edit' | 'add' | 'versions' | 'attach' | 'delete' | 'import';
  let mode = $state<M>(''), dlg = $state(false), name = $state(''), desc = $state(''), formErr = $state(''), busy = $state(false);
  $effect(() => { if (!dlg) { mode = ''; formErr = ''; } });
  const show = (m: M) => { mode = m; formErr = ''; dlg = true; };
  async function run(fn: () => Promise<any>, done: string, after?: () => void) {
    busy = true; formErr = '';
    try { await fn(); dlg = false; if (done) toast(done); await loadList(); await loadDetail(); after?.(); } catch (e) { formErr = msg(e); } finally { busy = false; }
  }
  const create = () => run(async () => { const c = await post(`/api/workspaces/${wid}/contexts`, { name, description: desc }); choose(c.id); }, 'Context created');
  const openEdit = () => { name = d.name; desc = d.description; show('edit'); };
  const saveEdit = () => run(() => patch(`/api/contexts/${d.id}`, { name, description: desc }), 'Saved');
  const toggleRestricted = () => patch(`/api/contexts/${d.id}`, { restricted: !d.restricted }).then(() => { toast(d.restricted ? 'Restriction removed' : 'Restricted: only people with an explicit rule can open it'); loadDetail(); loadList(); }).catch((e) => toast(msg(e), 'bad'));
  const archive = () => patch(`/api/contexts/${d.id}`, { archived: true }).then(() => { toast('Archived'); choose(''); loadList(); }).catch((e) => toast(msg(e), 'bad'));
  const remove = () => run(() => del(`/api/contexts/${d.id}`), 'Context deleted. The files stay in their workspace.', () => choose(''));
  const dropItem = (f: any) => del(`/api/contexts/${d.id}/items/${f.file_id}`).then(() => { toast('Removed from this context'); loadDetail(); loadList(); }).catch((e) => toast(msg(e), 'bad'));

  // ---- add files ----
  let all = $state<any[]>([]), pickQ = $state(''), chosen = $state<Record<string, boolean>>({}), loadingAll = $state(false);
  const inCtx = $derived(new Set((d?.items ?? []).map((i: any) => i.file_id)));
  const candidates = $derived(all.filter((f) => !inCtx.has(f.id) && (!pickQ || f.name.toLowerCase().includes(pickQ.toLowerCase()))));
  async function openAdd() {
    show('add'); chosen = {}; pickQ = ''; loadingAll = true;
    try { all = await get(`/api/workspaces/${wid}/all-files`); } catch (e) { formErr = msg(e); } finally { loadingAll = false; }
  }
  const addChosen = () => run(async () => { for (const id of Object.keys(chosen).filter((k) => chosen[k])) await put(`/api/contexts/${d.id}/items/${id}`); }, 'Added');

  // ---- versions ----
  let versions = $state<any[]>([]);
  async function openVersions() { show('versions'); versions = []; try { versions = await get(`/api/contexts/${d.id}/versions`); } catch (e) { formErr = msg(e); } }
  const restoreV = (n: number) => run(() => post(`/api/contexts/${d.id}/versions/${n}/restore`), `Restored version ${n} as a new version`);

  // ---- attach / import ----
  let targets = $state<any[]>([]), target = $state('');
  async function openAttach() {
    show('attach'); target = '';
    try { targets = (await get(`/api/orgs/${cur.ws.org_id}/workspaces`)).filter((w: any) => w.id !== d.workspace_id && w.my_permissions?.includes('ctx.manage') && !d.attached_to.includes(w.id)); }
    catch (e) { formErr = msg(e); }
  }
  const attach = () => run(() => post(`/api/contexts/${d.id}/attach`, { workspace_id: target }), 'Attached');
  const detach = (w: string) => del(`/api/contexts/${d.id}/attach/${w}`).then(() => { toast('Detached'); loadDetail(); loadList(); }).catch((e) => toast(msg(e), 'bad'));
  async function doImport(e: Event) {
    const i = e.currentTarget as HTMLInputElement; const f = i.files?.[0]; i.value = ''; if (!f) return;
    try { const c = await upload(`/api/workspaces/${wid}/contexts/import`, f); toast('Imported'); await loadList(); choose(c.id); } catch (err) { toast(msg(err), 'bad'); }
  }

  // ---- try it ----
  let q = $state(''), res = $state<any>(null), searching = $state(false), sErr = $state('');
  async function ask() {
    if (!q.trim() || !d) return; searching = true; sErr = '';
    try { res = await post(`/api/workspaces/${wid}/retrieve`, { query: q, context_ids: [d.id] }); } catch (e) { sErr = msg(e); res = null; } finally { searching = false; }
  }
  const modeLabel = (m: string) => (m === 'hybrid' ? 'keyword + meaning' : m === 'semantic' ? 'meaning' : m);
</script>

<div class="cx">
  <aside aria-label="Contexts" class="side">
    {#if manage}<button class="btn primary" style="width:100%" onclick={() => { name = desc = ''; show('new'); }}><Icon name="plus" size={16} /> New context</button>{/if}
    <State {loading} {error} retry={loadList} />
    {#if !loading && !error && !list.length}<p class="muted small">No contexts yet. A context is a named set of files, such as “Brand guidelines” or “Client Alpha”, that people and agents can search together.</p>{/if}
    <ul>
      {#each list as c (c.id)}
        <li><button class="ci" class:on={c.id === sel} aria-current={c.id === sel ? 'true' : undefined} onclick={() => choose(c.id)}>
          <span class="n">{c.name}</span>
          <span class="tiny muted">{c.files} file{c.files === 1 ? '' : 's'} · v{c.version}{#if c.restricted} · restricted{/if}{#if c.home} · from another workspace{/if}</span></button></li>
      {/each}
    </ul>
    {#if manage}<label class="btn sm ghost" style="margin-top:8px"><Icon name="upload" size={14} /> Import context<input type="file" accept=".zip,application/zip" hidden onchange={doImport} /></label>{/if}
  </aside>

  <section aria-label="Context details">
    {#if !sel}
      <div class="panel"><div class="panel-b"><p><strong>Choose a context</strong>, or create one.</p>
        <p class="muted">Contexts keep their own version history. Searching one shows the exact file, version and location each passage came from, and only ever returns what you are allowed to read.</p></div></div>
    {:else}
      <State loading={dLoading && !d} error={dError} retry={loadDetail} />
      {#if d}
        <header class="head">
          <div class="grow"><h2>{d.name}</h2>{#if d.description}<p class="muted" style="margin:2px 0 0">{d.description}</p>{/if}</div>
          <div class="row">
            <span class="chip mono">v{d.version}</span>{#if d.restricted}<span class="chip warn"><Icon name="lock" size={12} /> Restricted</span>{/if}
            <button class="btn sm" onclick={openVersions}><Icon name="history" size={14} /> History</button>
            {#if can('file.download')}<a class="btn sm" href="/api/contexts/{d.id}/export" download><Icon name="download" size={14} /> Export</a>{/if}
            {#if manage && homeHere}
              <button class="btn sm" onclick={openEdit}><Icon name="edit" size={14} /> Edit</button>
              <button class="btn sm" onclick={openAttach}><Icon name="workspaces" size={14} /> Attach to…</button>
              {#if can('ws.members.manage')}<button class="btn sm" onclick={toggleRestricted}><Icon name="lock" size={14} /> {d.restricted ? 'Unrestrict' : 'Restrict'}</button>{/if}
              <button class="btn sm danger" onclick={() => show('delete')}>Delete</button>
            {/if}
          </div>
        </header>
        {#if !homeHere}<div class="notice info" role="status"><div class="small">This context belongs to another workspace and is read here under that workspace's access rules.</div></div>{/if}

        <section class="panel" aria-label="Ask this context">
          <div class="panel-h"><h3>Search this context</h3></div>
          <div class="panel-b">
            <form class="row" onsubmit={(e) => { e.preventDefault(); ask(); }} role="search">
              <label class="sr-only" for="cq">Question</label><input id="cq" class="input grow" placeholder="Ask a question or type keywords" bind:value={q} />
              <button class="btn primary" disabled={searching || !q.trim()}>Search</button>
            </form>
            {#if sErr}<div class="err" role="alert">{sErr}</div>{/if}
            {#if res}
              <p class="tiny muted" style="margin-top:8px">Searched {res.searched_files} file{res.searched_files === 1 ? '' : 's'} · <span class="chip info">{modeLabel(res.mode)}</span></p>
              {#each res.notes ?? [] as n}<div class="notice warn" role="status"><div class="small">{n}</div></div>{/each}
              {#if !res.passages.length}<p class="muted">No matching passages.</p>{/if}
              {#each res.passages as p}
                <article class="hit"><div class="row"><strong>{p.file}</strong>{#if p.loc}<span class="chip">{p.loc}</span>{/if}<span class="chip">version {p.version}</span><span class="chip info">{modeLabel(p.mode)}</span>
                  {#if p.cosine != null}<span class="chip mono" title="Semantic similarity">{p.cosine.toFixed(2)}</span>{/if}</div><p class="small snip">{p.text}</p></article>
              {/each}
            {/if}
          </div>
        </section>

        <section class="panel" aria-label="Files in this context" style="margin-top:16px">
          <div class="panel-h"><h3>Files <span class="muted">({d.items.length})</span></h3>{#if mayWrite}<button class="btn sm" onclick={openAdd}><Icon name="plus" size={14} /> Add files</button>{/if}</div>
          {#if !d.items.length}<div class="panel-b"><p class="muted">No files yet. Add files from this workspace.</p></div>
          {:else}<div class="tablewrap" use:scrollable style="border:0;border-radius:0"><table class="t"><caption class="sr-only">Files in {d.name}</caption>
            <thead><tr><th>Name</th><th>Kind</th><th>Version</th><th></th></tr></thead>
            <tbody>{#each d.items as f (f.file_id)}<tr><td>{f.name}</td><td class="muted">{f.kind}</td><td class="mono">v{f.version}</td>
              <td style="text-align:right">{#if mayWrite}<button class="btn sm ghost" onclick={() => dropItem(f)} aria-label="Remove {f.name} from this context">Remove</button>{/if}</td></tr>{/each}</tbody></table></div>{/if}
        </section>

        {#if d.attached_to.length}
          <p class="small muted" style="margin-top:12px">Also attached to {d.attached_to.length} other workspace{d.attached_to.length === 1 ? '' : 's'}.
            {#if manage && !homeHere}<button class="linkish" onclick={() => detach(wid)}>Detach from this workspace</button>{/if}</p>
        {/if}
      {/if}
    {/if}
  </section>
</div>

<Modal bind:open={dlg} title={mode === 'new' ? 'New context' : mode === 'edit' ? 'Edit context' : mode === 'add' ? 'Add files' : mode === 'versions' ? 'Context history' : mode === 'attach' ? 'Attach to another workspace' : mode === 'delete' ? 'Delete context' : 'Context'} wide={mode === 'add' || mode === 'versions'}>
  {#if mode === 'new' || mode === 'edit'}
    <form id="cf" onsubmit={(e) => { e.preventDefault(); (mode === 'new' ? create : saveEdit)(); }}>
      <div class="field"><label for="cn">Name</label><input id="cn" class="input" bind:value={name} maxlength="160" required placeholder="Marketing Brand Guidelines" /></div>
      <div class="field"><label for="cd">Description <span class="muted">(optional)</span></label><textarea id="cd" class="input" rows="3" bind:value={desc}></textarea></div>
    </form>
  {:else if mode === 'add'}
    <div class="field"><label for="pq">Filter</label><input id="pq" class="input" bind:value={pickQ} placeholder="Type part of a file name" /></div>
    {#if loadingAll}<p class="muted" role="status">Loading…</p>
    {:else if !candidates.length}<p class="muted">No other files to add.</p>
    {:else}<div class="pickbox">{#each candidates as f (f.id)}
      {@const ix = indexChip(f.index)}
      <label class="row"><input type="checkbox" bind:checked={chosen[f.id]} /><span class="grow">{f.name}{#if f.path} <span class="muted tiny">in {f.path}</span>{/if}</span><span class="chip {ix[0]}">{ix[1]}</span></label>
    {/each}</div>{/if}
    <p class="hint">Files that cannot be read as text (such as pictures) can be added, but have nothing to search.</p>
  {:else if mode === 'versions'}
    <div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">Versions</caption><thead><tr><th>Version</th><th>When</th><th>Change</th><th>Files</th><th></th></tr></thead>
      <tbody>{#each versions as v}<tr><td class="mono">v{v.n}{#if d && v.n === d.version} <span class="chip ok">Current</span>{/if}</td><td class="muted">{ago(v.created_at)}</td><td>{v.note}</td><td class="num">{v.items}</td>
        <td style="text-align:right">{#if d && v.n !== d.version && manage && homeHere}<button class="btn sm" disabled={busy} onclick={() => restoreV(v.n)}>Restore</button>{/if}</td></tr>{/each}</tbody></table></div>
  {:else if mode === 'attach'}
    {#if !targets.length}<p class="muted">There is no other workspace where you can manage contexts.</p>
    {:else}<div class="field"><label for="at">Workspace</label><select id="at" class="input" bind:value={target}><option value="">Choose…</option>{#each targets as t}<option value={t.id}>{t.name}</option>{/each}</select>
      <span class="hint">Attaching makes this context available there. It does not give anyone in that workspace access to it: they can read it only if they already have access here.</span></div>{/if}
  {:else if mode === 'delete'}
    <p>Delete <strong>{d?.name}</strong> and its history? The files themselves stay in the workspace.</p>
  {/if}
  {#if formErr}<div class="err" role="alert" style="margin-top:8px">{formErr}</div>{/if}
  {#snippet footer()}
    {#if mode === 'versions'}<button class="btn" onclick={() => (dlg = false)}>Close</button>
    {:else if mode === 'delete'}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn danger solid" disabled={busy} onclick={remove}>Delete context</button>
    {:else if mode === 'add'}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn primary" disabled={busy || !Object.values(chosen).some(Boolean)} onclick={addChosen}>Add selected</button>
    {:else if mode === 'attach'}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn primary" disabled={busy || !target} onclick={attach}>Attach</button>
    {:else}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn primary" form="cf" type="submit" disabled={busy || !name.trim()}>{mode === 'new' ? 'Create context' : 'Save'}</button>{/if}
  {/snippet}
</Modal>

<style>
  .cx { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 24px; align-items: start; }
  .side { display: grid; gap: 8px; } .side ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
  .ci { all: unset; cursor: pointer; display: grid; gap: 2px; padding: 8px 10px; border-radius: var(--radius-sm); box-sizing: border-box; width: 100%; }
  .ci:hover { background: var(--hover); } .ci.on { background: var(--selected); box-shadow: inset 0 0 0 1px var(--accent); } .ci:focus-visible { outline: 2px solid var(--accent); }
  .ci .n { font-weight: 600; overflow-wrap: anywhere; }
  .head { display: flex; gap: 12px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; margin-bottom: 16px; }
  .hit { padding: 8px 0; border-top: 1px solid var(--border); } .snip { margin: 4px 0 0; white-space: pre-wrap; max-height: 9em; overflow: auto; color: var(--fg-2); }
  .pickbox { max-height: 45vh; overflow: auto; display: grid; gap: 2px; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px; }
  @media (max-width: 860px) { .cx { grid-template-columns: minmax(0, 1fr); } }
</style>
