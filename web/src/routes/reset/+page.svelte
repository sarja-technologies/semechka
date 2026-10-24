<script lang="ts">
  import { page } from '$app/state';
  import { post, ApiError } from '$lib/api';
  import AuthFrame from '$lib/components/AuthFrame.svelte';
  let password = $state(''), busy = $state(false), done = $state(false), error = $state('');
  async function submit(e: Event) {
    e.preventDefault(); busy = true; error = '';
    try { await post('/api/auth/reset', { token: page.url.searchParams.get('token') ?? '', password }); done = true; } catch (err) { error = err instanceof ApiError ? err.message : 'Reset failed.'; } finally { busy = false; }
  }
</script>
<AuthFrame title="Choose a new password">
  {#if done}<div class="notice ok" role="status">Password changed. All other sessions were signed out.</div><a class="btn primary" href="/login">Sign in</a>{:else}
    <form onsubmit={submit}>
      {#if error}<div class="notice bad" role="alert">{error}</div>{/if}
      <div class="field"><label for="p">New password</label><input id="p" class="input" type="password" autocomplete="new-password" bind:value={password} required /></div>
      <button class="btn primary" disabled={busy || !password}>Set password</button>
    </form>
  {/if}
</AuthFrame>
