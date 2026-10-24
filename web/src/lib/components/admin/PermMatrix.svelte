<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from '$lib/api';
  import { errMsg } from './util';
  // Edits two lists of permission rules. Exact permission ids are set per row; wildcard patterns (e.g. tech.*) are shown and removable separately.
  let { grants = $bindable([]), denies = $bindable([]), who = 'this person' }: { grants: string[]; denies: string[]; who?: string } = $props();
  let reg = $state<any[]>([]); let presets = $state<Record<string, string[]>>({}); let err = $state(''); let newPat = $state('');
  onMount(async () => { try { reg = await get('/api/admin/permissions'); presets = (await get('/api/admin/tech-presets')).presets; } catch (e) { err = errMsg(e); } });
  const groups = $derived([...new Set(reg.map((p) => p.group))]);
  const covers = (pat: string, id: string) => pat === '*' ? !reg.find((p) => p.id === id)?.sensitive : pat.endsWith('.*') ? id.startsWith(pat.slice(0, -1)) : pat === id;
  function ruleState(id: string) { return denies.includes(id) ? 'deny' : grants.includes(id) ? 'grant' : 'inherit'; }
  function via(id: string, list: string[]) { return list.find((p) => p !== id && p.includes('*') && covers(p, id)); }
  function set(id: string, v: string) { grants = grants.filter((x) => x !== id); denies = denies.filter((x) => x !== id); if (v === 'grant') grants = [...grants, id]; if (v === 'deny') denies = [...denies, id]; }
  function preset(name: string) {
    const fields = presets[name] ?? [];
    grants = [...grants.filter((g) => !g.startsWith('tech.')), ...fields];
  }
  const patterns = (l: string[]) => l.filter((p) => p.includes('*'));
  function addPat(kind: 'grant' | 'deny') { const p = newPat.trim(); if (!p) return; if (kind === 'grant') grants = [...new Set([...grants, p])]; else denies = [...new Set([...denies, p])]; newPat = ''; }
</script>
{#if err}<p class="err">{err}</p>{/if}
{#if patterns(grants).length || patterns(denies).length}
  <div class="row" style="margin-bottom:8px"><span class="label">Pattern rules</span>
    {#each patterns(grants) as p}<span class="chip ok">grant {p} <button class="x" aria-label="Remove grant {p}" onclick={() => (grants = grants.filter((g) => g !== p))}>×</button></span>{/each}
    {#each patterns(denies) as p}<span class="chip bad">deny {p} <button class="x" aria-label="Remove deny {p}" onclick={() => (denies = denies.filter((g) => g !== p))}>×</button></span>{/each}</div>
{/if}
<div class="row" style="margin-bottom:12px"><label class="sr-only" for="np">Add pattern rule</label><input id="np" class="input mono" style="max-width:200px" placeholder="e.g. tech.perf.*" bind:value={newPat} />
  <button class="btn sm" onclick={() => addPat('grant')}>Add as grant</button><button class="btn sm" onclick={() => addPat('deny')}>Add as deny</button>
  <span class="hint">A deny always overrides a grant.</span></div>
{#each groups as g}
  <fieldset class="grp"><legend>{g}</legend>
    {#if g === 'Technical visibility'}
      <div class="notice info" style="margin-bottom:8px"><div>Reading technical information is separate from operating infrastructure (Operations) and from reading content. Granting these never lets {who} change anything.<div class="row" style="margin-top:8px"><span class="label">Apply preset:</span>{#each Object.keys(presets) as n}<button class="btn sm" onclick={() => preset(n)}>{n}</button>{/each}</div></div></div>
    {/if}
    <div style="overflow-x:auto"><table class="t"><caption class="sr-only">{g} permissions</caption><thead><tr><th>Permission</th><th>Inherit</th><th>Grant</th><th>Deny</th></tr></thead><tbody>
      {#each reg.filter((p) => p.group === g) as p (p.id)}
        {@const st = ruleState(p.id)}
        <tr><td class="wrap"><div>{p.label}</div><div class="mono tiny muted">{p.id}{#if st === 'inherit' && via(p.id, denies)} · denied via {via(p.id, denies)}{:else if st === 'inherit' && via(p.id, grants)} · granted via {via(p.id, grants)}{/if}</div></td>
          {#each ['inherit', 'grant', 'deny'] as v}<td><input type="radio" name={'p-' + p.id} value={v} checked={st === v} aria-label="{v} {p.label}" onchange={() => set(p.id, v)} /></td>{/each}</tr>
      {/each}</tbody></table></div>
  </fieldset>
{/each}
<style>.grp { min-width: 0; border: 1px solid var(--border); border-radius: var(--radius); margin: 0 0 12px; padding: 8px 12px; background: var(--surface); } legend { font-weight: 600; padding: 0 6px; } .x { all: unset; cursor: pointer; padding: 0 4px; } .x:focus-visible { outline: 2px solid var(--accent); } .grp :global(table.t) { border: 0; }</style>
