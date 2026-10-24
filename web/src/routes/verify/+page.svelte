<script lang="ts">
  import { page } from '$app/state';
  import { onMount } from 'svelte';
  import { post, ApiError } from '$lib/api';
  import AuthFrame from '$lib/components/AuthFrame.svelte';
  let phase = $state<'working' | 'ok' | 'bad'>('working'), msg = $state('');
  onMount(async () => {
    try { msg = (await post('/api/auth/verify', { token: page.url.searchParams.get('token') ?? '' })).message; phase = 'ok'; }
    catch (e) { msg = e instanceof ApiError ? e.message : 'Could not verify.'; phase = 'bad'; }
  });
</script>
<AuthFrame title="Confirm your email">
  <div class="notice {phase === 'ok' ? 'ok' : phase === 'bad' ? 'bad' : 'info'}" role="status">{phase === 'working' ? 'Checking the link…' : msg}</div>
  <a href="/login">Go to sign in</a>
</AuthFrame>
