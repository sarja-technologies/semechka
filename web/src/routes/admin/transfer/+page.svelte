<script lang="ts">
  import { get, post } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import { errMsg } from '$lib/components/admin/util';
  let plan = $state<any>(null); let err = $state(''); let profile = $state<any>(null); let name = $state(''); let confirm = $state(false); let busy = $state(false);
  async function exp() { err = ''; try { const p = await get('/api/admin/settings-export'); const a = document.createElement('a'); a.href = URL.createObjectURL(new Blob([JSON.stringify(p, null, 2)], { type: 'application/json' })); a.download = 'semechka-settings.json'; a.click(); URL.revokeObjectURL(a.href); toast(`Exported ${p.settings.length} settings (no secrets, no role/team ids).`); } catch (e) { err = errMsg(e); } }
  async function pick(e: Event) {
    err = ''; plan = null; const f = (e.target as HTMLInputElement).files?.[0]; if (!f) return; name = f.name;
    try { profile = JSON.parse(await f.text()); plan = await post('/api/admin/settings-import', { profile, dry_run: true }); } catch (x) { err = x instanceof SyntaxError ? 'That file is not valid JSON.' : errMsg(x); profile = null; }
  }
  async function apply() { busy = true; err = ''; try { const r = await post('/api/admin/settings-import', { profile, dry_run: false, confirm_weaker: confirm }); plan = r; if (r.applied) toast(`Applied ${r.plan.length} settings.`); else err = 'Not applied. See the problems listed below.'; } catch (x) { err = errMsg(x); } busy = false; }
  const sv = (x: any) => (x === null || x === undefined ? '—' : typeof x === 'object' ? JSON.stringify(x) : String(x));
</script>
<Page title="Import / Export" lead="Move configuration between deployments. Exports contain deployment-level values only: no secrets, no accounts, and no role or team ids. Imports are checked first; nothing changes until you apply.">
  <div class="row" style="margin-bottom:24px"><button class="btn primary" onclick={exp}>Export settings</button>
    <label class="btn">Choose a file to import…<input type="file" accept="application/json,.json" class="sr-only" onchange={pick} /></label>{#if name}<span class="mono small">{name}</span>{/if}</div>
  {#if err}<p class="err" role="alert">{err}</p>{/if}
  {#if plan}
    <h2 style="font-size:17px">{plan.applied ? 'Applied' : 'Preview of changes'}</h2>
    {#if plan.errors.length}<div class="notice bad" role="alert"><div><strong>{plan.errors.length} problems. Nothing was changed.</strong><ul style="margin:6px 0 0;padding-left:18px">{#each plan.errors as e}<li><code>{e.key}</code>: {e.error}</li>{/each}</ul></div></div>{/if}
    {#if plan.plan.length}<div class="tablewrap"><table class="t"><caption class="sr-only">Settings the file would change</caption><thead><tr><th>Setting</th><th>Current</th><th>Imported</th><th>Effect</th></tr></thead><tbody>
      {#each plan.plan as p}<tr><td class="mono tiny">{p.key}</td><td class="mono tiny">{sv(p.old)}</td><td class="mono tiny">{sv(p.new)}</td><td>{#if p.old === p.new}<span class="chip">no change</span>{:else if p.weakens_protection}<span class="chip warn">weakens protection</span>{:else}<span class="chip info">changes</span>{/if}</td></tr>{/each}</tbody></table></div>
      {#if !plan.applied}<div class="row" style="margin-top:12px">{#if plan.plan.some((p: any) => p.weakens_protection)}<label class="row" style="gap:8px"><input type="checkbox" bind:checked={confirm} /> I confirm the changes marked “weakens protection”.</label>{/if}<button class="btn primary" disabled={busy || plan.errors.length > 0} onclick={apply}>Apply {plan.plan.length} settings</button></div>{/if}{/if}
  {/if}
</Page>
