<script lang="ts">
  // Choose pictures and videos already in the Library (uploaded, edited or generated) to attach.
  import { tick, untrack } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from './MediaThumb.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import { fmtSize, listAssets, type Asset } from '$lib/media/assets';

  let { open = $bindable(false), onpick, accept = 'any', title = 'Add from your Library' }:
    { open: boolean; onpick: (assets: Asset[]) => void; accept?: 'any' | 'image' | 'video'; title?: string } = $props();

  type F = { id: string; label: string; kind?: string; origin?: string };
  const FILTERS: F[] = [{ id: 'all', label: 'All' }, { id: 'video', label: 'Videos', kind: 'video' }, { id: 'image', label: 'Pictures', kind: 'image' },
    { id: 'upload', label: 'Uploaded', origin: 'upload' }, { id: 'edit', label: 'Edited', origin: 'edit' }, { id: 'generated', label: 'Generated', origin: 'generated' }];
  let filter = $state('all'), items = $state<Asset[]>([]), loading = $state(false), more = $state(true), error = $state(''), chosen = $state<Asset[]>([]), dlg: HTMLDivElement | undefined = $state();
  let opener: Element | null = null;
  const f = $derived(FILTERS.find((x) => x.id === filter)!);
  const shown = $derived(accept === 'any' ? items : items.filter((a) => a.kind === accept));

  async function load(reset: boolean) {
    if (loading) return;
    loading = true; error = '';
    try {
      const before = reset ? undefined : items[items.length - 1]?.created_at;
      const got = await listAssets({ kind: accept !== 'any' ? accept : f.kind, origin: f.origin, before, limit: 40 });
      items = reset ? got : [...items, ...got]; more = got.length >= 40;
    } catch (e) { error = errText(e); } finally { loading = false; }
  }
  $effect(() => {
    if (open) { untrack(() => { opener = document.activeElement; chosen = []; load(true); }); tick().then(() => dlg?.focus()); }
    else if (opener instanceof HTMLElement) { opener.focus(); opener = null; }
  });
  function setFilter(id: string) { filter = id; items = []; more = true; load(true); }
  const has = (a: Asset) => chosen.some((c) => c.id === a.id);
  function toggle(a: Asset) { chosen = has(a) ? chosen.filter((c) => c.id !== a.id) : [...chosen, a]; }
  function done() { const c = chosen; open = false; if (c.length) onpick(c); }
  function key(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.stopPropagation(); open = false; }
    if (e.key !== 'Tab' || !dlg) return;
    const els = [...dlg.querySelectorAll<HTMLElement>('button:not(:disabled), [tabindex="0"]')];
    if (!els.length) return;
    const a = els[0], z = els[els.length - 1];
    if (e.shiftKey && (document.activeElement === a || document.activeElement === dlg)) { e.preventDefault(); z.focus(); } else if (!e.shiftKey && document.activeElement === z) { e.preventDefault(); a.focus(); }
  }
</script>

