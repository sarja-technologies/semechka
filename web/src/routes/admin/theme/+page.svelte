<script lang="ts">
  // Theme editor: the three maintained themes plus one Custom theme, typography and layout, with a live preview.
  // Work is kept as a draft until published; publishing checks contrast on the server and is one revertible change.
  import { onMount } from 'svelte';
  import { get, put, post, del } from '$lib/api';
  import { app, loadMe, loadPublic } from '$lib/session.svelte';
  import { toast } from '$lib/toast.svelte';
  import { TOKENS, GROUPS, PRESETS, FONTS, THEME_LABELS, contrastReport, derive, sourceOf, type TokenValues } from '$lib/theme/theme';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import ColorField from '$lib/components/admin/ColorField.svelte';
  import ThemePreview from '$lib/components/admin/ThemePreview.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { errMsg, fmtTime } from '$lib/components/admin/util';

  type V = 'light' | 'dark';
  let data = $state<any>(null), b = $state<any>(null), saved = '', loading = $state(true), error = $state(''), busy = $state(false), formErr = $state('');
  let variant = $state<V>('light'), view = $state<string>('custom'), showAll = $state(false), histOpen = $state(false);
  const BASE = ['brand', 'brand-2', 'accent'] as const;
  const SWATCHES = ['#1D2021', '#2B2F31', '#0B6E69', '#1F5F8B', '#2D6A4F', '#3D5A80', '#8A5A3C', '#9C3D2E', '#B5832A', '#5B6770', '#F7F7F5', '#FFFFFF'];

  async function load() {
    try {
      data = await get('/api/admin/theme');
      b = JSON.parse(JSON.stringify(data.draft ?? data.published));
      delete b.schema;
      saved = JSON.stringify(b); error = '';
      view = b.custom ? 'custom' : b.default ?? 'light';
    } catch (e) { error = errMsg(e); }
    loading = false;
  }
  onMount(load);
  const dirty = $derived(!!b && JSON.stringify(b) !== saved);
  const hasDraft = $derived(!!data?.draft);

  // ---- custom theme ---------------------------------------------------------------------------------------------
  function build(from: V) {
    b.custom = { name: 'Custom', light: { ...PRESETS.light.values }, dark: { ...PRESETS.dark.values }, base: { light: pick(PRESETS.light.values), dark: pick(PRESETS.dark.values) }, overrides: { light: [], dark: [] } };
    variant = from; view = 'custom';
  }
  const pick = (vals: TokenValues) => Object.fromEntries(BASE.map((k) => [k, vals[k]]));
  const vals = $derived<TokenValues>(b?.custom?.[variant] ?? PRESETS[variant].values);
  const overrides = $derived<string[]>(b?.custom?.overrides?.[variant] ?? []);
  function setBase(k: string, hex: string) {
    const c = b.custom;
    c.base[variant] = { ...(c.base[variant] ?? pick(c[variant])), [k]: hex };
    c[variant] = derive(variant, c.base[variant], c.overrides[variant], { ...c[variant], [k]: hex });
  }
  function setToken(tid: string, hex: string) {
    if ((BASE as readonly string[]).includes(tid)) return setBase(tid, hex);
    const c = b.custom;
    c[variant] = { ...c[variant], [tid]: hex };
    if (!c.overrides[variant].includes(tid)) c.overrides[variant] = [...c.overrides[variant], tid];
  }
  function resetToken(tid: string) {
    const c = b.custom;
    c.overrides[variant] = c.overrides[variant].filter((x: string) => x !== tid);
    c[variant] = derive(variant, c.base[variant] ?? pick(c[variant]), c.overrides[variant], { ...c[variant], [tid]: PRESETS[sourceOf(variant, c[variant])].values[tid] });
  }
  function startFrom(src: 'light' | 'dark' | 'graphite') {
    const c = b.custom;
    c[variant] = { ...PRESETS[src].values }; c.overrides[variant] = []; c.base[variant] = pick(PRESETS[src].values);
  }
  const report = $derived(b?.custom ? { light: contrastReport(b.custom.light), dark: contrastReport(b.custom.dark) } : null);
  const failing = $derived(report ? [...report.light.filter((r) => !r.ok).map((r) => ({ ...r, v: 'Light' })), ...report.dark.filter((r) => !r.ok).map((r) => ({ ...r, v: 'Dark' }))] : []);

  // ---- preview --------------------------------------------------------------------------------------------------
  const previewVals = $derived<TokenValues>(view === 'custom' ? vals : PRESETS[view as 'light'].values);
  const previewVariant = $derived<V>(view === 'custom' ? variant : PRESETS[view as 'light']?.scheme ?? 'light');

  // ---- actions --------------------------------------------------------------------------------------------------
  const bundle = () => ({ schema: 'semechka.theme-bundle/1', ...b });
  async function saveDraft() {
    busy = true; formErr = '';
    try { await put('/api/admin/theme/draft', { bundle: bundle() }); toast('Draft saved. Nobody sees it until you publish.'); await load(); }
    catch (e) { formErr = errMsg(e); } finally { busy = false; }
  }
  async function publish() {
    busy = true; formErr = '';
    try { await post('/api/admin/theme/publish', { bundle: bundle() }); toast('Theme published.'); await Promise.all([load(), loadMe(), loadPublic()]); }
    catch (e) { formErr = errMsg(e); } finally { busy = false; }
  }
  async function discard() {
    busy = true; formErr = '';
    try { if (hasDraft) await del('/api/admin/theme/draft'); await load(); b = JSON.parse(JSON.stringify(data.published)); delete b.schema; saved = JSON.stringify(b); toast('Back to the published theme.'); }
    catch (e) { formErr = errMsg(e); } finally { busy = false; }
  }
  async function revert(batch: string) {
    histOpen = false; busy = true;
    try { await post('/api/admin/theme/revert', { batch }); toast('That change was undone.'); await Promise.all([load(), loadMe(), loadPublic()]); }
    catch (e) { toast(errMsg(e), 'bad'); } finally { busy = false; }
  }
  function exportJson() {
    const blob = new Blob([JSON.stringify(bundle(), null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'semechka-theme.json'; a.click(); URL.revokeObjectURL(a.href);
  }
  async function importJson(e: Event) {
    const inp = e.currentTarget as HTMLInputElement; const f = inp.files?.[0]; inp.value = ''; if (!f) return;
    formErr = '';
    try {
      if (f.size > 256 * 1024) throw new Error('Theme files are small; this one is larger than 256 KB.');
      const parsed = JSON.parse(await f.text());
      await post('/api/admin/theme/import', { bundle: parsed });
      toast('Imported as a draft. Check the preview, then publish.'); await load();
    } catch (x) { formErr = x instanceof SyntaxError ? 'That file is not valid JSON.' : errMsg(x); }
  }
  const tokensIn = (g: string) => TOKENS.filter((t) => t.group === g && !(BASE as readonly string[]).includes(t.id));
</script>

<Page title="Theme" lead="Light, Dark and Graphite are maintained with Semechka. The Custom theme is yours: three base colours, every token adjustable, separate light and dark versions. Changes stay in a draft until you publish.">
  <State {loading} {error} retry={load} />
  {#if b}
    <div class="bar" role="toolbar" aria-label="Theme actions">
      <span class="st">
        {#if dirty}<span class="chip warn">Unsaved changes</span>{:else if hasDraft}<span class="chip info">Draft saved, not published</span>{:else}<span class="chip ok">Matches what people see</span>{/if}
      </span>
      <button class="btn sm" onclick={saveDraft} disabled={busy || !dirty}>Save draft</button>
      <button class="btn primary sm" onclick={publish} disabled={busy || (!dirty && !hasDraft) || failing.length > 0} title={failing.length ? 'Fix the contrast problems first' : ''}>Publish</button>
      <button class="btn ghost sm" onclick={discard} disabled={busy || (!dirty && !hasDraft)}>Discard changes</button>
      <span class="spacer"></span>
      <button class="btn ghost sm" onclick={exportJson}><Icon name="download" size={15} /> Export</button>
      <label class="btn ghost sm"><Icon name="upload" size={15} /> Import<input type="file" accept="application/json,.json" class="sr-only" onchange={importJson} /></label>
      <button class="btn ghost sm" onclick={() => (histOpen = true)} disabled={!data.history.length}><Icon name="history" size={15} /> History</button>
    </div>
    {#if formErr}<div class="notice bad" role="alert"><div>{formErr}</div></div>{/if}

    <div class="grid">
      <div class="controls">
        <section class="panel"><div class="panel-h"><h3>Who sees what</h3></div><div class="panel-b stack">
          <div class="field"><label for="tdef">Default theme</label>
            <select id="tdef" class="input" bind:value={b.default}>
              {#each ['light', 'dark', 'graphite'] as t (t)}<option value={t}>{THEME_LABELS[t]}</option>{/each}
              <option value="custom" disabled={!b.custom}>Custom{b.custom ? '' : ' (build it below first)'}</option>
            </select>
            <span class="hint">Per-role and per-team defaults can be set in All settings → Default theme.</span></div>
          <label class="row"><input type="checkbox" bind:checked={b.follow_system} /> <span>Follow each device's light or dark setting <span class="hint">(Light and Dark switch; Custom switches between its two versions; Graphite stays as it is)</span></span></label>
          <label class="row"><input type="checkbox" bind:checked={b.user_choice} /> <span>Let people choose their own theme in the account menu</span></label>
        </div></section>

        <section class="panel"><div class="panel-h"><h3>Custom theme</h3>
          {#if b.custom}<div class="seg" role="tablist" aria-label="Version">{#each ['light', 'dark'] as v (v)}<button role="tab" aria-selected={variant === v} class:on={variant === v} onclick={() => { variant = v as V; view = 'custom'; }}>{v === 'light' ? 'Light version' : 'Dark version'}</button>{/each}</div>{/if}
        </div><div class="panel-b stack">
          {#if !b.custom}
            <p class="muted">No Custom theme yet. Build one from the maintained themes, then change what you need.</p>
            <div><button class="btn" onclick={() => build('light')}>Build a Custom theme</button></div>
          {:else}
            <div class="field"><label for="cname">Name shown to people</label><input id="cname" class="input" maxlength="40" bind:value={b.custom.name} /></div>
            <div class="row small">Start this version from
              {#each (variant === 'light' ? ['light'] : ['dark', 'graphite']) as s (s)}<button class="btn sm" onclick={() => startFrom(s as any)}>{THEME_LABELS[s]}</button>{/each}
            </div>
            <div class="base">
              {#each BASE as k (k)}
                {@const t = TOKENS.find((x) => x.id === k)!}
                <ColorField id={`c-${variant}-${k}`} label={t.label} hint={t.hint} value={vals[k]} swatches={SWATCHES} onchange={(h) => setBase(k, h)} />
              {/each}
            </div>
            <p class="hint">Changing a base colour re-derives buttons, selection, your messages and the sidebar and header tints. Text colours are never derived, so readable contrast is not put at risk; tokens you set yourself are kept.</p>
            <button class="btn ghost sm adv" aria-expanded={showAll} onclick={() => (showAll = !showAll)}><span class="chev" class:shut={!showAll}><Icon name="chevdown" size={14} /></span> Every colour ({TOKENS.length - 3}){overrides.length ? ` · ${overrides.length} set by you` : ''}</button>
            {#if showAll}
              {#each GROUPS.filter((g) => g.id !== 'brand') as g (g.id)}
                <h4 class="gh">{g.label}</h4>
                <div class="toks">
                  {#each tokensIn(g.id) as t (t.id)}
                    <ColorField id={`c-${variant}-${t.id}`} label={t.label} hint={t.hint} value={vals[t.id]} alpha={t.alpha} overridden={overrides.includes(t.id)}
                      onchange={(h) => setToken(t.id, h)} onreset={() => resetToken(t.id)} />
                  {/each}
                </div>
              {/each}
            {/if}
            <div class="contrast" aria-live="polite">
              <h4 class="gh">Contrast, {variant} version</h4>
              <ul>
                {#each report?.[variant] ?? [] as r (r.label)}
                  <li class:fail={!r.ok}><span class="chip {r.ok ? 'ok' : 'bad'}">{r.ok ? 'Pass' : 'Too low'}</span><span class="grow">{r.label}</span><span class="mono tiny">{r.ratio}:1 (needs {r.min}:1)</span></li>
                {/each}
              </ul>
              {#if failing.length}<p class="err">{failing.length} pair{failing.length === 1 ? '' : 's'} below the minimum ({[...new Set(failing.map((f) => f.v))].join(' and ')}). Publishing is blocked until they pass.</p>{/if}
            </div>
            <button class="btn ghost sm danger" onclick={() => { b.custom = null; if (b.default === 'custom') b.default = 'light'; view = b.default; }}>Remove the Custom theme</button>
          {/if}
        </div></section>

        <section class="panel"><div class="panel-h"><h3>Type and shape</h3></div><div class="panel-b two">
          <div class="field"><label for="tf">Font</label><select id="tf" class="input" bind:value={b.font}>{#each FONTS as f (f.id)}<option value={f.id}>{f.label}</option>{/each}</select><span class="hint">Self-hosted or the device's own; nothing loads from elsewhere.</span></div>
          <div class="field"><label for="tfs">Text size: {b.font_size}px</label><input id="tfs" type="range" min="13" max="18" bind:value={b.font_size} /></div>
          <div class="field"><label for="trad">Corner radius: {b.radius}px</label><input id="trad" type="range" min="0" max="16" bind:value={b.radius} /></div>
          <div class="field"><label for="tden">Density</label><select id="tden" class="input" bind:value={b.density}><option value="comfortable">Comfortable</option><option value="compact">Compact</option></select></div>
        </div></section>

        <section class="panel"><div class="panel-h"><h3>Conversation layout</h3></div><div class="panel-b two">
          <div class="field"><label for="tal">Each person's own messages</label><select id="tal" class="input" bind:value={b.user_align}><option value="right">On the right</option><option value="left">On the left</option></select></div>
          <div class="field"><label for="tmw">Conversation width</label><select id="tmw" class="input" bind:value={b.message_width}><option value="narrow">Narrow</option><option value="normal">Normal</option><option value="wide">Wide</option></select></div>
          <div class="field"><label for="tsw">Sidebar width: {b.sidebar_width}px</label><input id="tsw" type="range" min="220" max="420" step="4" bind:value={b.sidebar_width} /><span class="hint">People can still drag it wider or narrower for themselves.</span></div>
        </div></section>

        <section class="panel"><div class="panel-h"><h3>Custom CSS</h3></div><div class="panel-b">
          <div class="field"><label for="tcss">Applied to conversation pages only</label>
            <textarea id="tcss" class="input mono" rows="6" bind:value={b.css} spellcheck="false" placeholder=".msg.assistant .who {'{'} letter-spacing: 0.01em; {'}'}"></textarea>
            <span class="hint">Never applied to administration, account or sign-in pages, so it cannot hide a way back. No imports, remote addresses, fonts or scripting; only uploaded branding images may be referenced. Checked when you save or publish. CSS is not a stable interface: class names can change between releases, while the colour tokens above will not.</span></div>
        </div></section>
      </div>

      <div class="pv">
        <div class="pvh">
          <h3>Preview</h3>
          <div class="seg" role="radiogroup" aria-label="Preview">
            {#each ['light', 'dark', 'graphite'] as t (t)}<button role="radio" aria-checked={view === t} class:on={view === t} onclick={() => (view = t)}>{THEME_LABELS[t]}</button>{/each}
            {#if b.custom}<button role="radio" aria-checked={view === 'custom'} class:on={view === 'custom'} onclick={() => (view = 'custom')}>Custom ({variant})</button>{/if}
          </div>
        </div>
        <ThemePreview values={previewVals} variant={previewVariant} bundle={b} appName={app.brand.app_name || 'Assistant'} />
        <p class="hint">Painted with the values above, including unsaved ones. The rest of this page keeps your current theme.</p>
      </div>
    </div>
  {/if}
</Page>

<Modal bind:open={histOpen} title="Published theme changes" wide>
  <p class="small muted">Undoing a change puts every value it changed back as it was. The undo is itself a change you can undo.</p>
  <ul class="hist">
    {#each data?.history ?? [] as h (h.batch)}
      <li><span class="grow"><strong>{fmtTime(h.ts)}</strong> <span class="small muted">{h.keys.length} setting{h.keys.length === 1 ? '' : 's'}: {h.keys.map((k: string) => k.split('.').pop()).join(', ')}</span></span><button class="btn sm" onclick={() => revert(h.batch)} disabled={busy}>Undo</button></li>
    {:else}<li class="muted">Nothing published from the editor yet.</li>{/each}
  </ul>
  {#snippet footer()}<button class="btn" onclick={() => (histOpen = false)}>Close</button>{/snippet}
</Modal>

<style>
  .bar { position: sticky; top: 0; z-index: 5; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; padding: 8px 0; background: var(--bg); border-bottom: 1px solid var(--border); margin-bottom: 16px; }
  .st { margin-right: 8px; }
  .grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(360px, 520px); gap: 20px; align-items: start; }
  .controls { display: grid; gap: 16px; min-width: 0; }
  .pv { position: sticky; top: 64px; display: grid; gap: 8px; }
  .pvh { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
  .seg { display: inline-flex; flex-wrap: wrap; border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 2px; gap: 2px; }
  .seg button { all: unset; padding: 0 10px; height: 26px; display: inline-flex; align-items: center; border-radius: calc(var(--radius-sm) - 2px); cursor: pointer; font-size: 13px; }
  .seg button.on { background: var(--selected); font-weight: 600; } .seg button:focus-visible { outline: 2px solid var(--accent); }
  .base { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .toks { display: grid; gap: 12px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .gh { font-size: 13px; margin: 8px 0 0; color: var(--fg-muted); }
  .adv { justify-self: start; }
  .chev { display: grid; transition: transform 120ms ease-out; } .chev.shut { transform: rotate(-90deg); }
  .contrast ul { list-style: none; margin: 6px 0 0; padding: 0; display: grid; gap: 4px; }
  .contrast li { display: flex; gap: 8px; align-items: center; font-size: 13px; }
  .contrast li.fail { font-weight: 600; }
  .two { display: grid; gap: 4px 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  input[type='range'] { accent-color: var(--accent); width: 100%; }
  .hist { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; } .hist li { display: flex; gap: 12px; align-items: center; }
  .danger { color: var(--danger); justify-self: start; }
  @media (max-width: 1100px) { .grid { grid-template-columns: minmax(0, 1fr); } .pv { position: static; order: -1; } }
</style>
