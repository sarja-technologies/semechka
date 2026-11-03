<script lang="ts">
  import type { WorkArtifact } from '$lib/work/types';
  import ArtifactCard from './ArtifactCard.svelte';
  let { artifacts, loaded, error = '', onretry, onsaved }: { artifacts: WorkArtifact[]; loaded: boolean; error?: string; onretry: () => void; onsaved?: (id: string) => void } = $props();
</script>

{#if !loaded}<p class="muted small" role="status">Loading…</p>
{:else if error && !artifacts.length}<div class="notice bad" role="alert"><div>{error} <button class="btn sm" onclick={onretry}>Try again</button></div></div>
{:else if !artifacts.length}<p class="muted small">No artifacts yet. Files the session saves in its artifacts folder, or publishes, appear here and stay after the container is gone.</p>
{:else}
  {#if error}<p class="err small" role="alert">{error}</p>{/if}
  <div class="stack" style="gap:var(--s3)">{#each artifacts as a (a.id)}<ArtifactCard {a} compact {onsaved} />{/each}</div>
{/if}
