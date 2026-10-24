<script lang="ts">
  // One message of the visible branch. Text replies render as Markdown; picture and video replies follow their job.
  import Icon from '$lib/components/Icon.svelte';
  import Markdown from '$lib/components/Markdown.svelte';
  import Sources from '$lib/components/Sources.svelte';
  import ModelIcon from './ModelIcon.svelte';
  import MediaCard from './MediaCard.svelte';
  import type { Msg, Attachment, MsgActions } from '$lib/chat/thread.svelte';

  let { m, label, busy, canBranch, canRegenerate, techOn, showSources, selected = false, act }:
    { m: Msg; label: string; busy: boolean; canBranch: boolean; canRegenerate: boolean; techOn: boolean; showSources: boolean; selected?: boolean; act: MsgActions } = $props();

  let editing = $state<string | null>(null);
  const user = $derived(m.role === 'user');
  const media = $derived(m.kind === 'image' || m.kind === 'video');
  const op = $derived(user ? m.meta?.operation : undefined);
  const sib = $derived(m.siblings);
  const attUrl = (a: Attachment) => (a.id.startsWith('ma_') || a.id.startsWith('me_') ? `/api/media/assets/${a.id}/content` : `/api/chat/attachments/${a.id}/content`);
  const size = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
  function saveEdit() { const t = editing?.trim(); if (!t && !m.attachments?.length) return; editing = null; act.edit(m, t ?? ''); }
  function editKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); saveEdit(); }
    else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); editing = null; }
  }
</script>

