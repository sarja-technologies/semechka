<script lang="ts">
  // Everything recorded about one generation (or one stored item): who, which model, when, status and error, the prompt and settings, the inputs
  // that were handed to the engine (source, mask, references) and the results, original and final where both are kept.
  import { tick } from 'svelte';
  import { fmtTime } from './util';
  import { fmtDur, fmtSize, paramRows, roleLabel, statusTone, STATUS_LABEL, workflowLabel, type FileRef } from '$lib/admin/explorer';
  import MediaThumb from './MediaThumb.svelte';
  let { detail, onclose, onfile, onconversation }: { detail: any; onclose: () => void; onfile: (f: FileRef) => void; onconversation?: (id: string) => void } = $props();
  let panel: HTMLElement | undefined = $state();
  $effect(() => { detail?.id; tick().then(() => panel?.focus()); });
  const rows = $derived(paramRows(detail?.params));
  const wf = $derived(detail?.workflow_detail ? paramRows(Object.fromEntries(Object.entries(detail.workflow_detail).filter(([k]) => k !== 'source'))) : []);
  const ref = (a: any, label: string): FileRef => ({ type: a.type, id: a.id, kind: a.kind, mime: a.mime, size: a.size, name: a.name, label });
</script>
<svelte:window onkeydown={(e) => { if (e.key === 'Escape' && detail) onclose(); }} />
{#if detail}
  <div class="scrim" role="presentation" onclick={onclose}></div>
  <div class="dr" role="dialog" aria-modal="true" aria-label="Generation record" tabindex="-1" bind:this={panel}>
    <header>
      <div class="grow">
        <h2>{workflowLabel(detail.workflow)}{detail.operation && detail.operation !== detail.workflow && detail.operation !== 'generate' ? ` · ${detail.operation}` : ''}</h2>
        <div class="row" style="gap:8px;margin-top:4px"><span class="chip {statusTone(detail.status)}">{STATUS_LABEL[detail.status] ?? detail.status}</span>
          <span class="small muted">{detail.kind} · <span class="mono">{detail.id}</span></span></div>
      </div>
      <button class="btn ghost icon sm" aria-label="Close" onclick={onclose}>×</button>
    </header>
    <div class="scroll">
      {#if detail.note}<div class="notice warn" role="status"><div>{detail.note}</div></div>{/if}
      {#if detail.error}<div class="notice bad" role="alert"><div><strong>Error</strong><br />{detail.error}</div></div>{/if}

      <section aria-labelledby="d-out"><h3 id="d-out">Results</h3>
        {#if detail.outputs?.length}
          <ul class="grid">{#each detail.outputs as o (o.id)}
            <li class="card">
              <div class="pv"><MediaThumb url={o.thumb_url} kind={o.kind} fileState={o.state} fit="contain" /></div>
              <p class="tiny" style="margin:6px 0">{o.mime}{o.width ? ` · ${o.width} × ${o.height}` : ''}{o.duration ? ` · ${fmtDur(o.duration)}` : ''} · {fmtSize(o.size)}{o.state !== 'available' ? ` · ${o.state === 'expired' ? 'expired' : 'file missing'}` : ''}</p>
              <div class="row"><button class="btn sm" disabled={!o.available} onclick={() => onfile(ref(o, `${o.kind === 'video' ? 'Video' : 'Picture'} result`))}>Preview or download</button></div>
            </li>{/each}</ul>
        {:else}<p class="small muted">{detail.status === 'failed' ? 'The generation failed and produced no file.' : detail.status === 'cancelled' ? 'The generation was cancelled before a file was kept.' : detail.status === 'queued' || detail.status === 'running' ? 'Not finished yet.' : 'No file is stored.'}</p>{/if}
        {#if detail.original}
          <h4>Original</h4>
          <div class="row"><div class="mini"><MediaThumb url={detail.original.thumb_url} kind={detail.original.kind} fileState={detail.original.state} /></div>
            <button class="btn sm" disabled={!detail.original.available} onclick={() => onfile(ref(detail.original, 'Original'))}>Preview original</button></div>
        {/if}
        {#if detail.derived?.length}
          <h4>Final versions made from the result</h4>
          <ul class="grid small">{#each detail.derived as o (o.id)}<li class="card"><div class="pv sm"><MediaThumb url={o.thumb_url} kind={o.kind} fileState={o.state} fit="contain" /></div>
            <p class="tiny" style="margin:4px 0">{o.origin}{o.width ? ` · ${o.width} × ${o.height}` : ''}</p><button class="btn sm" disabled={!o.available} onclick={() => onfile(ref(o, `${o.origin} copy`))}>Preview</button></li>{/each}</ul>
        {/if}
      </section>

      {#if detail.prompt}<section aria-labelledby="d-pr"><h3 id="d-pr">Prompt</h3><p class="prompt">{detail.prompt}</p></section>{/if}

      {#if detail.inputs?.length}
        <section aria-labelledby="d-in"><h3 id="d-in">Inputs sent to the model</h3>
          <ul class="grid">{#each detail.inputs as a (a.role + a.id)}
            <li class="card"><div class="pv sm"><MediaThumb url={a.thumb_url} kind={a.kind ?? 'image'} fileState={a.available ? 'available' : a.state} fit="contain" /></div>
              <p class="tiny" style="margin:4px 0"><strong>{roleLabel(a.role)}</strong>{a.name ? ` · ${a.name}` : ''}{a.state && a.state !== 'available' ? ` · ${a.state === 'expired' ? 'expired' : 'not retained'}` : ''}</p>
              <button class="btn sm" disabled={!a.available} onclick={() => onfile(ref(a, roleLabel(a.role)))}>Preview</button></li>{/each}</ul>
        </section>
      {/if}

      {#if !detail.inputs?.length && detail.inputs_note}<section aria-labelledby="d-in2"><h3 id="d-in2">Inputs sent to the model</h3><p class="small muted">{detail.inputs_note}</p></section>{/if}

      <section aria-labelledby="d-set"><h3 id="d-set">Settings</h3>
        {#if rows.length || wf.length}
          <dl class="kv">{#each rows as [k, v] (k)}<dt>{k}</dt><dd>{v}</dd>{/each}</dl>
          {#if wf.length}<h4>{workflowLabel(detail.workflow)} details</h4><dl class="kv">{#each wf as [k, v] (k)}<dt>{k}</dt><dd class="mono">{v}</dd>{/each}</dl>{/if}
        {:else}<p class="small muted">No settings are stored for this item.</p>{/if}
      </section>

      <section aria-labelledby="d-meta"><h3 id="d-meta">Record</h3>
        <dl class="kv">
          <dt>Person</dt><dd>{detail.owner.display_name} &lt;{detail.owner.email}&gt;</dd>
          <dt>Model</dt><dd>{detail.model?.alias ?? '—'}{detail.backend ? ` on ${detail.backend}` : ''}</dd>
          <dt>Created</dt><dd>{fmtTime(detail.created_at)}</dd>
          {#if detail.started_at}<dt>Started</dt><dd>{fmtTime(detail.started_at)}</dd>{/if}
          {#if detail.finished_at}<dt>Finished</dt><dd>{fmtTime(detail.finished_at)}{detail.duration_s != null ? ` (${fmtDur(detail.duration_s)})` : ''}</dd>{/if}
          <dt>Kept until</dt><dd>{detail.expires_at ? fmtTime(detail.expires_at) : 'No expiry set'}</dd>
          <dt>Saved</dt><dd>{detail.saved ? 'Yes' : 'No-save generation'}</dd>
          {#if detail.conversation_id}<dt>Conversation</dt><dd>{#if onconversation}<button class="linkish" onclick={() => onconversation(detail.conversation_id)}>Show in Conversations</button>{:else}<span class="mono">{detail.conversation_id}</span>{/if}</dd>{/if}
        </dl>
      </section>
    </div>
  </div>
{/if}
<style>
  .scrim { position: fixed; inset: 0; background: rgba(10, 12, 12, 0.4); z-index: 60; }
  .dr { position: fixed; z-index: 61; top: 0; right: 0; bottom: 0; width: min(560px, 100vw); background: var(--surface); border-left: 1px solid var(--border-strong); box-shadow: var(--shadow); display: flex; flex-direction: column; outline: none; }
  header { display: flex; gap: 12px; align-items: flex-start; padding: 14px 16px; border-bottom: 1px solid var(--border); }
  h2 { font-size: 17px; }
  .scroll { overflow: auto; padding: 16px; display: grid; gap: 20px; align-content: start; }
  section h3 { font-size: 13px; text-transform: uppercase; letter-spacing: .06em; color: var(--fg-muted); font-weight: 600; margin-bottom: 8px; }
  h4 { font-size: 13px; margin: 12px 0 6px; }
  .grid { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); }
  .grid:not(.small) { grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); }
  .card { border: 1px solid var(--border); border-radius: var(--radius); padding: 8px; background: var(--surface-2); }
  .pv { aspect-ratio: 4 / 3; border-radius: var(--radius-sm); overflow: hidden; } .pv.sm { aspect-ratio: 1 / 1; }
  .mini { width: 72px; height: 54px; border-radius: 6px; overflow: hidden; }
  .prompt { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--surface-2); border-radius: var(--radius-sm); padding: 10px 12px; margin: 0; }
  .kv { display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 16px; margin: 0; font-size: 13px; }
  .kv dt { color: var(--fg-muted); } .kv dd { margin: 0; overflow-wrap: anywhere; }
  @media (max-width: 520px) { .kv { grid-template-columns: minmax(0, 1fr); } .kv dd { margin-bottom: 6px; } }
</style>
