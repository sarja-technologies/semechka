<script lang="ts">
  import Page from '$lib/components/admin/Page.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
</script>
<Page title="Email" lead="Email is sent only when an SMTP host and sender are configured. The password is write-only."><SettingsPanel categories={['Email']} searchable={false} /></Page>
