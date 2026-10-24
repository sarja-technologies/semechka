<script lang="ts">
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { get, patch, put, post, del, qs } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { app, can } from '$lib/session.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import PermMatrix from '$lib/components/admin/PermMatrix.svelte';
  import { errMsg, fmtTime } from '$lib/components/admin/util';
  import { loadRoles, loadTeams, type Opt } from '$lib/components/admin/lookup';
  const id = page.params.id;
  let u = $state<any>(null); let loading = $state(true); let error = $state('');
  let roles = $state<Opt[]>([]); let teams = $state<Opt[]>([]); let models = $state<any[]>([]);
  let name = $state(''); let status = $state('active'); let rsel = $state<string[]>([]); let tsel = $state<string[]>([]);
  let grants = $state<string[]>([]); let denies = $state<string[]>([]); let ovErr = $state(''); let pErr = $state(''); let profErr = $state('');
  let pw = $state(''); let delOpen = $state(false);
  let exPerm = $state('mode.image'); let ex = $state<any>(null); let exModel = $state(''); let exm = $state<any>(null); let exErr = $state('');
  async function load() {
    try {
      u = await get(`/api/admin/users/${id}`); loadSessions();
      name = u.display_name; status = u.status === 'suspended' ? 'suspended' : 'active'; rsel = u.roles.map((x: any) => x.id); tsel = u.teams.map((x: any) => x.id);
      const o = await get(`/api/admin/users/${id}/override`); grants = o.grants; denies = o.denies;
    } catch (e) { error = errMsg(e); }
    loading = false;
  }
  onMount(async () => { load(); roles = await loadRoles(); teams = await loadTeams(); try { models = (await get('/api/admin/models?per_page=200')).items; } catch { /* needs models permission */ } });
  const tog = (arr: string[], v: string) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  async function saveProfile() { profErr = ''; try { const b: any = { display_name: name, role_ids: rsel, team_ids: tsel }; if (u.status === 'active' || u.status === 'suspended') b.status = status; else if (status === 'active') b.status = 'active'; await patch(`/api/admin/users/${id}`, b); toast('Saved.'); load(); } catch (e) { profErr = errMsg(e); } }
  async function approve() { try { await patch(`/api/admin/users/${id}`, { status: 'active' }); toast('Approved.'); load(); } catch (e) { profErr = errMsg(e); } }
  async function setPw() { pErr = ''; try { await post(`/api/admin/users/${id}/password`, { password: pw }); pw = ''; toast('Password set; their other sessions were signed out.'); } catch (e) { pErr = errMsg(e); } }
  async function saveOv() { ovErr = ''; try { await put(`/api/admin/users/${id}/override`, { grants, denies }); toast('Override saved.'); } catch (e) { ovErr = errMsg(e); } }
  // sign-in and security: sessions (never their tokens), ending them, and resetting a lost second factor
  let sessions = $state<any[] | null>(null), mfaOpen = $state(false), mfaReason = $state(''), mfaErr = $state(''), endOpen = $state(false);
  async function loadSessions() { try { sessions = await get(`/api/admin/users/${id}/sessions`); } catch { sessions = null; } }
  async function resetMfa() {
    mfaErr = '';
    try { const r = await post(`/api/admin/users/${id}/mfa-reset`, { reason: mfaReason }); mfaOpen = false; mfaReason = ''; toast(`Two-step sign-in removed; ${r.sessions_ended} session${r.sessions_ended === 1 ? '' : 's'} ended.`); load(); }
    catch (e) { mfaErr = errMsg(e); }
  }
  async function endSessions() { endOpen = false; try { const r = await post(`/api/admin/users/${id}/sessions/end`); toast(`${r.sessions_ended} session${r.sessions_ended === 1 ? '' : 's'} ended.`); loadSessions(); } catch (e) { toast(errMsg(e), 'bad'); } }
  async function remove() { try { await del(`/api/admin/users/${id}`); toast('Account deleted.'); goto('/admin/people'); } catch (e) { profErr = errMsg(e); delOpen = false; } }
  async function explain() { exErr = ''; ex = null; try { ex = await get('/api/admin/explain/permission' + qs({ user_id: id, permission: exPerm })); } catch (e) { exErr = errMsg(e); } }
  async function explainModel() { exErr = ''; exm = null; if (!exModel) return; try { exm = await get('/api/admin/explain/model' + qs({ user_id: id, model_id: exModel })); } catch (e) { exErr = errMsg(e); } }
