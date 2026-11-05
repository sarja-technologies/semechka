<script lang="ts">
  // Engine settings, drawn by the same schema-driven form the message box uses (ProfileForm), limited to the fields wanted here.
  import ProfileForm from '$lib/components/chat/ProfileForm.svelte';
  import { OWNED_FIELDS, pick, usedBy } from '$lib/studio/schema';
  import type { Studio } from '$lib/studio/store.svelte';
  let { s, only, skip = [], label = 'Settings' }: { s: Studio; only?: string[]; skip?: string[]; label?: string } = $props();
  const fixed = $derived(Object.keys(s.mode?.fixed_inputs ?? {}));
  const prof = $derived(pick(s.profileInfo, { only, skip: [...OWNED_FIELDS, ...fixed, ...skip] }));
  const shown = $derived(prof ? usedBy(prof, s.mode?.operation, Object.keys(prof.inputs_schema ?? {})) : []);
  let problem = $state('');
</script>
{#if prof && shown.length}
  <div class="sf" aria-label={label} data-testid="settings-fields">
    <ProfileForm profile={prof} operation={s.mode?.operation ?? null} bind:values={() => s.spec.settings, (v) => s.edit((sp) => { sp.settings = v; })} bind:problem />
  </div>
{/if}
<style>
  .sf :global(.pf) { background: transparent; border-color: var(--border); }
</style>
