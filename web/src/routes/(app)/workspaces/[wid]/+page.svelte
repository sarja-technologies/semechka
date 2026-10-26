<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // Files: the workspace's real folders and files, versions, trash, search with sources. Everything shown is already
  // filtered by the server to what this person may read.
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { get, post, put, patch, del, upload, qs } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { cur, can } from '$lib/ws/current.svelte';
  import { msg, bytes, ago, indexChip, fileIcon } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import State from '$lib/components/admin/State.svelte';
  import FolderPicker from '$lib/ws/FolderPicker.svelte';
  import Menu from '$lib/components/Menu.svelte';

  const wid = $derived(page.params.wid ?? '');
  const folder = $derived(page.url.searchParams.get('folder') ?? '');
  let data = $state<{ folders: any[]; files: any[]; path: { id: string; name: string }[] }>({ folders: [], files: [], path: [] });
  let loading = $state(true), error = $state(''), trash = $state(false);
  const writable = $derived(can('file.write') && !cur.ws?.archived);

  async function load() {
    loading = true;
    try { data = await get(`/api/workspaces/${wid}/files${qs({ folder_id: folder, trash: trash ? 'true' : '' })}`); error = ''; }
    catch (e) { error = msg(e); } finally { loading = false; }
  }
  $effect(() => { wid; folder; trash; load(); });
  // Indexing runs in the background: while anything is still being read, look again every few seconds.
  $effect(() => {
    if (!data.files.some((f) => f.index === 'pending')) return;
    const t = setInterval(load, 4000); return () => clearInterval(t);
  });
  const open = (id: string) => goto(id ? `/workspaces/${wid}?folder=${id}` : `/workspaces/${wid}`);

  // ---- upload ----
  let drag = $state(false), ups = $state<{ name: string; state: 'wait' | 'ok' | 'bad'; text: string }[]>([]), uploading = $state(false);
  async function send(files: File[]) {
    if (!files.length || !writable) return;
    uploading = true; ups = files.map((f) => ({ name: f.name, state: 'wait', text: 'Waiting' }));
    for (let i = 0; i < files.length; i++) {
      try { await upload(`/api/workspaces/${wid}/files`, files[i], { folder_id: folder }); ups[i] = { ...ups[i], state: 'ok', text: 'Uploaded' }; }
      catch (e) { ups[i] = { ...ups[i], state: 'bad', text: msg(e) }; }
    }
    uploading = false; load();
    if (ups.every((u) => u.state === 'ok')) setTimeout(() => (ups = []), 2500);
  }
  function pick(e: Event) { const i = e.currentTarget as HTMLInputElement; const f = [...(i.files ?? [])]; i.value = ''; send(f); }
  function drop(e: DragEvent) { e.preventDefault(); drag = false; send([...(e.dataTransfer?.files ?? [])]); }

  // ---- folders, documents, rename, move, restrict, trash ----
  let mode = $state<'' | 'folder' | 'doc' | 'rename' | 'move' | 'trash' | 'edit' | 'versions' | 'preview'>(''), target = $state<any>(null), isFolder = $state(false);
  let text = $state(''), name = $state(''), dest = $state<string | null>(null), formErr = $state(''), busy = $state(false);
  let dlg = $state(false);
  $effect(() => { if (!dlg) { mode = ''; formErr = ''; } });
  function ask(m: typeof mode, t: any = null, folderRow = false) { mode = m; target = t; isFolder = folderRow; name = t?.name ?? ''; text = ''; formErr = ''; dest = null; dlg = true; }

  async function run(fn: () => Promise<unknown>, done: string) {
    busy = true; formErr = '';
    try { await fn(); dlg = false; if (done) toast(done); await load(); } catch (e) { formErr = msg(e); } finally { busy = false; }
  }
  const mkFolder = () => run(() => post(`/api/workspaces/${wid}/folders`, { name, parent_id: folder || null }), 'Folder created');
  const mkDoc = () => run(async () => { const n = /\.[A-Za-z0-9]+$/.test(name) ? name : `${name}.md`; await upload(`/api/workspaces/${wid}/files`, new Blob([text], { type: 'text/plain' }), { folder_id: folder }, n); }, 'Document created');
  const rename = () => run(() => (isFolder ? patch(`/api/folders/${target.id}`, { name }) : patch(`/api/files/${target.id}`, { name })), 'Renamed');
  const move = () => run(() => (isFolder ? patch(`/api/folders/${target.id}`, { parent_id: dest }) : patch(`/api/files/${target.id}`, { folder_id: dest })), 'Moved');
  const toTrash = () => run(() => del(`/api/files/${target.id}`), 'Moved to the trash. It can be restored for a while.');
  const restoreFile = (f: any) => post(`/api/files/${f.id}/untrash`).then(() => { toast('Restored'); load(); }).catch((e) => toast(msg(e), 'bad'));
  const restrict = (t: any, isF: boolean) => (isF ? patch(`/api/folders/${t.id}`, { restricted: !t.restricted }) : patch(`/api/files/${t.id}`, { restricted: !t.restricted }))
    .then(() => { toast(t.restricted ? 'Restriction removed' : 'Now restricted: only people with an explicit rule can open it'); load(); }).catch((e) => toast(msg(e), 'bad'));

  // ---- preview / edit / versions ----
  let info = $state<any>(null), body = $state(''), bodyErr = $state('');
  const url = (f: any, v?: number, dl = false) => `/api/files/${f.id}/content${qs({ version: v, download: dl ? '1' : '' })}`;
  const textual = (f: any) => ['text', 'code', 'data'].includes(f.kind);
  async function preview(f: any) {
    ask('preview', f); info = null; body = ''; bodyErr = '';
    try {
      info = await get(`/api/files/${f.id}`);
      if (textual(f) && f.size < 300_000) { const r = await fetch(url(f), { credentials: 'same-origin' }); body = await r.text(); }
    } catch (e) { bodyErr = msg(e); }
  }
  async function edit(f: any) {
    ask('edit', f);
    try { const r = await fetch(url(f), { credentials: 'same-origin' }); text = await r.text(); } catch (e) { formErr = msg(e); }
  }
  const saveEdit = () => run(() => put(`/api/files/${target.id}/text`, { text, note: 'Edited in the browser' }), 'Saved as a new version');
  async function versions(f: any) { ask('versions', f); try { info = await get(`/api/files/${f.id}`); } catch (e) { formErr = msg(e); } }
  const restoreVersion = (n: number) => run(() => post(`/api/files/${target.id}/restore`, { version: n }), `Version ${n} restored as a new version`);
  const imageish = (f: any) => ['image/png', 'image/jpeg', 'image/gif', 'image/webp'].includes(f.mime);

  // ---- search ----
  let q = $state(''), res = $state<any>(null), searching = $state(false), sErr = $state('');
  async function search() {
    if (!q.trim()) { res = null; return; }
    searching = true; sErr = '';
    try { res = await get(`/api/workspaces/${wid}/search${qs({ q })}`); } catch (e) { sErr = msg(e); res = null; } finally { searching = false; }
  }
  const modeChip = (m: string) => (m === 'hybrid' ? 'keyword + meaning' : m === 'semantic' ? 'meaning' : m);
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="files" class:drag ondragover={(e) => { if (writable) { e.preventDefault(); drag = true; } }} ondragleave={(e) => { if (e.currentTarget === e.target) drag = false; }} ondrop={drop}>
  <div class="bar">
    <nav class="crumbs" aria-label="Folder path">
      <button class="linkish" onclick={() => open('')}>{cur.ws?.name}</button>
      {#each data.path as p}<span aria-hidden="true">/</span><button class="linkish" onclick={() => open(p.id)}>{p.name}</button>{/each}
      {#if trash}<span aria-hidden="true">/</span><strong>Trash</strong>{/if}
    </nav>
    <span class="spacer"></span>
    <form class="search" onsubmit={(e) => { e.preventDefault(); search(); }} role="search">
      <label class="sr-only" for="fq">Search this workspace</label>
      <input id="fq" class="input" type="search" placeholder="Search files and their contents" bind:value={q} />
      <button class="btn" type="submit" disabled={searching}><Icon name="search" size={16} /> Search</button>
    </form>
  </div>

  <div class="row tools">
    {#if writable && !trash}
      <label class="btn primary"><Icon name="upload" size={16} /> Upload<input type="file" multiple hidden onchange={pick} /></label>
      <button class="btn" onclick={() => ask('folder')}><Icon name="folder" size={16} /> New folder</button>
      <button class="btn" onclick={() => ask('doc')}><Icon name="edit" size={16} /> New document</button>
    {/if}
    <span class="spacer"></span>
    {#if can('file.restore') || can('file.delete')}
      <button class="btn ghost" aria-pressed={trash} onclick={() => (trash = !trash)}><Icon name="trash" size={16} /> {trash ? 'Back to files' : 'Trash'}</button>
    {/if}
  </div>
  {#if drag}<div class="drop" aria-hidden="true">Drop files to upload here</div>{/if}
  {#if ups.length}
    <ul class="ups" aria-live="polite">
      {#each ups as u}<li><span class="chip" class:ok={u.state === 'ok'} class:bad={u.state === 'bad'}>{u.text}</span> {u.name}</li>{/each}
    </ul>
  {/if}

  {#if sErr}<div class="notice bad" role="alert"><div>{sErr}</div></div>{/if}
  {#if res}
    <section class="panel results" aria-label="Search results">
      <div class="panel-h"><h2>Results for “{q}”</h2><button class="btn sm ghost" onclick={() => (res = null)}>Clear</button></div>
      <div class="panel-b">
        {#each res.notes ?? [] as n}<div class="notice warn" role="status"><div class="small">{n}</div></div>{/each}
        {#if !res.files.length && !res.passages.length}<p class="muted">Nothing matches. Files that are still being read, or that cannot be read as text, are found by name only.</p>{/if}
        {#if res.files.length}<h3>By name</h3><ul class="plain">{#each res.files as f}<li><button class="linkish" onclick={() => preview(f)}>{f.name}</button></li>{/each}</ul>{/if}
        {#if res.passages.length}<h3 style="margin-top:12px">Passages</h3>
          {#each res.passages as p}
            <article class="hit">
              <div class="row"><button class="linkish" onclick={() => preview({ id: p.file_id, name: p.file, kind: 'doc', size: 0, mime: '' })}><strong>{p.file}</strong></button>
                {#if p.loc}<span class="chip">{p.loc}</span>{/if}<span class="chip info">{modeChip(p.mode)}</span><span class="chip">version {p.version}</span></div>
              <p class="small snip">{p.text}</p>
            </article>
          {/each}{/if}
      </div>
    </section>
  {/if}

  <State {loading} {error} retry={load} />
  {#if !loading && !error}
    {#if !data.folders.length && !data.files.length}
      <div class="panel"><div class="panel-b"><p><strong>{trash ? 'The trash is empty.' : 'This folder is empty.'}</strong></p>
        {#if !trash}<p class="muted">{writable ? 'Upload files, or drop them anywhere on this page. Documents are read in the background so they can be searched and used by agents.' : 'Nothing has been shared here yet.'}</p>{/if}</div></div>
    {:else}
      <div class="tablewrap" use:scrollable><table class="t">
        <caption class="sr-only">{trash ? 'Deleted files' : 'Folders and files'}</caption>
        <thead><tr><th>Name</th><th>Size</th><th>Updated</th><th>Search</th><th></th></tr></thead>
        <tbody>
          {#each data.folders as f (f.id)}
            <tr>
              <td><button class="nm linkish" onclick={() => open(f.id)}><Icon name="folder" size={16} /> {f.name}</button>{#if f.restricted} <span class="chip warn" title="Only people with an explicit access rule can open this"><Icon name="lock" size={12} /> Restricted</span>{/if}</td>
              <td class="muted">Folder</td><td class="muted">{ago(f.created_at)}</td><td></td>
              <td class="act">
                {#if writable}
                  <Menu label="Actions for folder {f.name}">{#snippet children(close)}
                    <button role="menuitem" onclick={() => { close(); ask('rename', f, true); }}>Rename</button>
                    <button role="menuitem" onclick={() => { close(); ask('move', f, true); }}>Move to…</button>
                    {#if can('ws.members.manage')}<button role="menuitem" onclick={() => { close(); restrict(f, true); }}>{f.restricted ? 'Remove restriction' : 'Restrict access'}</button>{/if}
                  {/snippet}</Menu>
                {/if}
              </td>
            </tr>
          {/each}
          {#each data.files as f (f.id)}
            {@const ix = indexChip(f.index)}
            <tr>
              <td><button class="nm linkish" onclick={() => preview(f)}><Icon name={fileIcon(f.kind)} size={16} /> {f.name}</button>
                {#if f.restricted} <span class="chip warn" title="Only people with an explicit access rule can open this"><Icon name="lock" size={12} /> Restricted</span>{/if}
                {#if f.version > 1} <span class="chip" title="Earlier versions are kept">v{f.version}</span>{/if}</td>
              <td class="num">{bytes(f.size)}</td><td class="muted">{ago(trash ? f.deleted_at : f.updated_at)}</td>
              <td><span class="chip {ix[0]}" title={f.index === 'unsupported' ? 'Text cannot be read from this kind of file in this release' : ''}>{ix[1]}</span></td>
              <td class="act">
                {#if trash}
                  {#if can('file.restore')}<button class="btn sm" onclick={() => restoreFile(f)}>Restore</button>{/if}
                {:else}
                  <Menu label="Actions for {f.name}">{#snippet children(close)}
                    <button role="menuitem" onclick={() => { close(); preview(f); }}>Open</button>
                    {#if can('file.download')}<a role="menuitem" href={url(f, undefined, true)} download onclick={() => close()}>Download</a>{/if}
                    {#if writable && textual(f)}<button role="menuitem" onclick={() => { close(); edit(f); }}>Edit text</button>{/if}
                    {#if can('file.restore') || f.version > 1}<button role="menuitem" onclick={() => { close(); versions(f); }}>Version history</button>{/if}
                    {#if writable}<button role="menuitem" onclick={() => { close(); ask('rename', f); }}>Rename</button><button role="menuitem" onclick={() => { close(); ask('move', f); }}>Move to…</button>{/if}
                    {#if can('ws.members.manage') && !cur.ws?.archived}<button role="menuitem" onclick={() => { close(); restrict(f, false); }}>{f.restricted ? 'Remove restriction' : 'Restrict access'}</button>{/if}
                    {#if can('file.delete') && !cur.ws?.archived}<button role="menuitem" class="dng" onclick={() => { close(); ask('trash', f); }}>Move to trash</button>{/if}
                  {/snippet}</Menu>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table></div>
    {/if}
  {/if}
</div>

<Modal bind:open={dlg} title={mode === 'folder' ? 'New folder' : mode === 'doc' ? 'New document' : mode === 'rename' ? 'Rename' : mode === 'move' ? `Move “${target?.name ?? ''}”` : mode === 'trash' ? 'Move to trash' : mode === 'edit' ? `Edit ${target?.name ?? ''}` : mode === 'versions' ? 'Version history' : (target?.name ?? 'File')} wide={['doc', 'edit', 'versions', 'preview'].includes(mode)}>
  {#if mode === 'folder' || mode === 'rename'}
    <form id="mf" onsubmit={(e) => { e.preventDefault(); (mode === 'folder' ? mkFolder : rename)(); }}><div class="field"><label for="nm">Name</label><input id="nm" class="input" bind:value={name} maxlength="200" required /></div></form>
  {:else if mode === 'doc'}
    <form id="mf" onsubmit={(e) => { e.preventDefault(); mkDoc(); }}>
      <div class="field"><label for="dn">Name</label><input id="dn" class="input" bind:value={name} placeholder="Meeting notes.md" maxlength="200" required /><span class="hint">Without an extension it is saved as Markdown (.md).</span></div>
      <div class="field"><label for="dt">Content</label><textarea id="dt" class="input mono" rows="12" bind:value={text}></textarea></div>
    </form>
  {:else if mode === 'move'}
    <p class="muted small">Choose the folder to move it into.</p>
    <FolderPicker {wid} bind:value={dest} exclude={isFolder ? target?.id : ''} />
  {:else if mode === 'trash'}
    <p><strong>{target?.name}</strong> will be moved to the trash. Its history is kept, and it can be restored until the trash is emptied by the organisation's retention rule.</p>
  {:else if mode === 'edit'}
    <form id="mf" onsubmit={(e) => { e.preventDefault(); saveEdit(); }}><div class="field"><label for="et" class="sr-only">Text</label><textarea id="et" class="input mono" rows="18" bind:value={text}></textarea>
      <span class="hint">Saving adds a new version; the previous one stays in the history.</span></div></form>
  {:else if mode === 'versions'}
    {#if !info}<p class="muted" role="status">Loading…</p>
    {:else}<div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">Versions of {target?.name}</caption>
      <thead><tr><th>Version</th><th>When</th><th>Source</th><th>Size</th><th></th></tr></thead>
      <tbody>{#each info.versions as v}
        <tr><td>v{v.n}{#if v.n === info.version} <span class="chip ok">Current</span>{/if}</td><td class="muted">{ago(v.created_at)}</td><td>{v.source}{#if v.note} <span class="muted tiny">· {v.note}</span>{/if}</td><td class="num">{bytes(v.size)}</td>
          <td class="act">{#if can('file.download')}<a class="btn sm ghost" href={url(target, v.n, true)} download>Download</a>{/if}{#if v.n !== info.version && can('file.restore')}<button class="btn sm" disabled={busy} onclick={() => restoreVersion(v.n)}>Restore</button>{/if}</td></tr>
      {/each}</tbody></table></div>{/if}
  {:else if mode === 'preview'}
    {#if bodyErr}<div class="notice bad" role="alert"><div>{bodyErr}</div></div>{/if}
    {#if info}
      <dl class="kv" style="margin-bottom:12px">
        <dt>Version</dt><dd>v{info.version}</dd><dt>Size</dt><dd>{bytes(info.size)}</dd><dt>Updated</dt><dd>{ago(info.updated_at)}</dd>
        <dt>Search</dt><dd>{info.text ? (info.text.status === 'ok' ? `readable (${info.text.chars.toLocaleString()} characters, ${info.text.extractor})` : info.text.error || info.text.status) : 'not indexed yet'}</dd>
        {#if info.restricted}<dt>Access</dt><dd>Restricted</dd>{/if}
      </dl>
      {#if imageish(info)}<img src={url(info)} alt={info.name} class="pv" />
      {:else if info.mime === 'video/mp4' || info.mime === 'video/webm'}<video src={url(info)} controls class="pv"><track kind="captions" /></video>
      {:else if info.mime?.startsWith('audio/')}<audio src={url(info)} controls style="width:100%"></audio>
      {:else if textual(info) && body}<pre class="txt">{body}</pre>
      {:else}<p class="muted">A preview is not available for this kind of file{can('file.download') ? '. Download it to open it.' : '.'}</p>{/if}
    {:else if !bodyErr}<p class="muted" role="status">Loading…</p>{/if}
  {/if}
  {#if formErr}<div class="err" role="alert" style="margin-top:8px">{formErr}</div>{/if}
  {#snippet footer()}
    {#if mode === 'preview'}
      {#if info && can('file.download')}<a class="btn" href={url(info, undefined, true)} download><Icon name="download" size={16} /> Download</a>{/if}<button class="btn" onclick={() => (dlg = false)}>Close</button>
    {:else if mode === 'versions'}<button class="btn" onclick={() => (dlg = false)}>Close</button>
    {:else if mode === 'trash'}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn danger solid" disabled={busy} onclick={toTrash}>Move to trash</button>
    {:else if mode === 'move'}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn primary" disabled={busy} onclick={move}>Move here</button>
    {:else if mode}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn primary" form="mf" type="submit" disabled={busy || (mode !== 'edit' && !name.trim())}>{mode === 'folder' ? 'Create folder' : mode === 'doc' ? 'Create document' : mode === 'edit' ? 'Save' : 'Rename'}</button>{/if}
  {/snippet}
</Modal>

<style>
  .files { position: relative; }
  .files.drag { outline: 2px dashed var(--accent); outline-offset: 6px; border-radius: var(--radius); }
  .drop { position: absolute; inset: 0; z-index: 5; display: grid; place-items: center; background: color-mix(in srgb, var(--bg) 82%, transparent); font-weight: 600; pointer-events: none; }
  .bar { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-bottom: 12px; }
  .crumbs { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; font-weight: 500; }
  .search { display: flex; gap: 8px; } .search .input { width: min(320px, 60vw); }
  .tools { margin-bottom: 12px; }
  .nm { display: inline-flex; align-items: center; gap: 8px; color: var(--fg); text-decoration: none; font-weight: 500; }
  .nm:hover { text-decoration: underline; }
  td.act { text-align: right; width: 1%; }
  .ups { list-style: none; padding: 0; margin: 0 0 12px; display: grid; gap: 4px; font-size: 13px; }
  .plain { list-style: none; padding: 0; margin: 0; display: grid; gap: 4px; }
  .results { margin-bottom: 16px; } .hit { padding: 8px 0; border-top: 1px solid var(--border); } .hit:first-of-type { border-top: 0; }
  .snip { margin: 4px 0 0; white-space: pre-wrap; max-height: 9em; overflow: auto; color: var(--fg-2); }
  .pv { max-width: 100%; max-height: 60vh; display: block; border-radius: var(--radius-sm); border: 1px solid var(--border); }
  .txt { max-height: 55vh; overflow: auto; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 12px; font: 13px/1.55 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; margin: 0; }
</style>
