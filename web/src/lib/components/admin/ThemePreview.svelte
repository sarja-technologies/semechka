<script lang="ts">
  // A small, inert copy of the chat interface painted with the tokens being edited (never the live page's tokens).
  import { writeTokens, type TokenValues, type ThemeBundle } from '$lib/theme/theme';
  let { values, variant, bundle = {}, appName = 'Assistant' }: { values: TokenValues; variant: 'light' | 'dark'; bundle?: ThemeBundle; appName?: string } = $props();
  let box: HTMLDivElement | undefined = $state();
  $effect(() => { if (box) writeTokens(box, values, variant, bundle); });
  const right = $derived(bundle.user_align !== 'left');
</script>

<div class="pvw" bind:this={box} role="img" aria-label={`Preview of the ${variant} variant: a conversation asking for the capital of France`} data-density={bundle.density ?? 'comfortable'}>
  <div class="ph"><span class="pb">{appName}</span><span class="pav">AM</span></div>
  <div class="pbody">
    <div class="pside">
      <span class="pnew">New conversation</span>
      <span class="pg">Today</span>
      <span class="pit sel">Capital of France</span>
      <span class="pit hov">Trip planning</span>
      <span class="pit">Quarterly summary</span>
    </div>
    <div class="pmain">
      <div class="pthread">
        <div class="pu" class:left={!right}><span>What is the capital of France?</span></div>
        <div class="pa">
          <span class="pwho"><span class="pmark">Qw</span> Model</span>
          <p>The capital of France is <strong>Paris</strong>. It has been the capital since <code>987</code> and sits on the Seine. <span class="plink">Read more</span></p>
          <div class="pcard"><span class="pchip ok">Complete</span><span class="pchip warn">Stopped</span><span class="pchip bad">Failed</span><span class="pchip info">Queued</span></div>
        </div>
      </div>
      <div class="pcomp">
        <span class="pph">Message</span>
        <div class="pbar"><span class="pbtn">Chat</span><span class="pmodel">Model</span><span class="psp"></span><span class="pbtn">Cancel</span><span class="pbtn primary">Send</span></div>
      </div>
    </div>
  </div>
</div>

<style>
  .pvw { --surface: var(--card-bg); --surface-2: var(--bg-2);
    background: var(--bg); color: var(--fg); font: 400 var(--font-size, 14px) / 1.5 var(--font); border: 1px solid var(--border-strong); border-radius: var(--radius);
    overflow: hidden; display: flex; flex-direction: column; min-height: 380px; user-select: none; color-scheme: inherit; }
  .ph { display: flex; align-items: center; justify-content: space-between; padding: 0 12px; height: 40px; background: var(--header-bg); border-bottom: 1px solid var(--border); }
  .pb { font-weight: 600; font-size: 14px; }
  .pav { width: 24px; height: 24px; border-radius: 50%; display: grid; place-items: center; font-size: 10px; font-weight: 600; background: var(--btn-primary-bg); color: var(--btn-primary-fg); }
  .pbody { flex: 1; display: flex; min-height: 0; }
  .pside { width: 150px; flex: none; background: var(--sidebar-bg); border-right: 1px solid var(--border); padding: 8px; display: flex; flex-direction: column; gap: 2px; font-size: 12px; }
  .pnew { border: 1px solid var(--border-strong); border-radius: var(--radius-sm); padding: 4px 8px; margin-bottom: 6px; }
  .pg { color: var(--fg-muted); font-size: 11px; font-weight: 600; margin: 6px 4px 2px; }
  .pit { padding: 4px 8px; border-radius: var(--radius-sm); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .pit.sel { background: var(--selected); font-weight: 600; } .pit.hov { background: var(--hover); }
  .pmain { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .pthread { flex: 1; padding: 14px 16px; display: grid; gap: 12px; align-content: start; }
  .pu { display: flex; justify-content: flex-end; } .pu.left { justify-content: flex-start; }
  .pu span { background: var(--user-msg-bg); border-radius: var(--radius); padding: 6px 12px; font-size: 13px; }
  .pa { background: var(--assistant-msg-bg); font-size: 13px; }
  .pa p { margin: 4px 0 8px; }
  .pa code { font-family: var(--mono); background: var(--bg-2); padding: 1px 4px; border-radius: 4px; font-size: 12px; }
  .pa .plink { color: var(--link); text-decoration: underline; text-underline-offset: 2px; }
  .pwho { display: inline-flex; align-items: center; gap: 6px; font-weight: 600; font-size: 12px; }
  .pmark { width: 16px; height: 16px; border-radius: 4px; background: #3D5A80; color: #fff; font-size: 8px; display: grid; place-items: center; }
  .pcard { display: flex; flex-wrap: wrap; gap: 4px; background: var(--card-bg); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px; }
  .pchip { font-size: 11px; border: 1px solid; border-radius: 4px; padding: 0 6px; }
  .pchip.ok { color: var(--success); } .pchip.warn { color: var(--warn); } .pchip.bad { color: var(--danger); } .pchip.info { color: var(--info); }
  .pcomp { margin: 0 16px 12px; background: var(--composer-bg); border: 1px solid var(--border-strong); border-radius: calc(var(--radius) + 4px); padding: 8px 10px; display: grid; gap: 6px; }
  .pph { color: var(--fg-muted); font-size: 13px; }
  .pbar { display: flex; align-items: center; gap: 6px; font-size: 12px; }
  .pmodel { color: var(--fg); font-weight: 500; } .psp { flex: 1; }
  .pbtn { border: 1px solid var(--border-strong); background: var(--btn-bg); color: var(--btn-fg); border-radius: var(--radius-sm); padding: 2px 10px; }
  .pbtn.primary { background: var(--btn-primary-bg); border-color: var(--btn-primary-bg); color: var(--btn-primary-fg); }
  .pvw[data-density='compact'] .pthread { gap: 8px; padding: 10px 12px; }
  @media (max-width: 600px) { .pside { display: none; } }
</style>
