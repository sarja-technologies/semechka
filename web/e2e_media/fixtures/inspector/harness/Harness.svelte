<script lang="ts">
  // ?view=inspector|graph|compact|both  &job=j_run  &mode=drawer|inline  &final=<asset id>
  import JobInspector from '$lib/components/studio/inspector/JobInspector.svelte';
  import ComfyGraphView from '$lib/components/studio/inspector/ComfyGraphView.svelte';
  import { focusedNode } from '$lib/studio/inspector/focus.svelte';
  const q = new URLSearchParams(location.search);
  const view = q.get('view') ?? 'inspector';
  const job = q.get('job') ?? 'j_run';
  const mode = (q.get('mode') ?? (view === 'both' ? 'inline' : 'drawer')) as 'drawer' | 'inline';
  const final = q.get('final') ?? undefined;
  let open = $state(true);
  let openGraph = $state(true);
  const log: string[] = ((window as any).__log = []);
  const note = (s: string) => log.push(s);
</script>

<main>
  <p class="top"><output id="focus" data-testid="focus">{focusedNode.id ?? ''}</output>
    <button class="btn sm" id="reopen" onclick={() => { open = true; openGraph = true; }}>Reopen</button></p>
  {#if view === 'compact'}
    <div style="max-width: 560px"><JobInspector jobId={job} compact /></div>
  {:else if view === 'both'}
    <div class="two">
      {#if openGraph}<div class="cell"><ComfyGraphView jobId={job} mode="inline" onclose={() => (openGraph = false)} onnode={(id) => note(`graph-node:${id}`)} /></div>{/if}
      {#if open}<div class="cell"><JobInspector jobId={job} mode="inline" onclose={() => (open = false)} onnode={(id) => note(`node:${id}`)} onshowgraph={(id) => note(`show:${id}`)} /></div>{/if}
    </div>
  {:else if view === 'graph'}
    {#if open}<ComfyGraphView jobId={job} {mode} onclose={() => { open = false; note('closed'); }} onnode={(id) => note(`graph-node:${id}`)} />{/if}
  {:else}
    {#if open}<JobInspector jobId={job} {mode} finalAssetId={final} onopenfinal={() => note('openfinal')} onclose={() => { open = false; note('closed'); }} onnode={(id) => note(`node:${id}`)} onshowgraph={(id) => note(`show:${id}`)} />{/if}
  {/if}
</main>

<style>
  main { min-height: 100dvh; padding: 8px; }
  .top { margin: 0 0 8px; font-size: 12px; color: var(--fg-muted); }
  .two { display: grid; grid-template-columns: minmax(0, 1.6fr) minmax(0, 1fr); gap: 8px; height: calc(100dvh - 48px); }
  .cell { min-height: 0; height: 100%; } 
  @media (max-width: 800px) { .two { grid-template-columns: 1fr; height: auto; } .cell { height: 80dvh; } }
</style>
