<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { get, post, ApiError } from '$lib/api';
  import { app, loadMe } from '$lib/session.svelte';
  import AuthFrame from '$lib/components/AuthFrame.svelte';
  const focusMe = (n: HTMLElement) => n.focus();
  let email = $state(''), password = $state(''), totp = $state(''), recovery = $state(''), useRecovery = $state(false);
  let needMfa = $state(false), busy = $state(false), error = $state('');
  // SSO: providers come from the server; a failed sign-in returns here with a short code, never a raw error.
  let sso = $state<{ providers: { id: string; name: string }[]; local_login: boolean; errors: Record<string, string> }>({ providers: [], local_login: true, errors: {} });
  let ssoError = $state('');
  onMount(async () => {
    try { sso = await get('/api/sso/providers'); } catch { /* SSO is optional: the password form still works */ }
    const code = page.url.searchParams.get('sso_error');
    if (code) ssoError = sso.errors[code] ?? sso.errors.failed ?? 'Sign-in failed.';
  });
  const nextPath = () => { const n = page.url.searchParams.get('next'); return n && n.startsWith('/') && !n.startsWith('//') ? n : '/'; };
  async function submit(e: Event) {
    e.preventDefault(); busy = true; error = '';
    try {
      await post('/api/auth/login', { email, password, ...(needMfa ? (useRecovery ? { recovery_code: recovery.trim() } : { totp }) : {}) });
      await loadMe();
      const next = page.url.searchParams.get('next');
      goto(next && next.startsWith('/') && !next.startsWith('//') ? next : '/', { replaceState: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === 'mfa_required') { needMfa = true; error = ''; }
      else error = err instanceof ApiError ? err.message : 'Sign-in failed.';
    } finally { busy = false; }
  }
</script>
  {#snippet pwform()}
  <form onsubmit={submit} novalidate>
    {#if error}<div class="notice bad" role="alert">{error}</div>{/if}
    <div class="field"><label for="em">Email</label><input id="em" class="input" type="email" autocomplete="username" bind:value={email} required disabled={needMfa} /></div>
    <div class="field"><label for="pw">Password</label><input id="pw" class="input" type="password" autocomplete="current-password" bind:value={password} required disabled={needMfa} /></div>
    {#if needMfa && !useRecovery}
      <div class="field"><label for="tp">Authentication code</label><input id="tp" class="input mono" inputmode="numeric" autocomplete="one-time-code" maxlength="8" bind:value={totp} use:focusMe aria-describedby="tph" /><span id="tph" class="hint">The 6-digit code from your authenticator app.</span></div>
      <p class="small"><button type="button" class="linkish" onclick={() => { useRecovery = true; error = ''; }}>Lost your authenticator? Use a recovery code</button></p>
    {:else if needMfa}
      <div class="field"><label for="rc">Recovery code</label><input id="rc" class="input mono" autocomplete="off" spellcheck="false" maxlength="40" bind:value={recovery} use:focusMe aria-describedby="rch" /><span id="rch" class="hint">One of the codes you saved when you turned on two-step sign-in. Each code works once; set up your authenticator again afterwards.</span></div>
      <p class="small"><button type="button" class="linkish" onclick={() => { useRecovery = false; error = ''; }}>Use the authenticator code instead</button></p>
    {/if}
    <button class="btn primary" style="width:100%" disabled={busy || !email || !password || (needMfa && (useRecovery ? !recovery.trim() : !totp))}>{busy ? 'Signing in…' : needMfa ? 'Verify and sign in' : 'Sign in'}</button>
  </form>
  {/snippet}
<AuthFrame title={app.brand.login_title || `Sign in${app.brand.app_name ? ' to ' + app.brand.app_name : ''}`}>
  {#if ssoError}<div class="notice bad" role="alert">{ssoError}</div>{/if}
  {#if sso.providers.length}
    <div class="stack" style="margin-bottom:16px">
      {#each sso.providers as p (p.id)}<a class="btn" style="width:100%;justify-content:center" href={`/api/sso/${p.id}/start?return_to=${encodeURIComponent(nextPath())}`} data-sveltekit-reload>Continue with {p.name}</a>{/each}
    </div>
  {/if}
  {#if !sso.local_login && sso.providers.length}
    <details><summary class="small">Sign in with a password instead</summary>
      <p class="hint">Your organisation requires single sign-on. Only owners can still use a password; it is the way back in if the identity provider is unavailable.</p>
      {@render pwform()}
    </details>
  {:else}
    {@render pwform()}
  {/if}
  <p class="small" style="margin-top:16px"><a href="/forgot">Forgot your password?</a></p>
  {#if app.signup.open}
    <p class="small">No account? <a href="/signup">{app.signup.needs_invite ? 'Register with an invitation' : 'Create one'}</a>.</p>
  {:else if app.signup.message}
    <p class="small muted">{app.signup.message}</p>
  {/if}
</AuthFrame>
