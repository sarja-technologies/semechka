<script lang="ts">
  import Icon from '$lib/components/Icon.svelte';
  let { scope = '' }: { scope?: string } = $props();
</script>
<div class="notice info audit" role="note">
  <Icon name="shield" size={18} />
  <div class="grow"><strong>Every view is recorded in the audit log and shown to the owner.</strong>
    <span class="small">Each list, conversation, generation record, preview and download writes one entry with your name and reason (never the content itself). {scope === 'organisation' ? 'You can reach people in your own organisations only.' : scope === 'instance' ? 'You can reach everyone on this server.' : ''}</span></div>
</div>
<style>.audit { align-items: flex-start; } .audit :global(svg) { flex: none; margin-top: 2px; }</style>
