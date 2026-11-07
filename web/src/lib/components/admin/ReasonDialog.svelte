<script lang="ts">
  // The calm prompt that stands in front of every administrator access to private content. The reason is sent with the request, written to
  // the audit log (never any content) and shown to the owner. It is kept in memory for this browsing session only.
  import Modal from '$lib/components/Modal.svelte';
  import { answerReason, reasonUi } from '$lib/admin/reason.svelte';
  import { MAX_REASON, MIN_REASON, reasonOk } from '$lib/admin/explorer';
  // Closing the dialog any other way (Escape, the scrim, the close button) is a cancel: nothing is requested or recorded.
  $effect(() => { if (!reasonUi.open && reasonUi.resolve) answerReason(null); });
  const ok = $derived(reasonOk(reasonUi.value));
  function submit(e: Event) { e.preventDefault(); if (ok) answerReason(reasonUi.value.trim()); }
</script>
<Modal bind:open={reasonUi.open} title={reasonUi.title || 'State a reason'}>
  <form onsubmit={submit} id="reason-form">
    <p class="small" style="margin-top:0">{reasonUi.detail}</p>
    <div class="field" style="margin-bottom:0">
      <label for="rs">Reason for access</label>
      <textarea id="rs" class="input" rows="3" maxlength={MAX_REASON} bind:value={reasonUi.value} aria-describedby="rsh" placeholder="e.g. Legal hold request 2026-114 from counsel"></textarea>
      <span id="rsh" class="hint">At least {MIN_REASON} characters. Recorded with your name in the audit log and shown to the person whose items you open. Kept in this tab only, until you close it.</span>
    </div>
  </form>
  {#snippet footer()}
    <button class="btn" type="button" onclick={() => answerReason(null)}>Cancel</button>
    <button class="btn primary" type="submit" form="reason-form" disabled={!ok}>Record and continue</button>
  {/snippet}
</Modal>
