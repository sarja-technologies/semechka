<script lang="ts">
  import { goto } from '$app/navigation';
  import { get, post, put, setSetupToken, ApiError } from '$lib/api';
  import { app, loadPublic, loadMe } from '$lib/session.svelte';
  import Wordmark from '$lib/components/Wordmark.svelte';

  const STEPS = ['Token', 'Identity', 'Preset', 'Hardware', 'Models', 'People', 'Privacy', 'Access', 'Launch'];
  let step = $state(0), busy = $state(false), error = $state('');
  let token = $state(''), hw = $state<any>(null);
  let owner = $state({ display_name: '', email: '', password: '' });
  let preset = $state('business');
  // every wizard value is a settings-registry key, so it reappears later in Administration > Settings
  let v = $state<Record<string, any>>({
    'brand.org_name': '', 'brand.app_name': '', 'brand.short_name': '', 'brand.tab_title': '', 'brand.theme_default': 'light',
    'privacy.mode': 'standard', 'privacy.chat_retention_days': 30, 'privacy.ephemeral_allowed': true, 'privacy.external_egress': false, 'privacy.notice': '',
    'quota.daily_messages': 0, 'chat.attachments': true,
    'access.exposure': 'loopback', 'signup.mode': 'invite', 'signup.require_approval': true, 'signup.allowed_domains': [], 'auth.mfa_required_admins': false
  });
  let domainsText = $state('');
  let be = $state({ on: true, adapter: 'openai_compat', base_url: '', api_key: '', external: false, hint_id: '', name: 'Primary backend' });
  let probe = $state<any>(null), enable = $state<string[]>([]), smoke = $state('');
  let techPreset = $state('simple'), result = $state<any>(null);

  function pickPreset(k: string) { preset = k; Object.assign(v, hw.presets[k].settings); domainsText = (v['signup.allowed_domains'] ?? []).join(', '); }
  async function start() {
    busy = true; error = '';
    try { setSetupToken(token.trim()); hw = await get('/api/setup/hardware'); pickPreset(preset); const found = hw.backends.find((b: any) => b.reachable); if (found) { be.adapter = found.adapter; be.base_url = found.base_url; be.hint_id = found.hint_id ?? ''; } step = 1; }
    catch (e) { setSetupToken(''); error = e instanceof ApiError ? e.message : 'Could not reach the server.'; } finally { busy = false; }
  }
  async function testBackend() {
    busy = true; error = ''; probe = null;
    try { probe = await post('/api/setup/backend-test', { adapter: be.adapter, base_url: be.base_url, api_key: be.api_key || null, external: be.external, hint_id: be.hint_id || null }); enable = probe.health.ok ? probe.models.filter((m: any) => m.capabilities.includes('chat')).map((m: any) => m.real_id) : []; smoke = enable[0] ?? ''; }
    catch (e) { error = e instanceof ApiError ? e.message : 'Test failed.'; } finally { busy = false; }
  }
  const idOk = $derived(owner.display_name.trim() && owner.email.includes('@') && owner.password.length >= 12 && v['brand.org_name'].trim() && v['brand.app_name'].trim());
  function settingsPayload() {
    const s: Record<string, any> = { ...v };
    for (const k of ['brand.short_name', 'brand.tab_title']) if (!s[k]) s[k] = s['brand.app_name'];
    s['signup.allowed_domains'] = domainsText.split(/[,\s]+/).filter(Boolean);
    return s;
  }
  async function launch() {
    busy = true; error = '';
    try {
      const body: any = { owner, preset, settings: settingsPayload() };
      if (be.on && probe?.health.ok) { body.backend = { adapter: be.adapter, base_url: be.base_url, api_key: be.api_key || null, external: be.external, hint_id: be.hint_id || null }; body.backend_name = be.name; body.enable_models = enable; body.smoke_model = smoke || null; }
      result = await post('/api/setup/complete', body);
      setSetupToken('');
      await loadPublic(); await loadMe();
      // technical-visibility default for the Member role (the setup call has no field for it, so it is applied as the signed-in owner)
      if (techPreset !== 'simple') {
        try {
          const tp = await get('/api/admin/tech-presets'); const roles = await get('/api/admin/roles'); const m = roles.find((r: any) => r.id === 'member');
          const add = tp.presets[techPreset] ?? []; await put('/api/admin/roles/member', { label: m.label, description: m.description, rank: m.rank, grants: [...new Set([...m.grants, ...add])], denies: m.denies });
          await loadMe();
        } catch (e) { result = { ...result, tech_error: e instanceof ApiError ? e.message : 'Could not apply the technical visibility preset.' }; }
      }
      step = 9;
    } catch (e) {
      error = e instanceof ApiError ? e.message + (e.detail?.setting ? ` (${e.detail.setting})` : '') : 'Setup failed.';
    } finally { busy = false; }
  }
  const retentionOpts = [[0, 'Do not store chat history'], [7, '7 days'], [30, '30 days'], [90, '90 days'], [365, '1 year'], [-1, 'Until the user deletes it']];
  const TECH = [['simple', 'Simple', 'Friendly model names and plain errors only.'], ['informed', 'Informed', 'Adds model family, context size, approximate speed and queue state.'], ['technical', 'Technical', 'Adds real model ID, quantisation, generation settings, token counts and timings.'], ['expert', 'Expert', 'Every safe technical field the backend provides. Never secrets, other people’s content or control over infrastructure.']];
  const next = () => { error = ''; step++; window.scrollTo(0, 0); }, back = () => { error = ''; step--; };
