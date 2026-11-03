<script lang="ts">
  // The person's Work sessions: title, status, last activity and container state. Delete asks the parent to confirm.
  import { sessionStatus, containerState, relativeTime } from '$lib/work/format';
  import type { WorkSession } from '$lib/work/types';
  import type { Problem } from '$lib/work/client';
  import Icon from '$lib/components/Icon.svelte';

  let { sessions, activeId = null, loaded, error = null, now, onretry, ondelete }:
    { sessions: WorkSession[]; activeId?: string | null; loaded: boolean; error?: Problem | null; now: number; onretry: () => void; ondelete: (s: WorkSession) => void } = $props();
</script>

<nav aria-label="Work sessions">
  {#if !loaded}
    <p class="muted small pad" role="status">Loading sessions…</p>
  {:else if error && !sessions.length}
    <div class="notice bad pad-m" role="alert"><div>{error.message} <button class="btn sm" onclick={onretry}>Try again</button></div></div>
  {:else if !sessions.length}
    <p class="muted small pad">No sessions yet. A session is a task the model works on in its own container, with its own files.</p>
  {:else}
    {#if error}<p class="err pad" role="alert">{error.message}</p>{/if}
    <ul>
      {#each sessions as s (s.id)}
        {@const st = sessionStatus(s.status)}
        {@const cs = containerState(s.container_state)}
        <li class:on={s.id === activeId}>
          <a href={`/work/${s.id}`} aria-current={s.id === activeId ? 'page' : undefined}>
            <span class="t">{s.title || 'Untitled session'}</span>
            <span class="m">
              <span class="chip {st.tone === 'plain' ? '' : st.tone}">{st.label}</span>
              <span class="muted tiny"><time datetime={s.last_active_at} title={new Date(s.last_active_at).toLocaleString()}>{relativeTime(s.last_active_at, now)}</time> · {cs.label.toLowerCase()}</span>
            </span>
          </a>
          <button class="btn ghost icon sm del" aria-label={`Delete session ${s.title || 'Untitled session'}`} onclick={() => ondelete(s)}><Icon name="trash" size={15} /></button>
        </li>
      {/each}
    </ul>
  {/if}
</nav>

<style>
  nav { min-height: 0; }
  ul { list-style: none; margin: 0; padding: 0 var(--s2); display: grid; gap: 2px; }
  li { position: relative; border-radius: var(--radius-sm); }
  li:hover, li:focus-within { background: var(--hover); }
  li.on { background: var(--selected); }
  a { display: grid; gap: 4px; padding: 8px 40px 8px 10px; color: var(--fg); text-decoration: none; border-radius: var(--radius-sm); min-width: 0; }
  .t { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .m { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; }
  .del { position: absolute; right: 4px; top: 6px; opacity: 0; }
  li:hover .del, li:focus-within .del, .del:focus-visible { opacity: 1; }
  @media (pointer: coarse) { .del { opacity: 1; } }
  .pad { padding: 8px var(--s3); } .pad-m { margin: var(--s2); }
</style>
