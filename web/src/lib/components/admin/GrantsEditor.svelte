<script lang="ts">
  // Who may use a model, and for which kinds of turn. The server decides access from exactly these rules:
  // no rule means nobody, a deny beats an allow, and a rule with modes covers only those modes.
  import type { Opt, Rule } from './lookup';
  let { rules = $bindable(), roles, teams, users, modes }: { rules: Rule[]; roles: Opt[]; teams: Opt[]; users: Opt[]; modes: string[] } = $props();

  const MODE_LABEL: Record<string, string> = { chat: 'Chat', code: 'Code', vision: 'Reading images', image: 'Pictures', video: 'Videos' };
  let nt = $state<Rule['subject_type']>('role'), ns = $state(''), ne = $state<'allow' | 'deny'>('allow');
  const subj = (t: string) => (t === 'role' ? roles : t === 'team' ? teams : users);
  const name = (r: Rule) => (r.subject_type === 'everyone' ? 'Everyone signed in' : `${{ role: 'Role', team: 'Team', user: 'Person' }[r.subject_type]}: ${subj(r.subject_type).find((o) => o.id === r.subject_id)?.label ?? r.subject_id}`);
  const has = (t: string, id: string) => rules.some((r) => r.subject_type === t && (t === 'everyone' || r.subject_id === id));
  function add() {
    const id = nt === 'everyone' ? '*' : ns;
    if (!id || has(nt, id)) return;
    rules = [...rules, { subject_type: nt, subject_id: id, effect: ne, modes: null }]; ns = '';
  }
  function toggleMode(i: number, m: string) {
    const r = rules[i]; const cur = r.modes ?? [...modes];
    const next = cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m];
    rules[i] = { ...r, modes: next.length === modes.length ? null : next.length ? next : r.modes };
  }
</script>

<div class="ge">
  <p class="hint">No rule means nobody can use it. A deny always wins. Untick modes to limit a rule, for example a model allowed for Code only.</p>
  {#if rules.length}
    <ul class="rules">
      {#each rules as r, i (r.subject_type + r.subject_id)}
        <li>
          <span class="who">{name(r)}</span>
          <select class="input eff" aria-label={`${name(r)}: rule`} bind:value={r.effect}><option value="allow">Allow</option><option value="deny">Deny</option></select>
          <span class="modes" role="group" aria-label={`${name(r)}: modes`}>
            {#each modes as m (m)}<label class="md"><input type="checkbox" checked={!r.modes || r.modes.includes(m)} onchange={() => toggleMode(i, m)} disabled={!!r.modes && r.modes.length === 1 && r.modes[0] === m} /> {MODE_LABEL[m] ?? m}</label>{/each}
          </span>
          <button type="button" class="btn ghost sm" onclick={() => (rules = rules.filter((_, j) => j !== i))} aria-label={`Remove the rule for ${name(r)}`}>Remove</button>
        </li>
      {/each}
    </ul>
  {:else}<p class="small muted">No rules: nobody can use this model.</p>{/if}
  <div class="row add">
    <label class="sr-only" for="ge-t">Who</label>
    <select id="ge-t" class="input" style="max-width:150px" bind:value={nt} onchange={() => (ns = '')}><option value="everyone">Everyone</option><option value="role">Role</option><option value="team">Team</option><option value="user">Person</option></select>
    {#if nt !== 'everyone'}<label class="sr-only" for="ge-s">Which</label><select id="ge-s" class="input" style="max-width:260px" bind:value={ns}><option value="">Choose…</option>{#each subj(nt) as o (o.id)}<option value={o.id} disabled={has(nt, o.id)}>{o.label}</option>{/each}</select>{/if}
    <label class="sr-only" for="ge-e">Rule</label><select id="ge-e" class="input" style="max-width:100px" bind:value={ne}><option value="allow">Allow</option><option value="deny">Deny</option></select>
    <button type="button" class="btn sm" onclick={add} disabled={nt === 'everyone' ? has('everyone', '*') : !ns}>Add rule</button>
  </div>
</div>

<style>
  .ge { display: grid; gap: 8px; }
  .ge .hint { margin: 0; }
  .rules { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; }
  .rules li { display: grid; grid-template-columns: minmax(140px, 1fr) 96px minmax(0, 2fr) auto; gap: 8px; align-items: center; padding: 6px 0; border-bottom: 1px solid var(--border); }
  .who { font-size: 13px; font-weight: 500; overflow-wrap: anywhere; }
  .eff { min-height: 30px; font-size: 13px; }
  .modes { display: flex; flex-wrap: wrap; gap: 4px 10px; }
  .md { display: inline-flex; gap: 4px; align-items: center; font-size: 12px; white-space: nowrap; }
  .add { flex-wrap: wrap; }
  @media (max-width: 720px) { .rules li { grid-template-columns: 1fr 96px; } .modes { grid-column: 1 / -1; } }
</style>