{#if open}
  <div class="scrim" role="presentation" onclick={() => (open = false)}></div>
  <div class="pk" role="dialog" aria-modal="true" aria-label={title} tabindex="-1" bind:this={dlg} onkeydown={key}>
    <header><h3>{title}</h3><button type="button" class="x" aria-label="Close" onclick={() => (open = false)}><Icon name="x" size={16} /></button></header>
    <div class="fl" role="radiogroup" aria-label="Show">
      {#each FILTERS as x (x.id)}
        {#if !(accept !== 'any' && x.kind && x.kind !== accept)}<button type="button" role="radio" aria-checked={filter === x.id} class:on={filter === x.id} onclick={() => setFilter(x.id)}>{x.label}</button>{/if}
      {/each}
    </div>
    <div class="body">
      {#if error}<p class="err" role="alert">{error}</p>
      {:else if !shown.length && !loading}<p class="empty">Nothing here yet. Drop a file on the message box to add one.</p>
      {:else}
        <ul class="grid">
          {#each shown as a (a.id)}
            <li>
              <button type="button" class="it" class:on={has(a)} aria-pressed={has(a)} onclick={() => toggle(a)} ondblclick={() => { chosen = [a]; done(); }} title={a.name ?? ''}>
                <span class="t"><MediaThumb id={a.id} kind={a.kind} name={a.name} duration={a.duration} ratio="4 / 3" /></span>
                <span class="ck" aria-hidden="true">{#if has(a)}<Icon name="check" size={12} />{/if}</span>
                <span class="n">{a.name ?? a.prompt ?? (a.kind === 'video' ? 'Video' : 'Picture')}</span>
                <span class="s">{[a.width && a.height ? `${a.width}×${a.height}` : '', fmtSize(a.size)].filter(Boolean).join(' · ')}</span>
              </button>
            </li>
          {/each}
        </ul>
        {#if loading}<p class="empty" role="status">Loading…</p>{:else if more && items.length}<button type="button" class="more" onclick={() => load(false)}>Show more</button>{/if}
      {/if}
    </div>
    <footer><span class="tiny muted">{chosen.length ? `${chosen.length} selected` : 'Select one or more'}</span>
      <button type="button" class="b" onclick={() => (open = false)}>Cancel</button><button type="button" class="b pri" disabled={!chosen.length} onclick={done}>Attach{chosen.length > 1 ? ` ${chosen.length}` : ''}</button></footer>
  </div>
{/if}

<style>
  .scrim { position: fixed; inset: 0; z-index: 90; background: rgba(10, 12, 12, 0.5); animation: fi var(--medium) var(--ease); }
  .pk { position: fixed; z-index: 91; left: 50%; top: 50%; transform: translate(-50%, -50%); width: min(780px, calc(100vw - 24px)); max-height: min(640px, calc(100vh - 24px)); display: flex; flex-direction: column;
    background: var(--surface); border: 1px solid var(--border-strong); border-radius: 14px; box-shadow: var(--shadow); outline: none; animation: pop var(--medium) var(--ease); }
  @keyframes fi { from { opacity: 0; } } @keyframes pop { from { opacity: 0; transform: translate(-50%, -48%) scale(0.985); } }
  @media (prefers-reduced-motion: reduce) { .scrim, .pk { animation: none; } }
  header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px 6px; } h3 { font-size: 15px; margin: 0; }
  .x { all: unset; width: 28px; height: 28px; display: grid; place-items: center; border-radius: 8px; cursor: pointer; color: var(--fg-muted); } .x:hover { background: var(--hover); color: var(--fg); } .x:focus-visible { outline: 2px solid var(--m-ring); }
  .fl { display: flex; gap: 4px; padding: 4px 16px 10px; flex-wrap: wrap; }
  .fl button { all: unset; box-sizing: border-box; height: 28px; padding: 0 12px; border-radius: 999px; cursor: pointer; font-size: 12.5px; color: var(--fg-muted); border: 1px solid var(--border); }
  .fl button:hover { background: var(--hover); color: var(--fg); } .fl button.on { background: var(--selected); color: var(--fg); border-color: var(--border-strong); font-weight: 600; } .fl button:focus-visible { outline: 2px solid var(--m-ring); }
  .body { padding: 0 16px 8px; overflow: auto; min-height: 160px; }
  .grid { list-style: none; margin: 0; padding: 4px 0; display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(124px, 1fr)); }
  .it { all: unset; box-sizing: border-box; position: relative; display: grid; gap: 2px; width: 100%; cursor: pointer; border-radius: 10px; padding: 4px; transition: background var(--fast) var(--ease); }
  .it:hover { background: var(--hover); } .it:focus-visible { outline: 2px solid var(--m-ring); } .it.on { background: var(--m-ring-soft); }
  .t { display: block; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
  .ck { position: absolute; left: 10px; top: 10px; width: 18px; height: 18px; border-radius: 50%; border: 1.5px solid rgba(255,255,255,.9); background: rgba(14,15,16,.4); display: grid; place-items: center; color: #fff; transition: background var(--fast) var(--ease); }
  .it.on .ck { background: var(--m-ring); border-color: var(--m-ring); }
  .n { font-size: 12px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg); } .s { font-size: 11px; color: var(--fg-muted); }
  .empty, .err { margin: 28px 0; text-align: center; color: var(--fg-muted); } .err { color: var(--danger); }
  .more { all: unset; display: block; margin: 8px auto 4px; padding: 6px 14px; border-radius: 8px; cursor: pointer; color: var(--fg-muted); } .more:hover { background: var(--hover); color: var(--fg); }
  footer { display: flex; align-items: center; gap: 8px; padding: 10px 16px 14px; border-top: 1px solid var(--border); } footer .tiny { margin-right: auto; }
  .b { all: unset; box-sizing: border-box; height: 34px; padding: 0 16px; border-radius: 9px; cursor: pointer; display: inline-flex; align-items: center; border: 1px solid var(--border-strong); font-weight: 500; }
  .b:hover:not(:disabled) { background: var(--hover); } .b.pri { background: var(--btn-primary-bg); color: var(--btn-primary-fg); border-color: transparent; } .b:disabled { opacity: .4; cursor: default; } .b:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
</style>
