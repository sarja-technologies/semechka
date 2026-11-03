<script lang="ts">
  // The input form of an editing profile (for example OpenJutsu). Built entirely from the schema the engine publishes for the profile:
  // files, numbers, choices. Nothing here knows what a particular profile does; the server validates everything again.
  let { profile, maxMb, values = $bindable({}), files = $bindable({}), problem = $bindable('') }:
    { profile: any; maxMb: number; values: Record<string, any>; files: Record<string, File[]>; problem: string } = $props();

  const schema = $derived<Record<string, any>>(profile?.inputs_schema ?? {});
  const fileFields = $derived(Object.entries(schema).filter(([, s]) => s.type === 'file'));
  const otherFields = $derived(Object.entries(schema).filter(([k, s]) => s.type !== 'file' && k !== 'prompt'));
  const label = (k: string) => (schema[k]?.label ?? k.replace(/_/g, ' ')).replace(/^./, (c: string) => c.toUpperCase());
  const accept = (s: any) => (s.kind ? `${s.kind}/*` : 'video/*,image/*');
  const mb = (n: number) => `${(n / 1048576).toFixed(n > 1e7 ? 0 : 1)} MB`;

  function add(name: string, spec: any, list: FileList | null) {
    if (!list) return;
    const cur = [...(files[name] ?? [])];
    for (const f of list) {
      if (f.size > maxMb * 1048576) { problem = `${f.name} is larger than ${maxMb} MB.`; continue; }
      if (spec.kind && !f.type.startsWith(spec.kind + '/')) { problem = `${label(name)} must be a ${spec.kind}.`; continue; }
      if (spec.multi ? cur.length >= (spec.max_files ?? 9) : cur.length >= 1) { if (!spec.multi) cur.length = 0; else { problem = `At most ${spec.max_files ?? 9} files.`; continue; } }
      cur.push(f);
    }
    files = { ...files, [name]: cur };
  }
  function drop(name: string, i: number) { const cur = [...(files[name] ?? [])]; cur.splice(i, 1); files = { ...files, [name]: cur }; }

  // what is missing, in plain words; the composer keeps Send off until it is empty
  $effect(() => {
    const miss = fileFields.find(([k, s]) => s.required && !(files[k]?.length));
    const bad = otherFields.find(([k, s]) => s.required && (values[k] === undefined || values[k] === ''));
    if (!profile?.ready) problem = profile?.reason ? `Not ready on the server: ${profile.reason}` : 'Not ready on the server yet.';
    else if (miss) problem = `Add ${label(miss[0]).toLowerCase()}.`;
    else if (bad) problem = `Set ${label(bad[0]).toLowerCase()}.`;
    else if (problem && !problem.includes('larger') && !problem.includes('must be') && !problem.includes('At most')) problem = '';
  });
</script>

<div class="pf" role="group" aria-label="Video editing inputs">
  {#if !profile?.ready}
    <p class="warn" role="status">This can’t run yet: {profile?.reason ?? 'the server has not finished setting it up.'}</p>
  {/if}
  {#each fileFields as [name, spec] (name)}
    <div class="fld">
      <span class="lbl">{label(name)}{spec.required ? '' : ' (optional)'}</span>
      <label class="pick">
        <input type="file" accept={accept(spec)} multiple={!!spec.multi} onchange={(e) => { add(name, spec, e.currentTarget.files); e.currentTarget.value = ''; }} />
        <span>{(files[name]?.length ?? 0) ? 'Add another' : `Choose ${spec.kind ?? 'file'}${spec.multi ? 's' : ''}`}</span>
      </label>
      {#each files[name] ?? [] as f, i}
        <span class="file">{f.name} <span class="muted">{mb(f.size)}</span> <button type="button" class="linkish" onclick={() => drop(name, i)} aria-label={`Remove ${f.name}`}>Remove</button></span>
      {/each}
    </div>
  {/each}
  {#each otherFields as [name, spec] (name)}
    <label class="fld">
      <span class="lbl">{label(name)}</span>
      {#if spec.type === 'enum'}
        <select class="input" bind:value={values[name]}>{#each spec.values as v}<option value={v}>{v}</option>{/each}</select>
      {:else if spec.type === 'int' || spec.type === 'float'}
        <input class="input" type="number" min={spec.min} max={spec.max} step={spec.type === 'int' ? 1 : 0.05} placeholder={spec.default !== undefined ? String(spec.default) : ''}
          value={values[name] ?? ''} oninput={(e) => { const v = e.currentTarget.value; values = { ...values, [name]: v === '' ? undefined : Number(v) }; }} />
      {:else}
        <input class="input" bind:value={values[name]} maxlength={spec.max_length ?? 500} />
      {/if}
    </label>
  {/each}
  {#if problem}<p class="tiny err" role="status">{problem}</p>{/if}
</div>

<style>
  .pf { display: grid; gap: 10px; padding: 8px 4px; }
  .fld { display: grid; gap: 4px; }
  .lbl { font-size: 0.85em; color: var(--muted, inherit); }
  .pick input { position: absolute; opacity: 0; width: 1px; height: 1px; }
  .pick span { display: inline-block; border: 1px dashed var(--border-strong, currentColor); border-radius: 8px; padding: 6px 12px; cursor: pointer; }
  .pick:focus-within span { outline: 2px solid var(--focus, currentColor); }
  .file { font-size: 0.9em; display: flex; gap: 8px; align-items: baseline; }
  .warn { margin: 0; }
</style>
