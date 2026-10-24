<script lang="ts">
  import { app } from '$lib/session.svelte';
  const keys = [['Enter', 'Send the message'], ['Shift + Enter', 'New line in the message'], ['Esc', 'Stop a reply that is being written, or close a dialog'], ['/', 'Search your conversations'], ['g then c', 'Go to chat'], ['g then u', 'Go to your usage'], ['g then a', 'Go to administration (if you have access)']];
</script>
<div style="padding:24px;max-width:720px">
  <h1 style="margin-bottom:16px">Help</h1>
  <h2 style="font-size:16px;margin-bottom:8px">Using chat</h2>
  <ul class="small" style="padding-left:18px;display:grid;gap:6px;margin-bottom:24px">
    <li>Edit one of your messages or regenerate a reply to try another version. Earlier versions are kept; use the arrows beside a message to move between them.</li>
    <li>“No-save” conversations are never written to the chat database. They disappear when you close or reload the page.</li>
    <li>The lock button beside the message box explains exactly how the current chat is stored and who can read it.</li>
    <li>You can attach text and code files. Images and other binary files are not supported in this release.</li>
  </ul>
  <h2 style="font-size:16px;margin-bottom:8px">Keyboard</h2>
  <div class="tablewrap"><table class="t"><caption class="sr-only">Keyboard shortcuts</caption><tbody>{#each keys as [k, d]}<tr><td><kbd>{k}</kbd></td><td class="wrap">{d}</td></tr>{/each}</tbody></table></div>
  <p style="margin-top:24px">{#if app.brand.support_url}Need a person? <a href={app.brand.support_url} target="_blank" rel="noopener noreferrer">Contact support</a>.{:else}Questions about access or models go to your administrator.{/if}</p>
</div>