</script>
<main id="main" class="wiz">
  <header><Wordmark size={20} /><span class="muted small">First-run setup</span></header>
  {#if step < 9}
    <ol class="steps" aria-label="Setup progress">{#each STEPS as s, i}<li class:on={i === step} class:done={i < step} aria-current={i === step ? 'step' : undefined}><span class="n mono">{i}</span><span class="t">{s}</span></li>{/each}</ol>
  {/if}
  <div class="body">
  {#if error}<div class="notice bad" role="alert"><div>{error}</div></div>{/if}

  {#if step === 0}
    <h1>Set up this server</h1>
    <p>Setup is protected by a one-time token so that nobody who can reach this page can claim the owner account. The token was printed in the terminal where Semechka started, and is saved in the data directory.</p>
    <p class="small muted">Find it with <code>cat ~/.local/share/semechka/setup-token</code> (or the <code>SEMECHKA_DATA_DIR</code> you chose). It stops working as soon as the owner account is created.</p>
    <div class="field"><label for="tk">Setup token</label><input id="tk" class="input mono" bind:value={token} autocomplete="off" spellcheck="false" onkeydown={(e) => e.key === 'Enter' && token && start()} /></div>
    <button class="btn primary" onclick={start} disabled={busy || !token.trim()}>{busy ? 'Checking…' : 'Continue'}</button>

  {:else if step === 1}
    <h1>Identity</h1>
    <p class="muted">Names appear on the sign-in page, browser tab and emails. You can change all of this later under Administration.</p>
    <div class="grid">
      <div class="field"><label for="on">Organisation name</label><input id="on" class="input" bind:value={v['brand.org_name']} placeholder="Banana Corporation" /></div>
      <div class="field"><label for="an">Application title</label><input id="an" class="input" bind:value={v['brand.app_name']} placeholder="Banana Corporation Private AI" /></div>
      <div class="field"><label for="sn">Short name</label><input id="sn" class="input" bind:value={v['brand.short_name']} placeholder="Banana AI" /></div>
      <div class="field"><label for="tt">Browser tab label</label><input id="tt" class="input" bind:value={v['brand.tab_title']} placeholder="Banana AI" /></div>
      <div class="field"><label for="th">Default theme</label><select id="th" class="input" bind:value={v['brand.theme_default']}><option value="light">Light</option><option value="dark">Dark</option><option value="graphite">Graphite (dark grey)</option></select></div>
    </div>
    <p class="hint">Logo, favicon and colours are set after launch in Administration, where the logo can be uploaded and previewed.</p>
    <h2 style="margin:24px 0 8px;font-size:16px">Owner account</h2>
    <div class="grid">
      <div class="field"><label for="od">Your name</label><input id="od" class="input" bind:value={owner.display_name} autocomplete="name" /></div>
      <div class="field"><label for="oe">Email</label><input id="oe" class="input" type="email" bind:value={owner.email} autocomplete="username" /></div>
      <div class="field"><label for="op">Password</label><input id="op" class="input" type="password" bind:value={owner.password} autocomplete="new-password" aria-describedby="oph" /><span id="oph" class="hint">At least 12 characters. A passphrase of several words works well.</span></div>
    </div>
    <div class="notice info"><div class="small"><strong>Recovery.</strong> Passwords are never emailed. If you lose access, another owner can set a new password, or someone with access to this server can restore from an encrypted backup. Email-based reset works only after SMTP is configured.</div></div>
    <div class="row"><button class="btn" onclick={back}>Back</button><button class="btn primary" onclick={next} disabled={!idOk}>Continue</button></div>

  {:else if step === 2}
    <h1>Deployment preset</h1>
    <p class="muted">A preset only fills in starting values. Every value stays editable, and you can see them on the review step.</p>
    <div class="stack" role="radiogroup" aria-label="Preset">
      {#each Object.entries(hw.presets) as [k, p]}
        <label class="opt" class:sel={preset === k}><input type="radio" name="preset" checked={preset === k} onchange={() => pickPreset(k)} /><div><strong>{(p as any).label}</strong><div class="small muted">{(p as any).description}</div></div></label>
      {/each}
    </div>
    <div class="row" style="margin-top:16px"><button class="btn" onclick={back}>Back</button><button class="btn primary" onclick={next}>Continue</button></div>

  {:else if step === 3}
    <h1>This machine</h1>
    <p class="muted">What Semechka could actually detect. Nothing here is estimated.</p>
    <dl class="kv" style="margin-bottom:16px">
      <dt>System</dt><dd>{hw.host.os}</dd><dt>Architecture</dt><dd>{hw.host.arch}</dd><dt>CPU</dt><dd>{hw.host.cpu_model ?? 'Unavailable'} ({hw.host.cpu_count} threads)</dd>
      <dt>Memory</dt><dd>{hw.host.memory_total_mb ? `${(hw.host.memory_total_mb / 1024).toFixed(1)} GiB total, ${(hw.host.memory_available_mb / 1024).toFixed(1)} GiB available` : 'Unavailable'}</dd>
      <dt>Storage</dt><dd>{hw.storage.free_gb ?? '?'} GiB free of {hw.storage.total_gb ?? '?'} GiB</dd>
      <dt>Listening on</dt><dd>{hw.bind.host}:{hw.bind.port}</dd>
    </dl>
    <h2 style="font-size:16px;margin-bottom:8px">Accelerators</h2>
    {#if hw.gpus?.length}<ul class="plain">{#each hw.gpus as g}<li><span class="mono">{g.name}</span>, {g.unified_memory ? 'unified memory shared with system RAM' : `${g.vram_total_mb} MiB VRAM`}, driver {g.driver}</li>{/each}</ul>
    {:else}<p class="small muted">{hw.gpu_note || 'No GPU detected.'} Models can still run on CPU if your inference engine supports it.</p>{/if}
    <h2 style="font-size:16px;margin:16px 0 8px">Inference engines found on this machine</h2>
    {#if hw.backends.length}<ul class="plain">{#each hw.backends as b}<li>{b.label} at <span class="mono">{b.base_url}</span> <span class="chip {b.reachable ? 'ok' : 'warn'}">{b.reachable ? 'responding' : b.detail}</span></li>{/each}</ul>
    {:else}<p class="small muted">None found on the usual local ports. You can still connect one by address on the next step.</p>{/if}
    <div class="row" style="margin-top:16px"><button class="btn" onclick={back}>Back</button><button class="btn primary" onclick={next}>Continue</button></div>

  {:else if step === 4}
    <h1>Models</h1>
    <p class="muted">Connect an inference engine and choose which of its models people may use. You can skip this and connect one later.</p>
    <label class="row" style="gap:8px;margin-bottom:12px"><input type="checkbox" bind:checked={be.on} /> Connect an engine now</label>
    {#if be.on}
      <div class="grid">
        <div class="field"><label for="ad">Engine type</label><select id="ad" class="input" bind:value={be.adapter}>{#each hw.adapters as a}<option value={a.id}>{a.name}</option>{/each}</select></div>
        <div class="field"><label for="bu">Address</label><input id="bu" class="input mono" bind:value={be.base_url} oninput={() => (be.hint_id = "")} placeholder="http://127.0.0.1:8080/v1" /></div>
        {#if be.hint_id}<div class="field"><span class="label">API key / token</span><span class="hint">Supplied by the installer for this address. It stays on the server and is never shown here.</span></div>
        {:else}<div class="field"><label for="bk">API key / token (optional)</label><input id="bk" class="input" type="password" bind:value={be.api_key} autocomplete="off" /><span class="hint">Stored encrypted; never shown again.</span></div>{/if}
      </div>
      <label class="row" style="gap:8px;margin-bottom:12px"><input type="checkbox" bind:checked={be.external} /> This engine is an external service outside my network</label>
      <p class="hint">{hw.adapters.find((a: any) => a.id === be.adapter)?.notes}</p>
      <button class="btn" onclick={testBackend} disabled={busy || !be.base_url}>{busy ? 'Testing…' : 'Test connection and list models'}</button>
      {#if probe}
        {#if !probe.health.ok}<div class="notice bad" role="alert" style="margin-top:12px"><div>Could not connect: {probe.health.detail}</div></div>
        {:else}
          <div class="notice ok" style="margin-top:12px"><div>Connected{probe.health.version ? `, version ${probe.health.version}` : ''}. {probe.models.length} model{probe.models.length === 1 ? '' : 's'} found.</div></div>
          {#if probe.models.length}
            <fieldset class="fs"><legend>Models people may use</legend>
              {#each probe.models as m}<label class="row" style="gap:8px"><input type="checkbox" value={m.real_id} bind:group={enable} /> <span class="mono small">{m.real_id.split('/').pop()}</span> {#if m.meta.params}<span class="chip">{Math.round(m.meta.params / 1e6)} M params</span>{/if}{#if m.external}<span class="chip warn">external</span>{/if}</label>{/each}
            </fieldset>
            <div class="field" style="margin-top:12px"><label for="sm">Smoke test with</label><select id="sm" class="input" bind:value={smoke}><option value="">Skip the test</option>{#each enable as m}<option value={m}>{m.split('/').pop()}</option>{/each}</select><span class="hint">Sends one short message when you launch and reports whether a reply came back.</span></div>
          {/if}
        {/if}
      {/if}
    {/if}
    <div class="row" style="margin-top:16px"><button class="btn" onclick={back}>Back</button><button class="btn primary" onclick={next} disabled={be.on && !!be.base_url && !probe}>Continue</button></div>
    {#if be.on && be.base_url && !probe}<p class="hint">Test the connection first, or untick “Connect an engine now”.</p>{/if}

  {:else if step === 5}
    <h1>People and technical visibility</h1>
    <p class="muted">By default people see a simple chat. Choose how much technical detail ordinary Members may read. Reading is never operating: none of these let anyone load models or change servers.</p>
    <div class="stack" role="radiogroup" aria-label="Technical visibility for Members">
      {#each TECH as [k, l, d]}<label class="opt" class:sel={techPreset === k}><input type="radio" name="tp" checked={techPreset === k} onchange={() => (techPreset = k)} /><div><strong>{l}</strong><div class="small muted">{d}</div></div></label>{/each}
    </div>
    <p class="hint" style="margin-top:12px">Individual fields can be granted or removed for single people, teams and roles afterwards. Teams, roles and invitations are managed in Administration once you launch.</p>
    <div class="row"><button class="btn" onclick={back}>Back</button><button class="btn primary" onclick={next}>Continue</button></div>

  {:else if step === 6}
    <h1>Privacy, retention and limits</h1>
    <div class="grid">
      <div class="field"><label for="pm">Privacy mode</label><select id="pm" class="input" bind:value={v['privacy.mode']}><option value="standard">Standard private</option><option value="user_controlled">User-controlled history</option><option value="enterprise">Enterprise-managed</option></select></div>
      <div class="field"><label for="rt">Keep chat history for</label><select id="rt" class="input" bind:value={v['privacy.chat_retention_days']}>{#each retentionOpts as [n, l]}<option value={n}>{l}</option>{/each}</select><span class="hint">Applies to conversations created from now on.</span></div>
      <div class="field"><label for="dm">Messages per person per day</label><input id="dm" class="input" type="number" min="0" bind:value={v['quota.daily_messages']} /><span class="hint">0 means no limit.</span></div>
    </div>
    <label class="row" style="gap:8px;margin-bottom:8px"><input type="checkbox" bind:checked={v['privacy.ephemeral_allowed']} /> Allow “no-save” conversations</label>
    <label class="row" style="gap:8px;margin-bottom:8px"><input type="checkbox" bind:checked={v['chat.attachments']} /> Allow text-file attachments</label>
    <label class="row" style="gap:8px;margin-bottom:8px"><input type="checkbox" bind:checked={v['privacy.external_egress']} /> Allow models that run on external services</label>
    {#if v['privacy.external_egress']}<div class="notice warn"><div class="small">Messages sent to those models leave your network in readable form. People are told so in the chat.</div></div>{/if}
    <div class="field"><label for="pn">Notice shown in the privacy panel (optional)</label><textarea id="pn" class="input" bind:value={v['privacy.notice']}></textarea></div>
    <p class="hint">Chat text is stored encrypted at rest with a key kept on this server. It is readable by the model engine while it answers, so Semechka never describes chats as end-to-end encrypted.</p>
    <div class="row"><button class="btn" onclick={back}>Back</button><button class="btn primary" onclick={next}>Continue</button></div>

  {:else if step === 7}
    <h1>Access and sign-up</h1>
    <div class="grid">
      <div class="field"><label for="ex">Who can reach the server</label><select id="ex" class="input" bind:value={v['access.exposure']}><option value="loopback">This machine only</option><option value="lan">Other devices on my network (plain HTTP)</option></select><span class="hint">Takes effect the next time Semechka starts. Use HTTPS in front of it before real use beyond a trusted network.</span></div>
      <div class="field"><label for="sm2">How people get an account</label><select id="sm2" class="input" bind:value={v['signup.mode']}><option value="admin">Created by an administrator only</option><option value="invite">By invitation</option><option value="domain">Anyone with an approved email domain</option><option value="public">Anyone may register</option></select></div>
    </div>
    {#if v['signup.mode'] === 'domain'}<div class="field"><label for="dd">Approved domains (comma separated)</label><input id="dd" class="input mono" bind:value={domainsText} placeholder="example.org, lab.example" /></div>{/if}
    {#if v['signup.mode'] === 'public'}<div class="notice warn"><div class="small">Public registration exposes this service to strangers. Keep admin approval on and daily limits low.</div></div>{/if}
    {#if v['signup.mode'] === 'public' || v['signup.mode'] === 'domain'}<label class="row" style="gap:8px;margin-bottom:8px"><input type="checkbox" bind:checked={v['signup.require_approval']} /> New accounts need administrator approval</label>{/if}
    <label class="row" style="gap:8px;margin-bottom:8px"><input type="checkbox" bind:checked={v['auth.mfa_required_admins']} /> Require multi-factor authentication for administrators</label>
    {#if v['auth.mfa_required_admins']}<div class="notice info"><div class="small">You will be asked to set up an authenticator app straight after launch, before using administration.</div></div>{/if}
    <p class="hint">Email verification and password-reset emails need SMTP, which you can configure after launch.</p>
    <div class="row"><button class="btn" onclick={back}>Back</button><button class="btn primary" onclick={next}>Review</button></div>

  {:else if step === 8}
    <h1>Review and launch</h1>
    <div class="tablewrap"><table class="t"><caption>These values become settings you can search and change later.</caption><tbody>
      <tr><th scope="row">Owner</th><td>{owner.display_name}, {owner.email}</td></tr>
      <tr><th scope="row">Preset</th><td>{hw.presets[preset].label}</td></tr>
      <tr><th scope="row">Names</th><td>{v['brand.org_name']} / {v['brand.app_name']}</td></tr>
      <tr><th scope="row">Engine</th><td>{be.on && probe?.health.ok ? `${be.adapter} at ${be.base_url}, ${enable.length} model${enable.length === 1 ? '' : 's'} enabled` : 'Not connected yet'}</td></tr>
      <tr><th scope="row">Technical visibility</th><td>{techPreset}</td></tr>
      <tr><th scope="row">History</th><td>{retentionOpts.find((o) => o[0] === v['privacy.chat_retention_days'])?.[1]}</td></tr>
      <tr><th scope="row">Exposure</th><td>{v['access.exposure'] === 'lan' ? 'Network (after restart)' : 'This machine only'}</td></tr>
      <tr><th scope="row">Sign-up</th><td>{v['signup.mode']}{v['signup.require_approval'] && ['public', 'domain'].includes(v['signup.mode']) ? ', with approval' : ''}</td></tr>
      <tr><th scope="row">MFA for admins</th><td>{v['auth.mfa_required_admins'] ? 'Required' : 'Not required'}</td></tr>
    </tbody></table></div>
    <div class="row" style="margin-top:16px"><button class="btn" onclick={back} disabled={busy}>Back</button><button class="btn primary" onclick={launch} disabled={busy}>{busy ? 'Launching…' : 'Launch'}</button></div>

  {:else}
    <h1>Ready</h1>
    <div class="notice ok"><div>The owner account exists and you are signed in. The setup token has been retired.</div></div>
    <ul class="plain">
      <li>Models enabled for people: <strong>{result?.models_enabled ?? 0}</strong></li>
      {#if result?.smoke}<li>Model test: {#if result.smoke.ok}<span class="chip ok">passed</span> reply “{result.smoke.reply}”{:else}<span class="chip bad">failed</span> {result.smoke.error ?? 'no reply came back'}{/if}</li>{/if}
      {#if result?.backend_error}<li class="err">Engine: {result.backend_error}</li>{/if}
      {#if result?.tech_error}<li class="err">{result.tech_error}</li>{/if}
      {#if v['access.exposure'] === 'lan'}<li>Network exposure applies after you restart Semechka.</li>{/if}
    </ul>
    <button class="btn primary" onclick={() => goto('/')}>Open {v['brand.app_name']}</button>
  {/if}
  </div>
</main>
<style>
  .wiz { max-width: 820px; margin: 0 auto; padding: 16px 16px 64px; }
  header { display: flex; align-items: baseline; justify-content: space-between; padding: 12px 0 16px; }
  .steps { list-style: none; display: flex; gap: 4px; padding: 0; margin: 0 0 24px; overflow-x: auto; border-bottom: 1px solid var(--border); }
  .steps li { display: flex; gap: 6px; align-items: center; padding: 8px 10px; color: var(--fg-muted); font-size: 13px; border-bottom: 2px solid transparent; white-space: nowrap; }
  .steps li.on { color: var(--fg); border-bottom-color: var(--accent); font-weight: 600; } .steps li.done { color: var(--success); }
  .n { font-size: 12px; }
  @media (max-width: 640px) { .steps .t { display: none; } .steps li.on .t { display: inline; } }
  h1 { margin-bottom: 8px; } .body > p, .body > .field, .body > .grid { max-width: 640px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 0 16px; }
  .opt { display: flex; gap: 12px; align-items: flex-start; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); cursor: pointer; }
  .opt.sel { border-color: var(--accent); background: var(--accent-soft); } .opt input { margin-top: 3px; }
  .plain { padding-left: 18px; display: grid; gap: 6px; } .fs { border: 1px solid var(--border); border-radius: var(--radius); margin-top: 12px; padding: 12px; display: grid; gap: 6px; }
  th[scope='row'] { width: 180px; position: static !important; background: transparent !important; } table.t td { white-space: normal; }
</style>
