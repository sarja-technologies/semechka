<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, put, del } from '$lib/api';
  import { app } from '$lib/session.svelte';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import { errMsg } from '$lib/components/admin/util';
  import { loadRoles, type Opt } from '$lib/components/admin/lookup';
  let rows = $state<any[]>([]); let loading = $state(true); let error = $state('');
  let roles = $state<Opt[]>([]); let open = $state(false); let cur = $state<any>(null); let f = $state<any>({}); let err = $state('');
  let mapText = $state(''); let domainText = $state('');
  let result = $state<Record<string, any>>({}); let delT = $state<any>(null); let delOpen = $state(false);
  let localLogin = $state(true); let policyBusy = $state(false);
  const isOwner = $derived(!!app.me?.roles?.some((r: any) => r.id === 'owner'));
  const blank = () => ({ name: '', issuer: '', client_id: '', client_secret: '', scopes: 'openid email profile', email_claim: 'email', name_claim: 'name', groups_claim: 'groups', default_role: 'member', require_approval: false, jit_create: true, assume_email_verified: false, allow_private_network: false, enabled: false });
  async function load() {
    loading = true;
    try { rows = await get('/api/admin/sso/providers'); localLogin = (await get('/api/admin/settings/sso.local_login/effective').catch(() => ({ value: true }))).value !== false; error = ''; }
    catch (e) { error = errMsg(e); }
    loading = false;
  }
  onMount(async () => { load(); roles = await loadRoles(); });
  function edit(p: any) {
    cur = p; err = '';
    f = p ? { ...p, client_secret: '' } : blank();
    mapText = p ? Object.entries(p.role_map).map(([g, rs]: any) => `${g} = ${rs.join(', ')}`).join('\n') : '';
    domainText = p ? (p.allowed_domains ?? []).join(', ') : '';
    open = true;
  }
  function parseMap(): Record<string, string[]> {
    const out: Record<string, string[]> = {};
    for (const line of mapText.split('\n').map((l) => l.trim()).filter(Boolean)) {
      const i = line.lastIndexOf('=');
      if (i < 1) throw new Error(`“${line}” should look like  group = role_id, other_role_id`);
      out[line.slice(0, i).trim()] = line.slice(i + 1).split(',').map((x) => x.trim()).filter(Boolean);
    }
    return out;
  }
  async function save() {
    err = '';
    try {
      const body = { ...f, client_secret: f.client_secret || null, role_map: parseMap(), allowed_domains: domainText.split(',').map((d) => d.trim()).filter(Boolean) };
      delete body.id; delete body.has_secret; delete body.redirect_uri;
      if (cur) await put(`/api/admin/sso/providers/${cur.id}`, body); else await post('/api/admin/sso/providers', body);
      open = false; toast('Provider saved.'); load();
    } catch (e) { err = errMsg(e); }
  }
  async function test(p: any) {
    result[p.id] = { busy: true };
    try { result[p.id] = await post(`/api/admin/sso/providers/${p.id}/test`); } catch (e) { result[p.id] = { ok: false, error: errMsg(e) }; }
  }
  async function remove() { try { await del(`/api/admin/sso/providers/${delT.id}`); delOpen = false; toast('Provider removed.'); load(); } catch (e) { toast(errMsg(e), 'bad'); } }
  async function setPolicy(v: boolean) {
    policyBusy = true;
    try { await put('/api/admin/settings/sso.local_login', { value: v, confirm_weaker: true }); localLogin = v; toast(v ? 'Password sign-in is allowed for everyone.' : 'Only owners can use a password now.'); }
    catch (e) { toast(errMsg(e), 'bad'); }
    policyBusy = false;
  }
  const why: Record<string, string> = { blocked_address: 'The address is not allowed (private, link-local or cloud-metadata ranges are refused).', insecure_url: 'The provider must use https.', issuer_mismatch: 'The provider reports a different issuer than the one you entered.', provider_unreachable: 'The provider could not be reached.', unresolvable: 'The host name did not resolve.', provider_http_error: 'The provider answered with an error.', provider_bad_response: 'The provider returned something that is not valid OpenID Connect discovery.', response_too_large: 'The response was too large.' };
