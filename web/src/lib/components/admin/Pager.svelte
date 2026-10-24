<script lang="ts">
  let { page = $bindable(1), perPage, total, onchange }: { page: number; perPage: number; total: number; onchange: () => void } = $props();
  const pages = $derived(Math.max(1, Math.ceil(total / perPage)));
  const go = (p: number) => { page = Math.min(pages, Math.max(1, p)); onchange(); };
</script>
<nav class="row" aria-label="Pagination" style="padding:8px 0">
  <span class="muted small">{total} total · page {page} of {pages}</span><span class="spacer"></span>
  <button class="btn sm" disabled={page <= 1} onclick={() => go(page - 1)}>Previous</button>
  <button class="btn sm" disabled={page >= pages} onclick={() => go(page + 1)}>Next</button>
</nav>
