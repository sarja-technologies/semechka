<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, del } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import { errMsg, fmtTime } from '$lib/components/admin/util';
  import { loadRoles, loadTeams, type Opt } from '$lib/components/admin/lookup';
  let rows = $state<any[]>([]); let loading = $state(true); let error = $state(''); let mode = $state('');
  let roles = $state<Opt[]>([]); let teams = $state<Opt[]>([]);
  let open = $state(false); let f = $state({ email: '', role_id: 'member', team_id: '', max_uses: 1, expires_hours: '' as any }); let err = $state(''); let link = $state('');
  async function load() { loading = true; try { rows = await get('/api/admin/invites'); error = ''; } catch (e) { error = errMsg(e); } loading = false; }
  onMount(async () => { load(); roles = await loadRoles(); teams = await loadTeams(); try { mode = (await get('/api/public')).signup.open ? '' : 'closed'; } catch { /* ignore */ } });
  async function create() {
    err = '';
    try { const r = await post('/api/admin/invites', { email: f.email || null, role_id: f.role_id, team_id: f.team_id || null, max_uses: Number(f.max_uses), expires_hours: f.expires_hours ? Number(f.expires_hours) : null }); link = location.origin + r.path; load(); }
    catch (e) { err = errMsg(e); }
  }
  async function revoke(id: string) { try { await del(`/api/admin/invites/${id}`); toast('Invitation revoked.'); load(); } catch (e) { toast(errMsg(e), 'bad'); } }
  const chip = (s: string) => (s === 'active' ? 'ok' : s === 'used' ? '' : 'warn');
</script>
<Page title="Invites & Enrolment" lead="Expiring, limited-use invitations. The link is shown once when created and cannot be recovered; revoke and recreate if it is lost. Invitations only work when registration mode is “invite”; a person can only be invited to roles you could assign yourself.">
  {#snippet actions()}<button class="btn primary" onclick={() => { open = true; link = ''; err = ''; }}>Create invitation</button>{/snippet}
  <State {loading} {error} retry={load} empty={!loading && !error && !rows.length ? 'No invitations yet. Create one to onboard people in invite mode.' : ''} />
  {#if rows.length}<div class="tablewrap"><table class="t"><caption class="sr-only">Invitations</caption><thead><tr><th>For</th><th>Role</th><th>Uses</th><th>Expires</th><th>State</th><th></th></tr></thead><tbody>
    {#each rows as i (i.id)}<tr><td>{i.email ?? 'Anyone with the link'}</td><td class="mono">{i.role_id}</td><td class="num">{i.uses}/{i.max_uses}</td><td>{fmtTime(i.expires_at)}</td><td><span class="chip {chip(i.state)}">{i.state}</span></td><td>{#if i.state === 'active'}<button class="btn sm danger" onclick={() => revoke(i.id)}>Revoke</button>{/if}</td></tr>{/each}</tbody></table></div>{/if}
</Page>
<Modal bind:open title={link ? 'Invitation created' : 'Create invitation'}>
  {#if link}
    <div class="notice warn"><div>Copy this link now. It is shown only once.</div></div>
    <input class="input mono" readonly value={link} aria-label="Invitation link" onfocus={(e) => (e.target as HTMLInputElement).select()} />
    <p class="hint">Registration mode must be “invite” for the link to work (Registration & Sign-in).</p>
  {:else}
    <div class="field"><label for="ie">Restrict to an email (optional)</label><input id="ie" class="input" type="email" bind:value={f.email} /></div>
    <div class="field"><label for="ir">Role</label><select id="ir" class="input" bind:value={f.role_id}>{#each roles as r}<option value={r.id}>{r.label}</option>{/each}</select></div>
    <div class="field"><label for="it">Team (optional)</label><select id="it" class="input" bind:value={f.team_id}><option value="">None</option>{#each teams as r}<option value={r.id}>{r.label}</option>{/each}</select></div>
    <div class="row"><div class="field"><label for="iu">Maximum uses</label><input id="iu" class="input" type="number" min="1" bind:value={f.max_uses} /></div><div class="field"><label for="ix">Lifetime in hours (blank = default)</label><input id="ix" class="input" type="number" min="1" bind:value={f.expires_hours} /></div></div>
    {#if err}<p class="err" role="alert">{err}</p>{/if}
  {/if}
  {#snippet footer()}{#if link}<button class="btn primary" onclick={() => (open = false)}>Done</button>{:else}<button class="btn" onclick={() => (open = false)}>Cancel</button><button class="btn primary" onclick={create}>Create</button>{/if}{/snippet}
</Modal>
