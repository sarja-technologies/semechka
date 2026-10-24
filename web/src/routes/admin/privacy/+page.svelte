<script lang="ts">
  import Page from '$lib/components/admin/Page.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
</script>
<Page title="Privacy & Retention" lead="What is kept, for how long, and where requests may go. Changes that weaken protection show an impact preview and need confirmation."><SettingsPanel categories={['Privacy & Retention']} searchable={false} /></Page>
