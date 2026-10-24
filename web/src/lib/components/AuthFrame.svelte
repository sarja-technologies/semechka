<script lang="ts">
  import type { Snippet } from 'svelte';
  import Wordmark from './Wordmark.svelte';
  import { app } from '$lib/session.svelte';
  let { title, children, wide = false }: { title: string; children: Snippet; wide?: boolean } = $props();
</script>
<main id="main" class="auth">
  <div class="box" class:wide>
    <div class="brand"><Wordmark size={22} /></div>
    <h1>{title}</h1>
    {@render children()}
    {#if app.brand.signin_message}<p class="hint note">{app.brand.signin_message}</p>{/if}
  </div>
  {#if app.brand.footer_text || app.brand.support_url}
    <footer class="foot small muted">{app.brand.footer_text}{#if app.brand.support_url} <a href={app.brand.support_url}>Help</a>{/if}</footer>
  {/if}
</main>
<style>
  .auth { min-height: 100dvh; display: grid; grid-template-rows: 1fr auto; padding: 16px; }
  .box { width: min(420px, 100%); margin: 8vh auto 0; align-self: start; }
  .box.wide { width: min(760px, 100%); }
  .brand { margin-bottom: 24px; }
  h1 { margin-bottom: 16px; font-size: 22px; }
  .note { margin-top: 24px; }
  .foot { text-align: center; padding: 16px; }
</style>
