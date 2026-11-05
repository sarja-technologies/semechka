<script lang="ts">
  // Step 4: what to do, and to what. The modes and their status come from the engine. "In your video" (the target) and "Your pictures" (what it
  // becomes) are kept visibly apart: one is described and marked on the clip, the other is the reference gallery.
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import LiveJob from './LiveJob.svelte';
  import SettingsFields from './SettingsFields.svelte';
  import { MODE_BLURB, STATUS_LABEL, runnable, subjectSupport, needsSubject } from '$lib/studio/caps';
  import { SELECTION_FIELDS, BECOMES_FIELDS } from '$lib/studio/schema';
  import { KINDS } from '$lib/studio/spec';
  import type { Studio } from '$lib/studio/store.svelte';

  let { s }: { s: Studio } = $props();
  const modes = $derived(s.caps?.modes ?? []);
  const m = $derived(s.mode);
  const sub = $derived(s.spec.subject);
  const sup = $derived(subjectSupport(m, s.entry));
  const kind = $derived(KINDS.find((k) => k.id === sub.kind) ?? KINDS[0]);
  const tested = $derived((m?.subject_kinds ?? []) as string[]);
  const marks = $derived((sub.box ? 1 : 0) + sub.points.length);
  const usedRefs = $derived(s.spec.references.map((r, i) => ({ r, i, st: s.states[i] })).filter((x) => x.st.used));
  const status = $derived(m?.status);

  function pickMode(id: string, ok: boolean) { if (ok) s.chooseMode(id); }
  function clearMarks() { s.edit((sp) => { sp.subject.box = null; sp.subject.points = []; }); }
  function setKind(k: string) { s.edit((sp) => { sp.subject.kind = k as any; }); }
  const canCheck = $derived(s.checkable && !!s.project && (sub.text.trim().length > 0 || marks > 0) && !s.checking);
</script>

<div class="st-h"><h2>What do you want to do?</h2><p>Choose an operation. Each one shows whether it is ready, still experimental, or not possible right now.</p></div>

