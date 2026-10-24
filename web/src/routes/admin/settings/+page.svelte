<script lang="ts">
  import { page } from '$app/state';
  import Page from '$lib/components/admin/Page.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
  const q = page.url.searchParams.get('q') ?? ''; const cat = page.url.searchParams.get('category');
</script>
<Page title="All settings" lead="Every product setting, searchable. Press / to search. Each shows where its value comes from and what changing it does.">
  {#key q + cat}<SettingsPanel {q} categories={cat ? [cat] : []} skip={['tech.presets', 'nav.items']} />{/key}
</Page>