</script>
<Page title="Single sign-on" lead="OpenID Connect (authorization code with PKCE). People sign in at your identity provider; Semechka never sees their password. Multi-factor authentication is the provider’s job: Semechka trusts the provider’s result.">
  {#snippet actions()}<button class="btn primary" onclick={() => edit(null)}>Add provider</button>{/snippet}
  <State {loading} {error} retry={load} empty={!loading && !error && !rows.length ? 'No provider yet. Add one, test discovery, then enable it.' : ''} />
  {#if rows.length}
    <section class="panel" style="margin-bottom:16px"><div class="panel-h"><h2 style="font-size:16px">Password sign-in</h2><span class="chip {localLogin ? '' : 'warn'}">{localLogin ? 'Allowed for everyone' : 'Owners only'}</span></div><div class="panel-b">
      <p class="small">When off and at least one provider is enabled, only owners can still sign in with a password. That is the way back in if the provider breaks, so keep an owner account with a password and test it first.</p>
      <button class="btn" disabled={policyBusy} onclick={() => setPolicy(!localLogin)}>{localLogin ? 'Require single sign-on' : 'Allow passwords for everyone'}</button>
    </div></section>
  {/if}
  <div class="stack">{#each rows as p (p.id)}
    <section class="panel"><div class="panel-h"><div><h3>{p.name}</h3><span class="mono tiny muted">{p.issuer}</span></div>
      <span class="chip {p.enabled ? 'ok' : ''}">{p.enabled ? 'Enabled' : 'Disabled'}</span></div>
      <div class="panel-b">
        <dl class="kv" style="margin-bottom:12px"><dt>Redirect URI to register</dt><dd class="mono">{p.redirect_uri}</dd><dt>Client ID</dt><dd class="mono">{p.client_id}</dd><dt>Client secret</dt><dd>{p.has_secret ? 'Stored encrypted (hidden)' : 'None (public client)'}</dd><dt>New accounts</dt><dd>{p.jit_create ? (p.require_approval ? 'Created, then wait for approval' : 'Created on first sign-in') : 'Not created automatically'}, role {p.default_role}</dd></dl>
        <div class="row"><button class="btn sm" onclick={() => test(p)}>Test discovery</button><button class="btn sm" onclick={() => edit(p)}>Edit</button><button class="btn sm danger" onclick={() => { delT = p; delOpen = true; }}>Remove</button></div>
        {#if result[p.id]}
          <div class="notice {result[p.id].busy ? '' : result[p.id].ok ? 'ok' : 'bad'}" style="margin-top:12px" role="status">
            {#if result[p.id].busy}<div>Fetching the discovery document…</div>
            {:else if result[p.id].ok}<div>Discovery works. {result[p.id].jwks_keys} signing key{result[p.id].jwks_keys === 1 ? '' : 's'} published. PKCE S256: {result[p.id].pkce_s256 === null ? 'not stated by the provider' : result[p.id].pkce_s256 ? 'supported' : 'NOT supported'}. Algorithms Semechka accepts: {result[p.id].supported_algorithms.join(', ') || 'provider did not list any'}. Sign-out at the provider: {result[p.id].end_session_endpoint ? 'available' : 'not offered'}.
              {#if result[p.id].compatible === false} <strong>This provider signs with no algorithm Semechka accepts (RS256 or ES256).</strong>{/if}</div>
            {:else}<div>{why[result[p.id].error] ?? result[p.id].error}{#if result[p.id].detail} <span class="muted">({result[p.id].detail})</span>{/if}</div>{/if}
          </div>
        {/if}
      </div></section>
  {/each}</div>
</Page>
<Modal bind:open title={cur ? `Edit ${cur.name}` : 'Add provider'}>
  <div class="grid">
    <div class="field"><label for="sn">Name shown on the sign-in button</label><input id="sn" class="input" bind:value={f.name} /></div>
    <div class="field"><label for="si">Issuer address</label><input id="si" class="input mono" bind:value={f.issuer} placeholder="https://login.example.com/realms/main" /><span class="hint">Must be https. Discovery is read from /.well-known/openid-configuration.</span></div>
    <div class="field"><label for="sc">Client ID</label><input id="sc" class="input mono" bind:value={f.client_id} /></div>
    <div class="field"><label for="ss">Client secret</label><input id="ss" class="input" type="password" autocomplete="off" bind:value={f.client_secret} placeholder={cur?.has_secret ? 'Leave blank to keep the stored secret' : ''} /><span class="hint">Stored encrypted. Never shown again, never exported, never written to the audit log.</span></div>
    <div class="field"><label for="sd">Allowed email domains (optional)</label><input id="sd" class="input" bind:value={domainText} placeholder="example.com, example.org" /></div>
    <div class="field"><label for="sr">Role for new accounts</label><select id="sr" class="input" bind:value={f.default_role}>{#each roles as r}<option value={r.id}>{r.label}</option>{/each}</select><span class="hint">Roles that carry administration can only be chosen by an owner.</span></div>
    <div class="field"><label for="sm">Group to role mapping (optional)</label><textarea id="sm" class="input mono" rows="3" bind:value={mapText} placeholder={'ai-admins = platform_admin\nstaff = member, analyst'}></textarea><span class="hint">One per line: the group name from the provider, then role ids. Roles given by a group are removed again when the person leaves the group; roles you assign by hand are never touched.</span></div>
  </div>
  <details style="margin:8px 0"><summary class="small">Claim names and scopes</summary>
    <div class="grid" style="margin-top:8px">
      <div class="field"><label for="sx">Scopes</label><input id="sx" class="input mono" bind:value={f.scopes} /></div>
      <div class="field"><label for="se">Email claim</label><input id="se" class="input mono" bind:value={f.email_claim} /></div>
      <div class="field"><label for="sa">Name claim</label><input id="sa" class="input mono" bind:value={f.name_claim} /></div>
      <div class="field"><label for="sg">Groups claim</label><input id="sg" class="input mono" bind:value={f.groups_claim} /></div>
    </div></details>
  <label class="row" style="gap:8px"><input type="checkbox" bind:checked={f.jit_create} /> Create an account on first sign-in</label>
  <label class="row" style="gap:8px"><input type="checkbox" bind:checked={f.require_approval} /> New accounts wait for administrator approval</label>
  <label class="row" style="gap:8px"><input type="checkbox" bind:checked={f.assume_email_verified} /> Treat the email as verified when the provider does not say (only if you trust the provider to verify emails)</label>
  {#if isOwner}<label class="row" style="gap:8px"><input type="checkbox" bind:checked={f.allow_private_network} /> The provider is on a private network (owner only; link-local and cloud-metadata addresses are always refused)</label>{/if}
  <label class="row" style="gap:8px"><input type="checkbox" bind:checked={f.enabled} /> Enabled (shows the sign-in button)</label>
  <p class="hint">An existing password account is never linked automatically. Its owner connects the provider from their account page while signed in.</p>
  {#if err}<p class="err" role="alert">{err}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (open = false)}>Cancel</button><button class="btn primary" onclick={save} disabled={!f.name || !f.issuer || !f.client_id}>Save</button>{/snippet}
</Modal>
<Modal bind:open={delOpen} title="Remove provider">
  <p>Remove <strong>{delT?.name}</strong>? People who sign in only through it will no longer be able to, until you add a provider again or give them a password.</p>
  {#snippet footer()}<button class="btn" onclick={() => (delOpen = false)}>Cancel</button><button class="btn danger" onclick={remove}>Remove</button>{/snippet}
</Modal>
