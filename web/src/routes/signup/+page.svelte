<script lang="ts">
  import { page } from '$app/state';
  import { post, ApiError } from '$lib/api';
  import { app } from '$lib/session.svelte';
  import AuthFrame from '$lib/components/AuthFrame.svelte';
  let email = $state(''), name = $state(''), password = $state(''), invite = $state(page.url.searchParams.get('invite') ?? '');
  let busy = $state(false), error = $state(''), done = $state('');
  async function submit(e: Event) {
    e.preventDefault(); busy = true; error = '';
    try { const r = await post('/api/auth/signup', { email, password, display_name: name, invite: invite || null }); done = r.message; }
    catch (err) { error = err instanceof ApiError ? err.message : 'Registration failed.'; }
    finally { busy = false; }
  }
</script>
<AuthFrame title="Create an account">
  {#if done}
    <div class="notice ok" role="status">{done}</div><p><a href="/login">Back to sign in</a></p>
  {:else if !app.signup.open}
    <div class="notice info"><div>{app.signup.message || 'Registration is closed.'}</div></div><p><a href="/login">Back to sign in</a></p>
  {:else}
    {#if app.signup.requires_approval}<div class="notice info">New accounts must be approved by an administrator before you can sign in.</div>{/if}
    <form onsubmit={submit} novalidate>
      {#if error}<div class="notice bad" role="alert">{error}</div>{/if}
      <div class="field"><label for="n">Your name</label><input id="n" class="input" autocomplete="name" bind:value={name} required /></div>
      <div class="field"><label for="e">Email</label><input id="e" class="input" type="email" autocomplete="email" bind:value={email} required /></div>
      <div class="field"><label for="p">Password</label><input id="p" class="input" type="password" autocomplete="new-password" bind:value={password} required aria-describedby="ph" /><span id="ph" class="hint">Use a long passphrase. Short or common passwords are refused.</span></div>
      {#if app.signup.needs_invite}<div class="field"><label for="i">Invitation code</label><input id="i" class="input mono" bind:value={invite} required /></div>{/if}
      {#if app.privacyNotice}<p class="hint">{app.privacyNotice}</p>{/if}
      <button class="btn primary" style="width:100%" disabled={busy || !email || !name || !password}>{busy ? 'Creating…' : 'Create account'}</button>
    </form>
    <p class="small" style="margin-top:16px">Already registered? <a href="/login">Sign in</a>.</p>
  {/if}
</AuthFrame>
