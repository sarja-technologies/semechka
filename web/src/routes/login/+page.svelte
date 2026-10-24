<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { post, ApiError } from '$lib/api';
  import { app, loadMe } from '$lib/session.svelte';
  import AuthFrame from '$lib/components/AuthFrame.svelte';
  const focusMe = (n: HTMLElement) => n.focus();
  let email = $state(''), password = $state(''), totp = $state('');
  let needMfa = $state(false), busy = $state(false), error = $state('');
  async function submit(e: Event) {
    e.preventDefault(); busy = true; error = '';
    try {
      await post('/api/auth/login', { email, password, ...(needMfa ? { totp } : {}) });
      await loadMe();
      const next = page.url.searchParams.get('next');
      goto(next && next.startsWith('/') && !next.startsWith('//') ? next : '/', { replaceState: true });
    } catch (err) {
      if (err instanceof ApiError && err.code === 'mfa_required') { needMfa = true; error = ''; }
      else error = err instanceof ApiError ? err.message : 'Sign-in failed.';
    } finally { busy = false; }
  }
</script>
<AuthFrame title={`Sign in${app.brand.app_name ? ' to ' + app.brand.app_name : ''}`}>
  <form onsubmit={submit} novalidate>
    {#if error}<div class="notice bad" role="alert">{error}</div>{/if}
    <div class="field"><label for="em">Email</label><input id="em" class="input" type="email" autocomplete="username" bind:value={email} required disabled={needMfa} /></div>
    <div class="field"><label for="pw">Password</label><input id="pw" class="input" type="password" autocomplete="current-password" bind:value={password} required disabled={needMfa} /></div>
    {#if needMfa}
      <div class="field"><label for="tp">Authentication code</label><input id="tp" class="input mono" inputmode="numeric" autocomplete="one-time-code" maxlength="8" bind:value={totp} use:focusMe aria-describedby="tph" /><span id="tph" class="hint">The 6-digit code from your authenticator app.</span></div>
    {/if}
    <button class="btn primary" style="width:100%" disabled={busy || !email || !password || (needMfa && !totp)}>{busy ? 'Signing in…' : needMfa ? 'Verify and sign in' : 'Sign in'}</button>
  </form>
  <p class="small" style="margin-top:16px"><a href="/forgot">Forgot your password?</a></p>
  {#if app.signup.open}
    <p class="small">No account? <a href="/signup">{app.signup.needs_invite ? 'Register with an invitation' : 'Create one'}</a>.</p>
  {:else if app.signup.message}
    <p class="small muted">{app.signup.message}</p>
  {/if}
</AuthFrame>
