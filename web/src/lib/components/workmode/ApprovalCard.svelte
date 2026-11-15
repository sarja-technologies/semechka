<script lang="ts">
  // The agent is waiting for a decision. Nothing runs until it is answered; unanswered it is denied when it expires (the server says when).
  import { onMount } from 'svelte';
  import type { Item, Decision } from '$lib/workmode/types';
  let { a, ondecide, disabled = false }: { a: Extract<Item, { k: 'approval' }>; ondecide: (id: string, d: Decision) => void; disabled?: boolean } = $props();
  const ap = $derived(a.approval);
  const input = $derived(ap.input ? Object.entries(ap.input).map(([k, v]) => `${k}: ${typeof v === 'string' ? v : JSON.stringify(v)}`).join('\n') : '');
  const text: Record<string, string> = { allow_once: 'Allowed once', allow_always: 'Always allowed', deny: 'Denied', deny_always: 'Always denied', cancelled: 'Cancelled', timeout: 'Denied: nobody answered in time' };
  const can = (d: Decision) => !ap.options?.length || ap.options.includes(d);
  let now = $state(Date.now() / 1000);
  onMount(() => { const t = setInterval(() => (now = Date.now() / 1000), 1000); return () => clearInterval(t); });
  const left = $derived(ap.expires_at ? Math.max(0, Math.round(ap.expires_at - now)) : null);
  const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
  let busy = $state(false);
  async function go(d: Decision) { busy = true; try { await ondecide(ap.id, d); } finally { busy = false; } }
</script>
{#if a.decision}
  <p class="done" data-approval={ap.id} data-decision={a.decision}>{#if !ap.title.startsWith(ap.tool)}<span class="tool">{ap.tool}</span>{/if} <span class="ttl">{ap.title}</span> <span class="res" class:bad={a.decision.startsWith('deny') || a.decision === 'timeout'}>{text[a.decision] ?? a.decision}</span></p>
{:else}
  <section class="ask" role="alert" aria-labelledby={`ap-${ap.id}`} data-approval={ap.id}>
    <h3 id={`ap-${ap.id}`}>The agent asks to use <span class="tool">{ap.tool}</span>{#if ap.extension}&nbsp;<span class="muted">({ap.extension})</span>{/if}</h3>
    <pre class="what"><code>{ap.title}{#if input && input !== ap.title}{'\n'}{input}{/if}</code></pre>
    <div class="acts">
      {#if can('allow_once')}<button class="btn primary" disabled={busy || disabled} onclick={() => go('allow_once')}>Allow once</button>{/if}
      {#if can('allow_always')}<button class="btn" disabled={busy || disabled} onclick={() => go('allow_always')} title="Allow this tool for the rest of this session">Always allow</button>{/if}
      {#if can('deny')}<button class="btn" disabled={busy || disabled} onclick={() => go('deny')}>Deny</button>{/if}
      {#if can('deny_always')}<button class="btn ghost sm" disabled={busy || disabled} onclick={() => go('deny_always')} title="Never allow this tool in this session">Always deny</button>{/if}
    </div>
    <p class="tiny muted">{left !== null ? `The agent waits for you. If nobody answers in ${mmss(left)} it is denied.` : 'The agent waits for you.'} Allow always and Always deny apply to this tool for this session only.</p>
  </section>
{/if}
<style>
  .ask { border: 1px solid color-mix(in srgb, var(--warn) 45%, var(--border)); background: var(--warn-soft); border-radius: var(--radius); padding: 12px 14px; margin: 0 0 14px; display: grid; gap: 8px; }
  h3 { font-size: 14px; } .tool { font-family: var(--mono); }
  .what { margin: 0; background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 10px; font: 12.5px/1.5 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 200px; overflow: auto; }
  .acts { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; } .ask p { margin: 0; }
  .done { margin: 0 0 12px; font-size: 13px; color: var(--fg-muted); display: flex; gap: 8px; flex-wrap: wrap; align-items: baseline; }
  .done .ttl { font-family: var(--mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 48ch; }
  .res { font-weight: 600; color: var(--success); } .res.bad { color: var(--danger); }
</style>
