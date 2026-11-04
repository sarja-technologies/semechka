<script lang="ts">
  // The settings of an editing profile (for example OpenJutsu), built entirely from the schema the engine publishes: numbers, choices, switches and
  // words. Only what the chosen operation uses is shown. Files are not here: they live in the attachment tray at the top of the composer.
  // Nothing in this file knows what a particular profile does; the server validates everything again.
  let { profile, operation = null, exclude = null, values = $bindable({}), problem = $bindable('') }:
    { profile: any; operation?: string | null; exclude?: string | null; values: Record<string, any>; problem: string } = $props();

  const schema = $derived<Record<string, any>>(profile?.inputs_schema ?? {});
  const used = (s: any) => !operation || !s.operations || s.operations.includes(operation);
  const fields = $derived(Object.entries(schema).filter(([k, s]) => s.type !== 'file' && k !== 'operation' && k !== 'prompt' && k !== exclude && used(s)));
  const label = (k: string) => (schema[k]?.label ?? k.replace(/_/g, ' ')).replace(/^./, (c: string) => c.toUpperCase());

  // what is missing, in plain words; the composer keeps Send off until it is empty
  $effect(() => {
    const bad = fields.find(([k, s]) => s.required && (values[k] === undefined || values[k] === ''));
    const textBad = fields.find(([k, s]) => s.type === 'string' && s.max_length && String(values[k] ?? '').length > s.max_length);
    if (!profile?.ready) problem = profile?.reason ? `Not ready on the server: ${profile.reason}` : 'Not ready on the server yet.';
    else if (bad) problem = `Set ${label(bad[0]).toLowerCase()}.`;
    else if (textBad) problem = `${label(textBad[0])} is too long.`;
    else problem = '';
  });
</script>

<div class="pf" role="group" aria-label="Settings">
  {#if !profile?.ready}
    <p class="warn" role="status">This can’t run yet: {profile?.reason ?? 'the server has not finished setting it up.'}</p>
  {/if}
  {#each fields as [name, spec] (name)}
    {#if spec.type === 'bool'}
      <label class="chk" title={spec.description}>
        <input type="checkbox" checked={!!(values[name] ?? spec.default)} onchange={(e) => (values = { ...values, [name]: e.currentTarget.checked })} />
        <span>{label(name)}</span>
      </label>
    {:else}
      <label class="fld" class:wide={spec.type === 'string'} title={spec.description}>
        <span class="lbl">{label(name)}{spec.required ? '' : ''}</span>
        {#if spec.type === 'enum'}
          <select class="input" value={values[name] ?? spec.default ?? ''} onchange={(e) => (values = { ...values, [name]: spec.values.some((v: any) => typeof v === 'number') ? Number(e.currentTarget.value) : e.currentTarget.value })}>
            {#each spec.values as v}<option value={v}>{v}</option>{/each}
          </select>
        {:else if spec.type === 'int' || spec.type === 'float'}
          <input class="input" type="number" min={spec.min} max={spec.max} step={spec.type === 'int' ? 1 : 0.05} placeholder={spec.default !== undefined ? String(spec.default) : 'Automatic'}
            value={values[name] ?? ''} oninput={(e) => { const v = e.currentTarget.value; values = { ...values, [name]: v === '' ? undefined : Number(v) }; }} />
        {:else}
          <input class="input" value={values[name] ?? ''} oninput={(e) => (values = { ...values, [name]: e.currentTarget.value })} maxlength={spec.max_length ?? 500} placeholder={spec.description ? '' : undefined} />
        {/if}
      </label>
    {/if}
  {/each}
  {#if problem}<p class="tiny err" role="status">{problem}</p>{/if}
</div>

<style>
  .pf { display: flex; flex-wrap: wrap; gap: 10px 14px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); align-items: end; }
  .fld { display: grid; gap: 3px; width: 150px; font-size: 12px; color: var(--fg-muted); } .fld.wide { width: min(100%, 260px); }
  .fld .input { min-height: 32px; padding: 4px 8px; font-size: 13px; color: var(--fg); }
  .chk { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; color: var(--fg); min-height: 32px; cursor: pointer; }
  .chk input { width: 15px; height: 15px; accent-color: var(--m-ring, var(--accent)); }
  .warn, .err { margin: 0; width: 100%; }
</style>
