<script lang="ts">
  // The conversation and what the session did, in order. Built only from events the server sent.
  import type { Item } from '$lib/work/events';
  import { relativeTime, sessionStatus } from '$lib/work/format';
  import Markdown from '$lib/components/Markdown.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import ToolItem from './ToolItem.svelte';
  import JobCard from './JobCard.svelte';
  import ArtifactCard from './ArtifactCard.svelte';
  import ApprovalCard from './ApprovalCard.svelte';

  let { items, sessionId, now, modelName = 'Assistant', saved = {}, onanswered, onsaved }: { items: Item[]; sessionId: string; now: number; modelName?: string; saved?: Record<string, boolean>; onanswered: () => void; onsaved?: (id: string) => void } = $props();
  let openDelegates = $state<Record<string, boolean>>({});
  const time = (iso: string) => relativeTime(iso, now);
</script>

<ol class="tl" aria-label="Conversation and activity">
  {#each items as it (it.key)}
    <li class="it t-{it.type}" data-key={it.key}>
      {#if it.type === 'message'}
        {#if it.data.role === 'user'}
          <div class="user"><div class="bubble">{it.data.content}</div><time class="tiny muted" datetime={it.at} title={new Date(it.at).toLocaleString()}>You · {time(it.at)}</time></div>
        {:else if it.data.role === 'system'}
          <p class="sys small muted">{it.data.content}</p>
        {:else}
          <div class="asst"><div class="who tiny muted"><Icon name="robot" size={14} /> {modelName} <time datetime={it.at} title={new Date(it.at).toLocaleString()}>· {time(it.at)}</time></div><Markdown text={it.data.content} /></div>
        {/if}
      {:else if it.type === 'tool'}
        <ToolItem item={it} />
      {:else if it.type === 'job'}
        <JobCard job={{ id: it.data.job_id, kind: it.data.job_kind, model: it.data.model, status: it.data.status, phase: it.data.phase, progress: it.data.progress, error: it.data.error }} />
      {:else if it.type === 'delegate'}
        {@const d = it.data}
        <div class="deleg">
          <button class="dh" aria-expanded={!!openDelegates[it.key]} onclick={() => (openDelegates[it.key] = !openDelegates[it.key])}>
            <span class="chev" class:open={openDelegates[it.key]}><Icon name="chevron" size={14} /></span>
            <Icon name="agents" size={15} />
            <span>Asked <strong>{d.model}</strong> for help</span>
            <span class="spacer"></span>
            <span class="chip {d.status === 'done' ? 'ok' : d.status === 'failed' ? 'bad' : 'info'}">{d.status === 'done' ? 'Done' : d.status === 'failed' ? 'Failed' : 'Working'}</span>
          </button>
          {#if d.status === 'running' && d.note}<p class="tiny muted wait" role="status">{d.note}</p>{/if}
          {#if openDelegates[it.key]}
            <div class="db"><p class="lbl">Task</p><p class="small">{d.task}</p>
              {#if d.result}<p class="lbl">Answer</p><Markdown text={d.result} />{/if}
              {#if d.error}<p class="err small">{d.error}</p>{/if}
              {#if d.depth}<p class="tiny muted">Delegation depth {d.depth}</p>{/if}</div>
          {/if}
        </div>
      {:else if it.type === 'artifact'}
        <ArtifactCard a={{ id: it.data.artifact_id, name: it.data.name, mime: it.data.mime, size: it.data.size, saved_to_library: saved[it.data.artifact_id] }} {onsaved} />
      {:else if it.type === 'approval'}
        <ApprovalCard item={it} {sessionId} {onanswered} />
      {:else if it.type === 'state'}
        <p class="state tiny muted"><span class="line"></span><span>{sessionStatus(it.data.status).label}{it.data.note ? `: ${it.data.note}` : ''} · {time(it.at)}</span><span class="line"></span></p>
      {:else if it.type === 'error'}
        <div class="notice bad errn" role="alert"><Icon name="warn" size={16} /><div><strong>Something went wrong.</strong> {it.data.message}{#if it.data.code}<span class="tiny muted mono"> ({it.data.code})</span>{/if}</div></div>
      {/if}
    </li>
  {/each}
</ol>

<style>
  .tl { list-style: none; margin: 0; padding: 0; display: grid; gap: var(--s3); }
  .it { min-width: 0; }
  .user { display: grid; justify-items: end; gap: 2px; }
  .bubble { background: var(--user-msg-bg); padding: 8px 14px; border-radius: var(--radius-composer); max-width: min(85%, 640px); white-space: pre-wrap; overflow-wrap: anywhere; }
  .asst { display: grid; gap: 4px; }
  .who { display: flex; align-items: center; gap: 6px; }
  .sys { margin: 0; text-align: center; }
  .state { display: flex; align-items: center; gap: 10px; margin: 4px 0; } .state .line { flex: 1; border-top: 1px solid var(--border); }
  .errn { margin: 0; align-items: flex-start; }
  .deleg { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
  .dh { all: unset; box-sizing: border-box; width: 100%; display: flex; align-items: center; gap: 8px; padding: 8px 10px; cursor: pointer; min-height: 36px; border-radius: var(--radius); }
  .dh:hover { background: var(--hover); } .dh:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .chev { display: inline-flex; transition: transform var(--fast) var(--ease); color: var(--fg-muted); } .chev.open { transform: rotate(90deg); }
  .wait { margin: 0; padding: 0 12px 8px; }
  .db { padding: 0 12px 12px; } .lbl { margin: 8px 0 2px; font-size: 12px; font-weight: 600; color: var(--fg-muted); }
</style>
