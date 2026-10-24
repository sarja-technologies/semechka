<script lang="ts">
  import Page from '$lib/components/admin/Page.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
</script>
<Page title="Registration & Sign-in" lead="Who can create an account, how, and how sessions and MFA behave. Changes apply immediately; existing sessions stay signed in."><SettingsPanel categories={['Registration & Sign-in']} searchable={false} /></Page>
