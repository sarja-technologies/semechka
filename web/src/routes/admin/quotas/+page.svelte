<script lang="ts">
  import Page from '$lib/components/admin/Page.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
</script>
<Page title="Quotas" lead="Per-person limits. Where scopes disagree, the most restrictive non-zero limit wins."><SettingsPanel categories={['Quotas & Scheduling']} searchable={false} /></Page>