<article class="msg {m.role}" class:sel={selected} data-msg={m.id} data-role={m.role} aria-label={user ? 'You' : label}>
  {#if !user}
    <div class="who">
      <ModelIcon icon={act.icon(m.model_id)} label={label} size={20} />
      <span class="nm">{label}</span>
      {#if m.status === 'stopped'}<span class="chip warn">Stopped</span>{/if}
      {#if m.status === 'error' && !media}<span class="chip bad">Failed</span>{/if}
    </div>
  {/if}

  <div class="body">
    {#if user && editing !== null}
      <div class="editbox">
        <label class="sr-only" for={`ed-${m.id}`}>Edit message</label>
        <!-- svelte-ignore a11y_autofocus -->
        <textarea id={`ed-${m.id}`} class="input" rows="3" bind:value={editing} onkeydown={editKey} autofocus></textarea>
        <div class="row" style="gap:8px">
          <button class="btn primary sm" disabled={busy || (!editing.trim() && !m.attachments?.length)} onclick={saveEdit}>Send</button>
          <button class="btn sm" onclick={() => (editing = null)}>Cancel</button>
          <span class="tiny muted">The original stays as an earlier version.</span>
        </div>
      </div>
    {:else if user}
      {#if m.attachments?.length}
        <div class="atts">
          {#each m.attachments as a (a.id)}
            {#if a.missing}<span class="chip">File removed</span>
            {:else if a.kind === 'image'}<a class="thumb" href={attUrl(a)} target="_blank" rel="noopener"><img src={attUrl(a)} alt={a.name} loading="lazy" /></a>
            {:else}<span class="file"><Icon name="log" size={16} /><span class="fn">{a.name}</span><span class="tiny muted">{size(a.size)}</span></span>{/if}
          {/each}
        </div>
      {/if}
      {#if m.content}<div class="bubble">{#if op === 'image' || op === 'video'}<span class="op tiny">{op === 'image' ? 'Picture' : 'Video'}</span>{/if}{m.content}</div>{/if}
    {:else if media}
      <MediaCard job={m.media} kind={m.kind as 'image' | 'video'} onchange={(j) => act.mediaChanged(m, j)} onuse={act.useSource} />
      {#if m.status === 'error' && m.meta?.error}<div class="notice bad" role="alert"><div>{m.meta.error}</div></div>{/if}
    {:else}
      {#if showSources && m.sources?.length}<Sources sources={m.sources} />{/if}
      {#if m.content}<Markdown text={m.content} />
      {:else}<p class="muted small">{m.status === 'error' ? 'The model did not return a reply.' : m.status === 'stopped' ? 'Stopped before any text arrived.' : 'No text was produced.'}</p>{/if}
      {#if m.meta?.trimmed}<p class="tiny muted note"><Icon name="info" size={13} /> The {m.meta.trimmed} oldest message{m.meta.trimmed === 1 ? '' : 's'} did not fit in this model's context window and {m.meta.trimmed === 1 ? 'was' : 'were'} left out of this reply.</p>{/if}
    {/if}
  </div>

  {#if editing === null}
    <div class="acts" class:right={user}>
      {#if sib}
        <span class="ver" aria-label={`Version ${sib.index + 1} of ${sib.count}`}>
          <button class="btn ghost icon sm" aria-label="Previous version" disabled={!sib.prev || busy} onclick={() => sib.prev && act.version(sib.prev)}><span class="flip"><Icon name="chevron" size={14} /></span></button>
          <span class="tiny mono">{sib.index + 1}/{sib.count}</span>
          <button class="btn ghost icon sm" aria-label="Next version" disabled={!sib.next || busy} onclick={() => sib.next && act.version(sib.next)}><Icon name="chevron" size={14} /></button>
        </span>
      {/if}
      {#if !busy}
        {#if m.content && !media}<button class="btn ghost icon sm" title="Copy" aria-label="Copy" onclick={() => act.copy(m)}><Icon name="copy" size={15} /></button>{/if}
        {#if user && canBranch}<button class="btn ghost icon sm" title="Edit" aria-label="Edit and resend" onclick={() => (editing = m.content)}><Icon name="edit" size={15} /></button>{/if}
        {#if !user && canRegenerate}<button class="btn ghost icon sm" title={media ? 'Make another' : 'Regenerate'} aria-label={media ? 'Make another version' : 'Regenerate reply'} onclick={() => act.regenerate(m)}><Icon name="refresh" size={15} /></button>{/if}
        {#if !user && techOn && act.details && Object.keys(m.tech ?? {}).length}<button class="btn ghost icon sm" title="Technical details" aria-label="Technical details" aria-pressed={selected} onclick={() => act.details?.(m)}><Icon name="cpu" size={15} /></button>{/if}
        {#if !user && techOn && m.tech?.tokens_per_s}<span class="chip mono tiny">{m.tech.tokens_per_s} tok/s</span>{/if}
      {/if}
    </div>
  {/if}
</article>

<style>
  /* content-visibility lets the browser skip layout and paint for messages far outside the viewport */
  .msg { margin: 0 0 20px; content-visibility: auto; contain-intrinsic-size: auto 120px; display: flex; flex-direction: column; }
  .who { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; font-weight: 600; flex-wrap: wrap; }
  .nm { color: var(--fg); }
  .body { min-width: 0; }
  .assistant .body { background: var(--assistant-msg-bg); }
  .bubble { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--user-msg-bg); color: var(--fg); border-radius: var(--radius); padding: 8px 14px; max-width: min(72ch, 100%); }
  .op { display: block; font-weight: 600; color: var(--fg-muted); margin-bottom: 2px; }
  .user { align-items: flex-start; }
  :global([data-align='right']) .user { align-items: flex-end; }
  :global([data-align='right']) .user .atts { justify-content: flex-end; }
  .user .body { max-width: min(85%, 72ch); display: grid; gap: 6px; justify-items: inherit; }
  :global([data-align='right']) .user .body { justify-items: end; }
  .editbox { display: grid; gap: 8px; width: min(640px, 85vw); }
  .atts { display: flex; flex-wrap: wrap; gap: 6px; }
  .thumb { display: block; border-radius: var(--radius-sm); overflow: hidden; border: 1px solid var(--border); }
  .thumb img { display: block; max-width: 220px; max-height: 180px; object-fit: cover; }
  .file { display: inline-flex; align-items: center; gap: 6px; padding: 6px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); background: var(--card-bg); font-size: 13px; max-width: 280px; }
  .fn { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .note { display: flex; gap: 6px; align-items: center; margin: 8px 0 0; }
  .acts { display: flex; align-items: center; gap: 2px; margin-top: 4px; min-height: 28px; flex-wrap: wrap; }
  .acts.right { justify-content: flex-end; }
  :global([data-align='left']) .acts.right { justify-content: flex-start; }
  /* actions are quiet until the message is hovered or focused; touch screens always show them */
  @media (hover: hover) and (pointer: fine) {
    .acts > .btn { opacity: 0; transition: opacity 100ms ease-out; }
    .msg:hover .acts > .btn, .msg:focus-within .acts > .btn, .msg.sel .acts > .btn { opacity: 1; }
  }
  .ver { display: inline-flex; align-items: center; gap: 2px; color: var(--fg-muted); }
  .flip { display: grid; transform: rotate(180deg); }
  .msg.sel .body { outline: 2px solid var(--border-strong); outline-offset: 6px; border-radius: 2px; }
</style>