<div class="modes" role="radiogroup" aria-label="Operation" data-testid="modes">
  {#each modes as md (md.id)}
    {@const ok = runnable(md.status)}
    <button type="button" class="mode" role="radio" aria-checked={s.spec.mode === md.id} aria-disabled={!ok} class:off={!ok} class:on={s.spec.mode === md.id}
      data-mode={md.id} data-status={md.status} onclick={() => pickMode(md.id, ok)} aria-describedby={`mdesc-${md.id}`}>
      <span class="top"><strong>{md.label}</strong><span class="st-badge" class:ok={md.status === 'ready'} class:warn={md.status === 'experimental'} class:bad={md.status === 'unavailable' || md.status === 'not_ready'}>{STATUS_LABEL[md.status] ?? md.status}</span></span>
      <span class="desc" id={`mdesc-${md.id}`}>{MODE_BLURB[md.id] ?? ''}{#if md.reason && md.status !== 'ready'}<em> {md.reason}</em>{/if}</span>
    </button>
  {:else}
    <p class="st-note">{s.capsError || 'The server did not list any operations.'}</p>
  {/each}
</div>

{#if m && status === 'experimental'}
  <div class="notice warn exp" data-testid="experimental">
    <Icon name="warn" size={16} />
    <div>
      <strong>{m.label} is experimental.</strong> It has not been proven from start to finish on this machine yet, so the result may be poor or the run may fail.{#if m.reason} <span class="muted">Engine note: {m.reason}.</span>{/if}
      <label class="chk"><input type="checkbox" checked={s.spec.accept_experimental} onchange={(e) => s.edit((sp) => { sp.accept_experimental = e.currentTarget.checked; })} /> I understand, let me try it</label>
    </div>
  </div>
{/if}

{#if m}
  <div class="pair">
    {#if s.usesVideo}
      <section class="side" aria-label="In your video" data-testid="subject-section">
        <h3><Icon name="video" size={15} /> In your video</h3>
        <p class="st-note">What should change? Describe it in a few words.</p>
        <label class="st-field"><span>{needsSubject(m) ? 'Describe the subject' : 'Describe the subject (optional)'}</span>
          <input class="input" value={sub.text} maxlength="200" placeholder={`e.g. ${kind.example}`} aria-label="Subject description"
            oninput={(e) => s.edit((sp) => { sp.subject.text = e.currentTarget.value; })} /></label>
        <div class="st-seg" role="radiogroup" aria-label="Kind of subject">
          {#each KINDS as k (k.id)}<button type="button" role="radio" aria-checked={sub.kind === k.id} onclick={() => setKind(k.id)} title={tested.length && !tested.includes(k.id) ? 'Not tested with this operation' : ''}>{k.label}</button>{/each}
        </div>
        {#if tested.length && !tested.includes(sub.kind)}<p class="st-note warn">{m.label} was built for {tested.join(', ')}. {kind.label} may not work well.</p>{/if}
        <p class="st-note">Examples: <button type="button" class="linkish" onclick={() => s.edit((sp) => { sp.subject.text = kind.example; })}>{kind.example}</button></p>

        <h4>Show it on the picture</h4>
        {#if sup.box || sup.points}
          <div class="st-seg" role="radiogroup" aria-label="How to mark the subject">
            <button type="button" role="radio" aria-checked={s.pick === 'none'} onclick={() => (s.pick = 'none')}>Words only</button>
            {#if sup.box}<button type="button" role="radio" aria-checked={s.pick === 'box'} onclick={() => (s.pick = 'box')}>Draw a box</button>{/if}
            {#if sup.points}<button type="button" role="radio" aria-checked={s.pick === 'points'} onclick={() => (s.pick = 'points')}>Click points</button>
              <button type="button" role="radio" aria-checked={s.pick === 'exclude'} onclick={() => (s.pick = 'exclude')}>Mark what to leave out</button>{/if}
          </div>
          <p class="st-note" data-testid="mark-help">{s.pick === 'box' ? 'Drag on the first frame to draw a box around the subject.' : s.pick === 'points' ? 'Click on the subject. Press a point to remove it.' : s.pick === 'exclude' ? 'Click on anything that must not be part of the subject.' : 'The words above are used on their own. Marks are optional help.'}</p>
          <div class="st-row"><span class="st-note" role="status">{marks ? `${sub.box ? 'A box' : ''}${sub.box && sub.points.length ? ' and ' : ''}${sub.points.length ? `${sub.points.length} point${sub.points.length === 1 ? '' : 's'}` : ''} marked` : 'Nothing marked'}</span>
            <button type="button" class="btn sm" disabled={!marks} onclick={clearMarks}>Clear marks</button></div>
        {:else}
          <p class="st-note" data-testid="no-marking">Marking on the picture is not offered here{sup.note ? `: ${sup.note}` : ': the engine only takes a description in words for this operation'}. Words alone are used.</p>
        {/if}

        {#if s.checkable}
          <h4>Check what will be replaced</h4>
          <p class="st-note">Runs a quick check and shows what the model picked out, before the full run.</p>
          <div class="st-row"><button type="button" class="btn" onclick={() => s.runCheck()} disabled={!canCheck}><Icon name="eye" size={15} /> {s.checking ? 'Starting…' : 'Check selection'}</button></div>
          {#if !sub.text.trim() && !marks}<p class="st-note">Describe the subject first.</p>{/if}
          {#if s.checkError}<p class="st-note bad" role="alert">{s.checkError}</p>{/if}
          {#if s.checkJob}<LiveJob {s} job={s.checkJob} compact />
            <p class="st-note">Not right? Change the words, the marks or the options below and check again.</p>{/if}
        {/if}
        <SettingsFields {s} only={SELECTION_FIELDS} label="Selection options" />
      </section>
    {/if}

    <section class="side" aria-label="Your pictures" data-testid="pictures-section">
      <h3><Icon name="image" size={15} /> Your pictures</h3>
      <p class="st-note">What it should become.</p>
      {#if usedRefs.length}
        <ol class="mini" data-testid="used-refs">
          {#each usedRefs as u (u.i)}<li><span class="th"><MediaThumb id={u.r.asset} kind="image" name={u.r.label || `Picture ${u.i + 1}`} ratio="1 / 1" /></span><span class="n">{u.st.order}</span></li>{/each}
        </ol>
      {:else}
        <p class="st-note">{s.policy.active_max === 0 ? `${m.label} does not use pictures.` : 'No picture is in use yet.'}</p>
      {/if}
      <button type="button" class="btn sm" onclick={() => (s.step = 'references')}>Edit pictures</button>
      <SettingsFields {s} only={BECOMES_FIELDS} label="What it becomes" />
    </section>
  </div>
{/if}

<div class="st-foot"><button type="button" class="btn" onclick={() => (s.step = 'references')}>Back</button><button type="button" class="btn primary" onclick={() => (s.step = 'model')} disabled={!m}>Continue to Model <Icon name="chevron" size={14} /></button></div>

<style>
  .modes { display: grid; grid-template-columns: repeat(auto-fill, minmax(210px, 1fr)); gap: 8px; margin-bottom: var(--s3); }
  .mode { all: unset; box-sizing: border-box; display: grid; gap: 4px; align-content: start; padding: 10px 12px; min-height: 76px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card-bg); cursor: pointer; transition: border-color var(--fast) var(--ease), background var(--fast) var(--ease); }
  .mode:hover:not(.off) { background: var(--hover); } .mode.on { border-color: var(--m-ring); box-shadow: 0 0 0 1px var(--m-ring) inset; background: var(--m-ring-soft); }
  .mode:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; } .mode.off { cursor: not-allowed; opacity: .62; background: var(--surface-2); }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; } strong { font-size: 13.5px; }
  .mode.on .desc { color: var(--fg-2); }
  .desc { font-size: 12px; color: var(--fg-muted); line-height: 1.4; } .desc em { font-style: normal; display: block; color: var(--fg-2); margin-top: 2px; }
  .exp { margin-bottom: var(--s3); align-items: flex-start; } .exp :global(svg) { flex: none; margin-top: 2px; color: var(--warn); } .chk { display: flex; align-items: center; gap: 8px; margin-top: 8px; min-height: 34px; cursor: pointer; }
  .pair { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; align-items: start; }
  .side { display: grid; gap: 8px; padding: 12px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--m-card); min-width: 0; }
  .side :global(.btn) { justify-self: start; }
  .side h3 { display: flex; align-items: center; gap: 7px; font-size: 14px; } h4 { font-size: 12.5px; margin-top: 4px; }
  .mini { list-style: none; margin: 0; padding: 0; display: flex; gap: 8px; flex-wrap: wrap; } .mini li { position: relative; width: 64px; }
  .th { display: block; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); aspect-ratio: 1; } .n { position: absolute; left: 3px; top: 3px; min-width: 18px; height: 18px; padding: 0 5px; border-radius: 9px; display: grid; place-items: center; font: 600 11px var(--font); background: rgba(14,15,16,.82); color: #fff; }
</style>
