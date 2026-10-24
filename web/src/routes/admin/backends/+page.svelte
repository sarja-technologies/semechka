<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, put, del } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import { errMsg, fmtTime } from '$lib/components/admin/util';
  let rows = $state<any[]>([]); let loading = $state(true); let error = $state('');
  let open = $state(false); let cur = $state<any>(null); let f = $state<any>({}); let err = $state('');
  let status = $state<Record<string, string>>({}); let delT = $state<any>(null); let delOpen = $state(false);
  const ADAPTERS = [['sarness', 'Sarness Engine', 'http://127.0.0.1:8742'], ['ollama', 'Ollama', 'http://127.0.0.1:11434'], ['openai_compat', 'OpenAI-compatible (llama.cpp, vLLM, others)', 'http://127.0.0.1:8080/v1']];
  async function load() { loading = true; try { rows = await get('/api/admin/backends'); error = ''; } catch (e) { error = errMsg(e); } loading = false; }
  onMount(load);
  function edit(b: any) { cur = b; f = b ? { name: b.name, adapter: b.adapter, base_url: b.base_url, api_key: '', external: b.external, enabled: b.enabled } : { name: '', adapter: 'openai_compat', base_url: ADAPTERS[2][2], api_key: '', external: false, enabled: true }; err = ''; open = true; }
  async function save() { err = ''; try { const body = { ...f, api_key: f.api_key || null }; if (cur) await put(`/api/admin/backends/${cur.id}`, body); else await post('/api/admin/backends', body); open = false; toast('Backend saved.'); load(); } catch (e) { err = errMsg(e); } }
  async function test(b: any) { status[b.id] = 'Testing…'; try { const r = await post(`/api/admin/backends/${b.id}/test`); status[b.id] = r.ok ? `Reachable${r.version ? ', version ' + r.version : ''}.` : `Not reachable: ${r.detail}`; } catch (e) { status[b.id] = errMsg(e); } }
  async function sync(b: any) { status[b.id] = 'Discovering…'; try { const r = await post(`/api/admin/backends/${b.id}/sync`); status[b.id] = `Found ${r.found}; ${r.added} new, ${r.updated} updated. New models stay disabled until you enable them.`; load(); } catch (e) { status[b.id] = errMsg(e); } }
  async function remove() { try { await del(`/api/admin/backends/${delT.id}`); delOpen = false; toast('Backend removed with its models.'); load(); } catch (e) { toast(errMsg(e), 'bad'); } }
  const yn = (v: boolean) => (v ? 'Supported' : 'Not supported');
  const caps: [string, string][] = [['streaming', 'Streaming'], ['cancel', 'Stop generation'], ['model_load', 'Load models'], ['model_unload', 'Unload models'], ['model_install', 'Install models'], ['system_telemetry', 'Hardware telemetry'], ['reports_usage_tokens', 'Reports token counts'], ['reports_timings', 'Reports speed']];
</script>
<Page title="Backends" lead="The inference servers Semechka talks to. Capabilities below come from each adapter’s manifest; anything it cannot do is shown as not supported, never faked.">
  {#snippet actions()}<button class="btn primary" onclick={() => edit(null)}>Add backend</button>{/snippet}
  <State {loading} {error} retry={load} empty={!loading && !error && !rows.length ? 'No backend connected. Add Sarness Engine, Ollama or an OpenAI-compatible server (llama.cpp, vLLM).' : ''} />
  <div class="stack">{#each rows as b (b.id)}
    <section class="panel"><div class="panel-h"><div><h3>{b.name}</h3><span class="mono tiny muted">{b.adapter} · {b.base_url}</span></div>
      <div class="row">{#if b.external}<span class="chip warn">External: content leaves your network</span>{:else}<span class="chip ok">Local</span>{/if}{#if !b.enabled}<span class="chip bad">Disabled</span>{/if}{#if b.has_api_key}<span class="chip">API key set</span>{/if}</div></div>
      <div class="panel-b"><div class="row" style="margin-bottom:12px"><button class="btn sm" onclick={() => test(b)}>Test connection</button><button class="btn sm" onclick={() => sync(b)}>Sync models</button><button class="btn sm" onclick={() => edit(b)}>Edit</button><button class="btn sm danger" onclick={() => { delT = b; delOpen = true; }}>Remove</button><span class="small" role="status">{status[b.id] ?? ''}</span></div>
        <dl class="kv" style="margin-bottom:12px"><dt>Version seen</dt><dd>{b.version ?? 'not reported'}</dd><dt>Last discovery</dt><dd>{fmtTime(b.last_seen)}</dd></dl>
        {#if b.manifest}<div class="tablewrap"><table class="t"><caption class="sr-only">Adapter capabilities for {b.name}</caption><thead><tr><th>Capability</th><th>Status</th></tr></thead><tbody>
          {#each caps as [k, l]}<tr><td>{l}</td><td><span class="chip {b.manifest[k] ? 'ok' : ''}">{yn(b.manifest[k])}</span></td></tr>{/each}
          <tr><td>Model details it can report</td><td class="mono tiny">{b.manifest.model_metadata.join(', ') || 'none'}</td></tr></tbody></table></div>{#if b.manifest.notes}<p class="hint" style="margin:8px 0 0">{b.manifest.notes}</p>{/if}{/if}</div></section>{/each}</div>
</Page>
<Modal bind:open title={cur ? `Edit ${cur.name}` : 'Add backend'}>
  <div class="field"><label for="bn">Name</label><input id="bn" class="input" bind:value={f.name} /></div>
  <div class="field"><label for="ba">Adapter</label><select id="ba" class="input" bind:value={f.adapter} disabled={!!cur} onchange={() => { if (!cur) f.base_url = ADAPTERS.find((a) => a[0] === f.adapter)![2]; }}>{#each ADAPTERS as a}<option value={a[0]}>{a[1]}</option>{/each}</select></div>
  <div class="field"><label for="bu">Address</label><input id="bu" class="input mono" bind:value={f.base_url} /></div>
  <div class="field"><label for="bk">API key / token</label><input id="bk" class="input" type="password" autocomplete="new-password" bind:value={f.api_key} placeholder={cur?.has_api_key ? 'Set. Type to replace. It is never shown.' : 'Optional'} /><span class="hint">Write-only. Stored encrypted; never returned by the API.</span></div>
  <label class="row" style="gap:8px"><input type="checkbox" bind:checked={f.external} /> This server is external (outside your control)</label>
  <p class="hint">Marking a backend external makes the privacy setting “Allow external inference backends” apply to every model on it. Leave it off only for servers you operate.</p>
  <label class="row" style="gap:8px"><input type="checkbox" bind:checked={f.enabled} /> Enabled</label>
  {#if err}<p class="err" role="alert">{err}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (open = false)}>Cancel</button><button class="btn primary" onclick={save}>Save</button>{/snippet}
</Modal>
<Modal bind:open={delOpen} title="Remove {delT?.name}?"><p>Its discovered models and their access rules are deleted from the catalogue. Saved conversations keep their text but lose the model link.</p>{#snippet footer()}<button class="btn" onclick={() => (delOpen = false)}>Cancel</button><button class="btn danger solid" onclick={remove}>Remove backend</button>{/snippet}</Modal>
