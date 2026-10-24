<script lang="ts">
  import { post, ApiError } from '$lib/api';
  import AuthFrame from '$lib/components/AuthFrame.svelte';
  let email = $state(''), busy = $state(false), msg = $state(''), error = $state('');
  async function submit(e: Event) {
    e.preventDefault(); busy = true; error = '';
    try { msg = (await post('/api/auth/forgot', { email })).message; } catch (err) { error = err instanceof ApiError ? err.message : 'Request failed.'; } finally { busy = false; }
  }
</script>
<AuthFrame title="Reset your password">
  {#if msg}<div class="notice ok" role="status">{msg}</div>{:else}
    <p class="small muted">If email delivery is set up on this server, a reset link is sent to the address you enter. If it is not, ask an administrator to set a new password for you.</p>
    <form onsubmit={submit}>
      {#if error}<div class="notice bad" role="alert">{error}</div>{/if}
      <div class="field"><label for="e">Email</label><input id="e" class="input" type="email" autocomplete="email" bind:value={email} required /></div>
      <button class="btn primary" disabled={busy || !email}>Request reset link</button>
    </form>
  {/if}
  <p class="small" style="margin-top:16px"><a href="/login">Back to sign in</a></p>
</AuthFrame>