</script>
<Page title={u ? u.display_name : 'Person'} lead={u ? u.email : ''}>
  {#snippet actions()}<a class="btn" href="/admin/people">All people</a>{/snippet}
  <State {loading} {error} retry={load} />
  {#if u}
  <div class="stack" style="max-width:860px">
    {#if u.status === 'pending_approval'}<div class="notice warn"><div class="grow">Waiting for approval. Approving gives only the default role.</div><button class="btn primary sm" onclick={approve}>Approve</button></div>{/if}
    <section class="panel"><div class="panel-h"><h3>Profile, roles and teams</h3><span class="muted small">Created {fmtTime(u.created_at)} · last sign-in {fmtTime(u.last_login_at)}</span></div><div class="panel-b">
      <div class="field"><label for="dn">Display name</label><input id="dn" class="input" bind:value={name} /></div>
      <div class="field"><label for="st">Account status</label><select id="st" class="input" style="max-width:240px" bind:value={status}>{#if u.status !== 'active' && u.status !== 'suspended'}<option value={u.status} disabled>{u.status.replace('_', ' ')}</option>{/if}<option value="active">Active</option><option value="suspended">Suspended (signs them out)</option></select></div>
      <fieldset class="fs"><legend>Roles</legend>{#each roles as r}<label class="row" style="gap:8px"><input type="checkbox" checked={rsel.includes(r.id)} onchange={() => (rsel = tog(rsel, r.id))} /> {r.label} <span class="mono tiny muted">{r.id}</span></label>{/each}</fieldset>
      <fieldset class="fs"><legend>Teams</legend>{#each teams as r}<label class="row" style="gap:8px"><input type="checkbox" checked={tsel.includes(r.id)} onchange={() => (tsel = tog(tsel, r.id))} /> {r.label}</label>{:else}<span class="muted">No teams yet.</span>{/each}</fieldset>
      {#if profErr}<p class="err" role="alert">{profErr}</p>{/if}
      <button class="btn primary" onclick={saveProfile}>Save</button></div></section>

    <section class="panel"><div class="panel-h"><h3>Permission override for this person</h3></div><div class="panel-b">
      <p class="muted">Direct rules apply on top of roles and teams. A deny anywhere wins over every grant. To show one person a single technical field, grant just that field.</p>
      <PermMatrix bind:grants bind:denies who={u.display_name} />
      {#if ovErr}<p class="err" role="alert">{ovErr}</p>{/if}
      <button class="btn primary" onclick={saveOv}>Save override</button></div></section>

    <section class="panel"><div class="panel-h"><h3>Why can’t {u.display_name} do this?</h3></div><div class="panel-b">
      <div class="row"><label for="ep" class="label">Permission</label><input id="ep" class="input mono" style="max-width:260px" list="permids" bind:value={exPerm} /><datalist id="permids">{#each ['mode.chat', 'mode.image', 'mode.video', 'tech.perf.tps', 'tech.queue.position', 'ops.model.load', 'admin.models.manage', 'chat.ephemeral'] as p}<option value={p}></option>{/each}</datalist><button class="btn" onclick={explain}>Explain</button></div>
      {#if ex}<div class="notice {ex.allowed ? 'ok' : 'bad'}" style="margin-top:12px"><div><strong>{ex.allowed ? 'Allowed' : 'Denied'}.</strong> {ex.reason}</div></div>{/if}
      {#if models.length}<div class="row" style="margin-top:12px"><label for="em" class="label">Model</label><select id="em" class="input" style="max-width:260px" bind:value={exModel} onchange={explainModel}><option value="">Choose a model…</option>{#each models as m}<option value={m.id}>{m.alias}</option>{/each}</select></div>{/if}
      {#if exm}<div class="notice {exm.allowed ? 'ok' : 'bad'}" style="margin-top:12px"><div><strong>{exm.allowed ? 'Allowed' : 'Denied'}.</strong> {exm.reason}<ol style="margin:8px 0 0;padding-left:20px">{#each exm.steps as s}<li>{s.rule}: <strong>{s.result}</strong>{#if s.detail} <span class="muted">— {s.detail}</span>{/if}</li>{/each}</ol></div></div>{/if}
      {#if exErr}<p class="err" role="alert">{exErr}</p>{/if}</div></section>

    <section class="panel"><div class="panel-h"><h3>Sign-in and security</h3><span class="chip {u.mfa ? 'ok' : ''}">Two-step sign-in {u.mfa ? 'on' : 'off'}</span></div><div class="panel-b stack">
      {#if u.mfa && can('admin.users.manage') && u.id !== app.me?.user.id}
        <div class="row"><p class="grow small" style="margin:0">If {u.display_name} lost their authenticator and their recovery codes, remove the second factor so they can sign in with their password and set it up again. Their sessions end, and the reason is recorded.</p><button class="btn" onclick={() => { mfaErr = ''; mfaOpen = true; }}>Reset two-step sign-in…</button></div>
      {/if}
      <div>
        <div class="row"><strong class="grow small">Signed-in devices</strong>{#if sessions?.length && can('admin.users.manage')}<button class="btn sm" onclick={() => (endOpen = true)}>Sign out everywhere</button>{/if}</div>
        {#if sessions === null}<p class="hint">Not available.</p>
        {:else if !sessions.length}<p class="small muted">No active sessions.</p>
        {:else}<div class="tablewrap"><table class="t"><caption class="sr-only">Active sessions of {u.display_name}</caption><thead><tr><th>Browser</th><th>Address</th><th>Last active</th><th>Expires</th></tr></thead><tbody>
          {#each sessions as s (s.id)}<tr><td class="wrap small">{s.ua?.slice(0, 70) || 'Unknown'}</td><td class="mono">{s.ip}</td><td>{fmtTime(s.last_seen)}</td><td>{fmtTime(s.expires_at)}</td></tr>{/each}
        </tbody></table></div>{/if}
      </div>
      {#if can('admin.content.read')}<p class="small" style="margin:0"><a href={`/admin/content?user=${u.id}`}>View this person's conversations</a> <span class="muted">(requires a stated reason; recorded and shown to them)</span></p>{/if}
    </div></section>

    <section class="panel"><div class="panel-h"><h3>Password</h3></div><div class="panel-b"><div class="field"><label for="np">New password</label><input id="np" class="input" type="password" autocomplete="new-password" bind:value={pw} style="max-width:360px" /><span class="hint">Signs the person out everywhere. The policy minimum length applies.</span></div>{#if pErr}<p class="err" role="alert">{pErr}</p>{/if}<button class="btn" disabled={!pw} onclick={setPw}>Set password</button></div></section>
    <section class="panel"><div class="panel-h"><h3>Delete account</h3></div><div class="panel-b"><p>Removes the account and its saved conversations.</p><button class="btn danger" onclick={() => (delOpen = true)}>Delete {u.display_name}…</button></div></section>
  </div>
  <Modal bind:open={mfaOpen} title="Reset two-step sign-in for {u.display_name}?">
    <p class="small">Only do this after confirming who is asking, outside this system (in person, or by a call to a number you already have). Whoever controls their password can then sign in until they set up a new factor.</p>
    {#if mfaErr}<div class="notice bad" role="alert"><div>{mfaErr}</div></div>{/if}
    <div class="field"><label for="mr">Reason (recorded in the audit log)</label><input id="mr" class="input" maxlength="300" bind:value={mfaReason} placeholder="e.g. Phone lost, identity confirmed by phone call" /></div>
    {#snippet footer()}<button class="btn" onclick={() => (mfaOpen = false)}>Cancel</button><button class="btn danger solid" onclick={resetMfa} disabled={mfaReason.trim().length < 3}>Reset</button>{/snippet}
  </Modal>
  <Modal bind:open={endOpen} title="Sign {u.display_name} out everywhere?"><p>Every session ends; they need to sign in again. Your own session is not affected.</p>{#snippet footer()}<button class="btn" onclick={() => (endOpen = false)}>Cancel</button><button class="btn primary" onclick={endSessions}>Sign out everywhere</button>{/snippet}</Modal>
  <Modal bind:open={delOpen} title="Delete {u.display_name}?"><p>This deletes <strong>{u.email}</strong> and everything they saved. It cannot be undone.</p>{#snippet footer()}<button class="btn" onclick={() => (delOpen = false)}>Cancel</button><button class="btn danger solid" onclick={remove}>Delete account</button>{/snippet}</Modal>
  {/if}
</Page>
<style>.fs { min-width: 0; border: 1px solid var(--border); border-radius: var(--radius-sm); margin: 0 0 16px; display: grid; gap: 8px; padding: 12px; } legend { font-weight: 500; font-size: 13px; padding: 0 4px; }</style>
