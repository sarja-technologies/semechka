<script lang="ts">
  // The scrolling conversation. Only the newest slice of a long thread is in the page; earlier messages load when the
  // reader scrolls up (keeping their place). While a reply arrives the view follows it, unless the reader scrolled away.
  import { onMount, tick, type Snippet } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import StreamText from '$lib/components/StreamText.svelte';
  import Sources from '$lib/components/Sources.svelte';
  import Message from './Message.svelte';
  import ModelIcon from './ModelIcon.svelte';
  import type { Thread, Msg, MsgActions } from '$lib/chat/thread.svelte';

  let { thread, labelFor, act, canBranch, techOn, showSources, selected = null, empty }:
    { thread: Thread; labelFor: (m: { model: string | null }) => string; act: MsgActions; canBranch: boolean; techOn: boolean; showSources: boolean;
      selected?: string | null; empty: Snippet } = $props();

  let scroller: HTMLDivElement | undefined = $state(), inner: HTMLDivElement | undefined = $state(), top: HTMLDivElement | undefined = $state();
  let stick = true, away = $state(false);
  const live = $derived(thread.live);
  const shown = $derived.by<Msg[]>(() => {
    const l = thread.live;
    if (!l || !l.anchor || l.mode === 'send') return thread.messages;
    const i = thread.messages.findIndex((m) => m.id === l.anchor);
    return i >= 0 ? thread.messages.slice(0, i) : thread.messages;
  });
  const lastAssistant = $derived([...thread.messages].reverse().find((m) => m.role === 'assistant'));

  function atBottom() { return !scroller || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 64; }
  function onScroll() { stick = atBottom(); away = !stick && (thread.messages.length > 0 || !!live); }
  export function toBottom(smooth = false) {
    if (!scroller) return;
    stick = true; away = false;
    scroller.scrollTo({ top: scroller.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
  }

  // Growth (a streamed block, a picture finishing, an image loading) keeps the view pinned to the end if it was there.
  onMount(() => {
    const ro = new ResizeObserver(() => { if (stick && scroller) scroller.scrollTop = scroller.scrollHeight; });
    if (inner) ro.observe(inner);
    const io = new IntersectionObserver((es) => { if (es.some((e) => e.isIntersecting)) older(); }, { root: scroller, rootMargin: '400px 0px 0px 0px' });
    if (top) io.observe(top);
    return () => { ro.disconnect(); io.disconnect(); };
  });

  // A newly opened conversation starts at its end.
  let seen = '';
  $effect(() => {
    const key = `${thread.id}:${thread.loading}`;
    if (key !== seen && !thread.loading) { seen = key; tick().then(() => toBottom()); }
  });
  // Sending always brings the end into view.
  $effect(() => { if (live && live.status === 'sending') tick().then(() => toBottom()); });

  async function older() {
    if (!scroller || !thread.hasMore || thread.loadingOlder) return;
    const h0 = scroller.scrollHeight, t0 = scroller.scrollTop;
    stick = false;
    const n = await thread.loadOlder();
    if (!n) return;
    await tick();
    scroller.scrollTop = t0 + (scroller.scrollHeight - h0); // the same message stays under the reader's eyes
  }
  const statusText = $derived(live ? (live.status === 'stopping' ? 'Stopping' : live.queue ? `In queue, position ${live.queue.position}` : live.status === 'sending' ? 'Sending' : live.text ? 'Assistant is writing' : 'Assistant is replying') : thread.outcome);
  const liveWord = $derived(!live ? '' : live.queue ? `In queue: position ${live.queue.position}` : live.status === 'sending' ? 'Waiting for the model' : live.status === 'stopping' ? 'Stopping' : live.text ? '' : live.reasoning ? 'Thinking' : 'Waiting for the first words');
</script>

<!-- One announcement per reply, never per token: screen readers hear that a reply started and how it ended. -->
<div class="tv">
<div class="sr-only" role="status" aria-live="polite" data-chat-status>{statusText}</div>
<div class="scroll" bind:this={scroller} onscroll={onScroll} data-chat-scroll data-total={thread.total} tabindex="-1" aria-label="Messages">
  <div class="col" bind:this={inner}>
    <div bind:this={top} class="top" aria-hidden={!thread.hasMore}>
      {#if thread.hasMore}
        <button class="btn ghost sm" onclick={older} disabled={thread.loadingOlder}>{thread.loadingOlder ? 'Loading earlier messages…' : `Show earlier messages (${thread.total - thread.messages.length} more)`}</button>
      {/if}
    </div>

    {#if thread.loading}<p class="muted" role="status">Loading conversation…</p>
    {:else if thread.error}<div class="notice bad" role="alert"><div>{thread.error} <a href="/chat">Start a new conversation</a></div></div>
    {:else if !shown.length && !live}{@render empty()}{/if}

    {#each shown as m (m.id)}
      <Message {m} label={labelFor(m)} busy={thread.busy} {canBranch} canRegenerate={canBranch && (!m.local || m.id === lastAssistant?.id)} {techOn} {showSources}
        selected={selected === m.id} {act} />
    {/each}

    {#if live}
      {#if live.mode !== 'regenerate' && (live.userText || live.userAttachments.length)}
        <article class="msg user pending" aria-label="You">
          <div class="body">
            {#if live.userAttachments.length}<div class="tiny muted">{live.userAttachments.length} attachment{live.userAttachments.length === 1 ? '' : 's'}</div>{/if}
            {#if live.userText}<div class="bubble">{live.userText}</div>{/if}
          </div>
        </article>
      {/if}
      <article class="msg assistant" aria-label={live.model} aria-busy="true">
        <div class="who">
          <ModelIcon icon={act.icon(null)} label={live.model} size={20} /><span>{labelFor({ model: live.model })}</span>
          {#if liveWord}<span class="state"><span class="dot" aria-hidden="true"></span>{liveWord}</span>{/if}
        </div>
        {#if live.queue}<p class="small muted">{live.queue.running} of {live.queue.limit} reply slot{live.queue.limit === 1 ? '' : 's'} in use; {live.queue.waiting} waiting. Your request starts when it reaches the front. Stop leaves the queue.</p>{/if}
        {#if showSources && (live.sources?.length || live.skipped)}<Sources sources={live.sources ?? []} skipped={live.skipped ?? 0} />{/if}
        {#if live.trimmed}<p class="tiny muted"><Icon name="info" size={13} /> The {live.trimmed} oldest message{live.trimmed === 1 ? '' : 's'} did not fit in this model's context window and {live.trimmed === 1 ? 'is' : 'are'} left out of this reply.</p>{/if}
        {#if live.reasoning}<details class="think"><summary class="small muted">Thinking</summary><pre>{live.reasoning}</pre></details>{/if}
        {#if live.text}<StreamText text={live.text} />{/if}
      </article>
    {/if}
    {#if thread.sendError}<div class="notice bad" role="alert"><div>{thread.sendError}</div></div>{/if}
  </div>
</div>
{#if away}
  <button class="btn sm jump" onclick={() => toBottom(true)} aria-label="Jump to the latest message"><Icon name="chevdown" size={15} /> Latest</button>
{/if}
</div>

<style>
  .tv { flex: 1; min-height: 0; display: flex; flex-direction: column; position: relative; }
  .scroll { flex: 1; overflow-y: auto; min-height: 0; overscroll-behavior: contain; position: relative; outline: none; }
  .col { max-width: var(--msg-w); margin: 0 auto; padding: 16px 24px 24px; width: 100%; }
  .top { min-height: 1px; display: flex; justify-content: center; margin-bottom: 8px; }
  .msg { margin: 0 0 20px; display: flex; flex-direction: column; }
  .msg.user { align-items: flex-start; }
  :global([data-align='right']) .msg.user { align-items: flex-end; }
  .msg.user .body { max-width: min(85%, 72ch); display: grid; gap: 4px; }
  .bubble { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--user-msg-bg); border-radius: var(--radius); padding: 8px 14px; }
  .pending { opacity: 0.8; }
  .who { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; font-weight: 600; }
  .state { display: inline-flex; align-items: center; gap: 6px; font-weight: 400; color: var(--fg-muted); }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }
  @media (prefers-reduced-motion: no-preference) { .dot { animation: pulse 1.1s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.25; } } }
  .think pre { white-space: pre-wrap; font: 12px/1.5 var(--mono); color: var(--fg-muted); margin: 4px 0 8px; border-left: 2px solid var(--border-strong); padding-left: 10px; max-height: 240px; overflow: auto; }
  .jump { position: absolute; left: 50%; transform: translateX(-50%); bottom: 12px; box-shadow: var(--shadow); border-radius: 999px; z-index: 5; }
  @media (max-width: 719px) { .col { padding: 12px 12px 16px; } }
</style>
