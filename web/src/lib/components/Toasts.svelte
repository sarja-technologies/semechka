<script lang="ts">
  import { toasts } from '$lib/toast.svelte';
</script>
<div class="toasts" role="status" aria-live="polite">
  {#each toasts as t (t.id)}<div class="toast {t.kind} fade">{t.text}</div>{/each}
</div>
<style>
  .toasts { position: fixed; right: 16px; bottom: 16px; display: grid; gap: 8px; z-index: 200; max-width: min(380px, calc(100vw - 32px)); }
  .toast { background: var(--surface); border: 1px solid var(--border-strong); border-left-width: 3px; border-radius: var(--radius-sm); padding: 10px 14px; box-shadow: var(--shadow); font-size: 14px; }
  .toast.ok { border-left-color: var(--success); } .toast.bad { border-left-color: var(--danger); } .toast.info { border-left-color: var(--info); }
</style>
