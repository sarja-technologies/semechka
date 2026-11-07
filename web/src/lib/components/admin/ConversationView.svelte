<script lang="ts">
  // One opened conversation: its messages with the generated pictures and videos, the reference pictures and the attachments where they belong.
  import { fmtTime } from './util';
  import { paramsLine, roleLabel, statusTone, STATUS_LABEL, workflowLabel, type FileRef } from '$lib/admin/explorer';
  import MediaThumb from './MediaThumb.svelte';
  let { conv, onjob, onfile }: { conv: any; onjob: (id: string) => void; onfile: (f: FileRef) => void } = $props();
  const who = (m: any) => (m.role === 'user' ? 'Person' : m.model || 'Assistant');
</script>
<div class="panel-h">
    <div class="grow"><h3>{conv.title || 'Untitled'}</h3>
      <p class="small muted" style="margin:2px 0 0">{conv.owner.display_name ?? conv.owner.email} &lt;{conv.owner.email}&gt; · {conv.mode}{conv.model ? ` · ${conv.model.alias}` : ''} · updated {fmtTime(conv.updated_at)}{conv.expires_at ? ` · kept until ${fmtTime(conv.expires_at)}` : ''}</p></div>
    <span class="chip warn">Recorded access</span>
  </div>
  <div class="panel-b">
    {#if conv.branches}<p class="hint" style="margin-top:0">Showing the current version of each reply; {conv.branches} earlier version{conv.branches === 1 ? ' is' : 's are'} not shown.</p>{/if}
    <ol class="msgs" aria-label="Messages">
      {#each conv.messages as m (m.id)}
        <li class={m.role}>
          <div class="meta tiny muted">{who(m)} · {fmtTime(m.created_at)}{m.status && m.status !== 'complete' ? ` · ${m.status}` : ''}</div>
          {#if m.content}<div class="body">{m.content}</div>{/if}
          {#if m.attachment_items?.length}
            <ul class="atts" aria-label="Attachments and reference pictures">
              {#each m.attachment_items as a (a.role + a.id)}
                <li><button class="att" onclick={() => onfile({ type: a.type, id: a.id, kind: a.kind, mime: a.mime, name: a.name, size: a.size, label: a.name })} disabled={!a.available} aria-label={`${roleLabel(a.role)}: ${a.name || a.id}${a.available ? '' : ` (${a.state})`}`}>
                  <span class="at"><MediaThumb url={a.thumb_url} kind={a.kind === 'image' ? 'image' : 'file'} fileState={a.available ? 'available' : a.state} /></span>
                  <span class="tiny"><strong>{roleLabel(a.role)}</strong><br />{a.name || a.id}</span></button></li>
              {/each}
            </ul>
          {/if}
          {#if m.media}
            {@const j = m.media}
            <div class="job" class:bad={j.status === 'failed'}>
              <div class="row" style="gap:8px">
                <span class="chip {statusTone(j.status)}">{STATUS_LABEL[j.status] ?? j.status}</span>
                <span class="small"><strong>{workflowLabel(j.workflow)}</strong> · {j.kind} · {j.model?.alias ?? 'unknown model'}</span>
                <span class="spacer"></span>
                <button class="btn sm" onclick={() => onjob(j.id)}>Open generation record</button>
              </div>
              {#if paramsLine(j.params)}<p class="tiny muted" style="margin:4px 0 0">{paramsLine(j.params)}</p>{/if}
              {#if j.error}<p class="small err">{j.error}</p>{/if}
              {#if j.outputs.length}
                <ul class="outs" aria-label="Results">
                  {#each j.outputs as o (o.id)}
                    <li><button class="out" onclick={() => onfile({ type: 'asset', id: o.id, kind: o.kind, mime: o.mime, size: o.size, label: `${o.kind === 'video' ? 'Video' : 'Picture'} ${o.width ?? ''}${o.width ? ' × ' + o.height : ''}` })} disabled={!o.available} aria-label={`Preview ${o.kind} result${o.available ? '' : ` (${o.state})`}`}>
                      <MediaThumb url={o.thumb_url} kind={o.kind} fileState={o.state} /></button></li>
                  {/each}
                </ul>
              {:else if j.status === 'completed'}<p class="small muted">The result is no longer stored.</p>
              {:else if j.status === 'queued' || j.status === 'running'}<p class="small muted">Still being generated.</p>{/if}
            </div>
          {/if}
        </li>
      {:else}<li class="small muted">This conversation has no messages.</li>{/each}
    </ol>
  </div>
<style>
  .msgs { list-style: none; margin: 0; padding: 0; display: grid; gap: 16px; }
  .msgs > li { border-left: 2px solid var(--border-strong); padding-left: 12px; min-width: 0; }
  .msgs > li.user { border-color: var(--accent); }
  .body { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 14px; }
  .atts, .outs { list-style: none; display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 0; padding: 0; }
  .att { all: unset; box-sizing: border-box; cursor: pointer; display: flex; gap: 8px; align-items: center; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 4px 8px 4px 4px; background: var(--surface); max-width: 260px; }
  .att:disabled { cursor: default; opacity: 0.7; }
  .att:hover:not(:disabled), .out:hover:not(:disabled) { border-color: var(--border-strong); }
  .att:focus-visible, .out:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .at { width: 44px; height: 44px; border-radius: 6px; overflow: hidden; flex: none; }
  .att .tiny { overflow-wrap: anywhere; }
  .job { margin-top: 8px; border: 1px solid var(--border); border-radius: var(--radius); padding: 10px 12px; background: var(--surface-2); }
  .job.bad { border-color: color-mix(in srgb, var(--danger) 40%, var(--border)); }
  .err { color: var(--danger); margin: 4px 0 0; overflow-wrap: anywhere; }
  .out { all: unset; box-sizing: border-box; cursor: pointer; display: block; width: 132px; height: 100px; border-radius: var(--radius-sm); overflow: hidden; border: 1px solid var(--border); }
  .out:disabled { cursor: default; }
</style>
