<script lang="ts">
  import Page from '$lib/components/admin/Page.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
</script>
<Page title="Access & System" lead="Network exposure binds on the next restart. Nothing is exposed until you restart the service."><SettingsPanel categories={['System']} searchable={false} /></Page>
