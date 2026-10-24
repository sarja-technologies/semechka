<script lang="ts">
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { get, post, patch, del, setCsrf, ApiError } from '$lib/api';
  import { app, loadMe } from '$lib/session.svelte';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  const me = $derived(app.me);
  let name = $state(app.me.user.display_name), theme = $state(app.me.user.prefs.theme ?? ''), density = $state(app.me.user.prefs.density ?? '');
  let cur = $state(''), nw = $state(''), pwErr = $state('');
  let mfa = $state<{ secret: string; uri: string } | null>(null), code = $state(''), mfaErr = $state(''), mfaOff = $state(''), mfaOffOpen = $state(false);
  let sessions = $state<any[]>([]), sessErr = $state('');
  let delOpen = $state(false), delPw = $state(''), delErr = $state('');
  const m = (e: unknown) => (e instanceof ApiError ? e.message : 'Something unexpected happened.');
  async function loadSessions() { try { sessions = await get('/api/auth/sessions'); } catch (e) { sessErr = m(e); } }
  onMount(loadSessions);
  // SSO: connections to identity providers (explicit linking; nothing is ever linked automatically)
  let ssoInfo = $state<any>({ linked: [], available: [], has_password: true });
  async function loadSso() { try { ssoInfo = await get('/api/sso/identities'); } catch { /* optional */ } }
  onMount(loadSso);
  async function unlink(id: number) { try { await del(`/api/sso/identities/${id}`); toast('Disconnected.'); loadSso(); } catch (e) { toast(e instanceof ApiError ? e.message : 'Could not disconnect.', 'bad'); } }
  async function saveProfile(e: Event) { e.preventDefault(); try { await patch('/api/me', { display_name: name, ...(theme ? { theme } : {}), ...(density ? { density } : {}) }); await loadMe(); toast('Saved'); } catch (x) { toast(m(x), 'bad'); } }
  async function changePw(e: Event) { e.preventDefault(); pwErr = ''; try { const r = await post('/api/auth/password', { current: cur, new: nw }); setCsrf(r.csrf); await loadMe(); cur = nw = ''; toast('Password changed. Other sessions were signed out.'); loadSessions(); } catch (x) { pwErr = m(x); } }
  async function beginMfa() { mfaErr = ''; try { mfa = await post('/api/auth/mfa/begin'); } catch (x) { mfaErr = m(x); } }
  async function confirmMfa(e: Event) { e.preventDefault(); mfaErr = ''; try { await post('/api/auth/mfa/confirm', { code }); mfa = null; code = ''; await loadMe(); toast('Multi-factor authentication is on.'); if (!app.me.mfa_enrolment_required) goto('/account'); } catch (x) { mfaErr = m(x); } }
  async function disableMfa() { try { await post('/api/auth/mfa/disable', { password: mfaOff }); mfaOffOpen = false; mfaOff = ''; await loadMe(); toast('Multi-factor authentication is off.'); } catch (x) { mfaErr = m(x); mfaOffOpen = false; } }
  async function revoke(id: string) { try { await del(`/api/auth/sessions/${id}`); loadSessions(); } catch (x) { toast(m(x), 'bad'); } }
  async function deleteMe() { delErr = ''; try { await api_del(); } catch (x) { delErr = m(x); } }
  async function api_del() { const { api } = await import('$lib/api'); await api('DELETE', '/api/me', { password: delPw }); setCsrf(''); app.me = null; goto('/login'); }
