<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { get, qs } from '$lib/api';
  import State from './State.svelte';
  import SettingRow from './SettingRow.svelte';
  import { errMsg } from './util';
  let { categories = [], keys = [], q: q0 = '', searchable = true, skip = [] }: { categories?: string[]; keys?: string[]; q?: string; searchable?: boolean; skip?: string[] } = $props();
  let all = $state<any[]>([]); let cats = $state<string[]>([]); let loading = $state(true); let error = $state('');
  let q = $state(untrack(() => q0)); let cat = $state(''); let search: HTMLInputElement | undefined = $state();
  async function load() { try { const r = await get('/api/admin/settings' + qs({ q: q || undefined })); all = r.settings; cats = r.categories; error = ''; } catch (e) { error = errMsg(e); } loading = false; }
  onMount(() => { load(); });
  let t: any; const typing = () => { clearTimeout(t); t = setTimeout(load, 200); };
  const shown = $derived(all.filter((s) => !skip.includes(s.id) && (!categories.length || categories.includes(s.category)) && (!keys.length || keys.includes(s.id)) && (!cat || s.category === cat)));
  const groups = $derived([...new Set(shown.map((s) => s.category))]);
  function key(e: KeyboardEvent) { const el = e.target as HTMLElement; if (e.key === '/' && !['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)) { e.preventDefault(); search?.focus(); } }
</script>
<svelte:window onkeydown={key} />
{#if searchable}
  <div class="row" style="margin-bottom:12px">
    <div class="grow" style="max-width:420px"><label class="sr-only" for="sq">Search settings</label><input id="sq" class="input" type="search" placeholder="Search settings (press /)" bind:value={q} oninput={typing} bind:this={search} /></div>
    {#if !categories.length}<label class="sr-only" for="scat">Category</label><select id="scat" class="input" style="max-width:240px" bind:value={cat}><option value="">All categories</option>{#each cats as c}<option>{c}</option>{/each}</select>{/if}
  </div>
{/if}
<State {loading} {error} retry={load} empty={!loading && !error && !shown.length ? (q ? `No settings match “${q}”.` : 'No settings in this area.') : ''} />
{#each groups as g}
  <section aria-labelledby={'g-' + g.replace(/\W/g, '')}>
    {#if !categories.length || categories.length > 1}<h2 id={'g-' + g.replace(/\W/g, '')} style="margin:24px 0 0;font-size:17px">{g}</h2>{/if}
    {#each shown.filter((s) => s.category === g) as s (s.id)}<SettingRow {s} onsaved={load} />{/each}
  </section>
{/each}
