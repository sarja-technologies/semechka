<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // Platform administration of organisations: create one (naming its first owner), see its size, suspend or reactivate it.
  // Platform administrators run the platform; they do not get a seat in, or a view of, any organisation's content.
  import { onMount } from 'svelte';
  import { get, post, patch } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { bytes, msg, ago } from '$lib/ws/util';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Icon from '$lib/components/Icon.svelte';

  let orgs = $state<any[]>([]), loading = $state(true), error = $state('');
  async function load() { try { orgs = await get('/api/admin/orgs'); error = ''; } catch (e) { error = msg(e); } finally { loading = false; } }
  onMount(load);
  let dlg = $state(false), name = $state(''), slug = $state(''), owner = $state(''), formErr = $state(''), busy = $state(false);
  async function create() {
    busy = true; formErr = '';
    try { await post('/api/orgs', { name, slug: slug || undefined, owner_email: owner }); dlg = false; name = slug = owner = ''; toast('Organisation created'); await load(); } catch (e) { formErr = msg(e); } finally { busy = false; }
  }
  const setState = (o: any, status: string) => patch(`/api/admin/orgs/${o.id}`, { status }).then(() => { toast(status === 'suspended' ? 'Suspended: everyone in it lost access at once' : 'Reactivated'); return load(); }).catch((e) => toast(msg(e), 'bad'));
</script>
<Page title="Organisations" lead="Separate companies or departments on this server, each with its own people, roles, workspaces and limits. Their content is never visible to platform administrators.">
  {#snippet actions()}<button class="btn primary" onclick={() => { formErr = ''; dlg = true; }}><Icon name="plus" size={16} /> New organisation</button>{/snippet}
  <State {loading} {error} retry={load} />
  {#if !loading && !error}
    <div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">Organisations</caption>
      <thead><tr><th>Name</th><th>Status</th><th class="num">People</th><th class="num">Workspaces</th><th class="num">Storage</th><th>Created</th><th></th></tr></thead>
      <tbody>{#each orgs as o (o.id)}<tr><td><strong>{o.name}</strong> <span class="muted mono tiny">{o.slug}</span>{#if o.is_default} <span class="chip">Default</span>{/if}</td>
        <td><span class="chip" class:ok={o.status === 'active'} class:warn={o.status !== 'active'}>{o.status === 'active' ? 'Active' : 'Suspended'}</span></td>
        <td class="num">{o.members}</td><td class="num">{o.workspaces}</td><td class="num">{bytes(o.storage_bytes)}</td><td class="muted">{ago(o.created_at)}</td>
        <td style="text-align:right">{#if o.status === 'active'}<button class="btn sm ghost" onclick={() => setState(o, 'suspended')}>Suspend</button>{:else}<button class="btn sm" onclick={() => setState(o, 'active')}>Reactivate</button>{/if}</td></tr>{/each}</tbody></table></div>
    <p class="tiny muted" style="margin-top:8px">The <strong>Default</strong> organisation holds everyone who had an account before organisations existed. Creating a second organisation stops new accounts joining it automatically. Suspending an organisation deletes nothing; reactivating restores it exactly.</p>
  {/if}
</Page>
<Modal bind:open={dlg} title="New organisation">
  <form id="of" onsubmit={(e) => { e.preventDefault(); create(); }}>
    <div class="field"><label for="on">Name</label><input id="on" class="input" bind:value={name} maxlength="160" required /></div>
    <div class="field"><label for="os">Short name <span class="muted">(optional)</span></label><input id="os" class="input mono" bind:value={slug} maxlength="48" placeholder="acme" /></div>
    <div class="field"><label for="oo">First owner's email</label><input id="oo" class="input" type="email" bind:value={owner} required /><span class="hint">An existing account. The owner then adds everyone else; you do not become a member.</span></div>
    {#if formErr}<div class="err" role="alert">{formErr}</div>{/if}
  </form>
  {#snippet footer()}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn primary" form="of" type="submit" disabled={busy || !name.trim() || !owner.trim()}>Create organisation</button>{/snippet}
</Modal>
