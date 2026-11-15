<script lang="ts">
  // Pictures and videos made inside a conversation: attach several pictures, use editing profiles, keep it all in one thread. The conversation shows up in Chat history too.
  import ExpBack from '$lib/components/ExpBack.svelte';
  import ChatView from '$lib/components/chat/ChatView.svelte';
</script>
<svelte:head><title>Conversation studio · Experimental</title></svelte:head>
<div class="wrap"><div class="bar"><ExpBack /></div><div class="body"><ChatView base="/experimental/studio" media /></div></div>
<style>
  .wrap { flex: 1; min-height: 0; display: flex; flex-direction: column; } .bar { flex: none; padding: 6px 12px 0; display: flex; align-items: center; }
  .body { flex: 1; min-height: 0; display: flex; flex-direction: column; } .body > :global(*) { flex: 1; min-height: 0; }
</style>