</script>
<div class="pg">
  <h1 style="margin-bottom:16px">Account and security</h1>
  {#if me.mfa_enrolment_required}<div class="notice warn" role="alert"><div><strong>Multi-factor authentication is required.</strong> Set it up below. Until you do, only this page is available.</div></div>{/if}

  <section class="panel"><div class="panel-h"><h2 style="font-size:16px">Profile and appearance</h2></div><form class="panel-b" onsubmit={saveProfile}>
    <p class="small muted">Signed in as <span class="mono">{me.user.email}</span>. Roles: {me.roles.map((r: any) => r.label).join(', ')}{me.teams.length ? `. Teams: ${me.teams.map((t: any) => t.name).join(', ')}` : ''}.</p>
    <div class="field"><label for="dn">Display name</label><input id="dn" class="input" bind:value={name} maxlength="120" /></div>
    <div class="field"><label for="t">Theme</label><select id="t" class="input" bind:value={theme}><option value="">Organisation default</option><option value="paper">Paper</option><option value="carbon">Carbon</option><option value="graphite">Graphite</option><option value="system">Match system</option></select></div>
    <div class="field"><label for="d">Density</label><select id="d" class="input" bind:value={density}><option value="">Organisation default</option><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></div>
    <button class="btn primary" disabled={!name.trim()}>Save</button></form></section>

  <section class="panel"><div class="panel-h"><h2 style="font-size:16px">Password</h2></div><form class="panel-b" onsubmit={changePw}>
    {#if pwErr}<div class="notice bad" role="alert">{pwErr}</div>{/if}
    <div class="field"><label for="cp">Current password</label><input id="cp" class="input" type="password" autocomplete="current-password" bind:value={cur} /></div>
    <div class="field"><label for="np">New password</label><input id="np" class="input" type="password" autocomplete="new-password" bind:value={nw} /></div>
    <button class="btn" disabled={!cur || !nw}>Change password</button><p class="hint" style="margin-top:8px">Changing it signs out every other session.</p></form></section>

  <section class="panel"><div class="panel-h"><h2 style="font-size:16px">Multi-factor authentication</h2><span class="chip {me.user.mfa ? 'ok' : ''}">{me.user.mfa ? 'On' : 'Off'}</span></div><div class="panel-b">
    {#if mfaErr}<div class="notice bad" role="alert">{mfaErr}</div>{/if}
    {#if me.user.mfa}
      <p class="small">An authenticator app code is required each time you sign in.</p><button class="btn" onclick={() => (mfaOffOpen = true)}>Turn off</button>
    {:else if mfa}
      <p class="small">Add this key to an authenticator app (time-based, 6 digits, 30 seconds), then enter the code it shows.</p>
      <dl class="kv"><dt>Key</dt><dd>{mfa.secret}</dd><dt>Setup link</dt><dd>{mfa.uri}</dd></dl>
      <form onsubmit={confirmMfa} style="margin-top:12px"><div class="field"><label for="mc">Code</label><input id="mc" class="input mono" inputmode="numeric" maxlength="8" bind:value={code} autocomplete="one-time-code" style="max-width:160px" /></div><button class="btn primary" disabled={!code}>Confirm and turn on</button></form>
    {:else}<p class="small">Adds a second step at sign-in. Recommended for anyone with administration access.</p><button class="btn primary" onclick={beginMfa}>Set up</button>{/if}
  </div></section>

  {#if ssoInfo.available.length || ssoInfo.linked.length}
  <section class="panel"><div class="panel-h"><h2 style="font-size:16px">Single sign-on</h2></div><div class="panel-b">
    <p class="small">Connect your account to your organisation’s identity provider so you can sign in with it. You must be signed in here with your current method; nothing is connected automatically.</p>
    {#if ssoInfo.linked.length}<ul class="plain">{#each ssoInfo.linked as l (l.id)}<li class="row"><span class="grow">{l.provider}</span><button class="btn sm" onclick={() => unlink(l.id)} disabled={!ssoInfo.has_password}>Disconnect</button></li>{/each}</ul>
      {#if !ssoInfo.has_password}<p class="hint">This account has no password, so it cannot be disconnected.</p>{/if}{/if}
    <div class="row">{#each ssoInfo.available.filter((a: any) => !ssoInfo.linked.some((l: any) => l.provider_id === a.id)) as a (a.id)}<a class="btn" href={`/api/sso/${a.id}/start?link=1&return_to=/account`} data-sveltekit-reload>Connect {a.name}</a>{/each}</div>
  </div></section>
  {/if}

  <section class="panel"><div class="panel-h"><h2 style="font-size:16px">Signed-in devices</h2></div><div class="panel-b">
    {#if sessErr}<div class="notice bad">{sessErr}</div>{/if}
    <div class="tablewrap"><table class="t"><caption class="sr-only">Active sessions</caption><thead><tr><th>Browser</th><th>Address</th><th>Last active</th><th></th></tr></thead><tbody>
      {#each sessions as s}<tr><td class="wrap small">{s.ua.slice(0, 60) || 'Unknown'}{#if s.current} <span class="chip ok">This device</span>{/if}</td><td class="mono">{s.ip}</td><td>{new Date(s.last_seen * 1000).toLocaleString()}</td><td>{#if !s.current}<button class="btn sm" onclick={() => revoke(s.id)}>Sign out</button>{/if}</td></tr>{/each}
    </tbody></table></div></div></section>

  <section class="panel"><div class="panel-h"><h2 style="font-size:16px">Delete account</h2></div><div class="panel-b"><p class="small">Permanently removes your account and conversations. Backups expire on their own schedule. If your organisation has turned this off you will be told.</p><button class="btn danger" onclick={() => (delOpen = true)}>Delete my account…</button></div></section>
</div>
<Modal bind:open={mfaOffOpen} title="Turn off multi-factor authentication?"><div class="field"><label for="mp">Confirm with your password</label><input id="mp" class="input" type="password" bind:value={mfaOff} /></div>{#snippet footer()}<button class="btn" onclick={() => (mfaOffOpen = false)}>Cancel</button><button class="btn danger solid" onclick={disableMfa} disabled={!mfaOff}>Turn off</button>{/snippet}</Modal>
<Modal bind:open={delOpen} title="Delete your account?"><p class="small">This cannot be undone. Enter your password to confirm.</p>{#if delErr}<div class="notice bad" role="alert">{delErr}</div>{/if}<div class="field"><label for="dp">Password</label><input id="dp" class="input" type="password" bind:value={delPw} /></div>{#snippet footer()}<button class="btn" onclick={() => (delOpen = false)}>Cancel</button><button class="btn danger solid" onclick={deleteMe} disabled={!delPw}>Delete account</button>{/snippet}</Modal>
<style>
  .pg { padding: 24px; max-width: 720px; display: grid; gap: 16px; }
  @media (max-width: 719px) { .pg { padding: 16px; } }
</style>
