<script lang="ts">
  import { onMount } from 'svelte';
  import { get, ApiError } from '$lib/api';
  let u = $state<any>(null), error = $state('');
  onMount(async () => { try { u = await get('/api/usage'); } catch (e) { error = e instanceof ApiError ? e.message : 'Could not load usage.'; } });
</script>
<div style="padding:24px;max-width:720px">
  <h1 style="margin-bottom:16px">Your usage</h1>
  {#if error}<div class="notice bad" role="alert">{error}</div>
  {:else if !u}<p class="muted" role="status">Loading…</p>
  {:else}
    <dl class="kv" style="font-size:14px">
      <dt>Messages in the last 24 hours</dt><dd>{u.messages_24h}{u.daily_limit ? ` of ${u.daily_limit} allowed` : ' (no daily limit)'}</dd>
      <dt>Messages in the last 30 days</dt><dd>{u.messages_30d}</dd>
      {#if u.tokens_30d}<dt>Prompt tokens (30 days)</dt><dd>{u.tokens_30d.prompt.toLocaleString()}</dd><dt>Output tokens (30 days)</dt><dd>{u.tokens_30d.completion.toLocaleString()}</dd>{/if}
    </dl>
    {#if u.tokens_30d}<p class="small muted" style="margin-top:12px">{u.tokens_30d.note}</p>{/if}
    <p class="small muted" style="margin-top:12px">Counts include no-save conversations (numbers only, never their text).</p>
  {/if}
</div>
