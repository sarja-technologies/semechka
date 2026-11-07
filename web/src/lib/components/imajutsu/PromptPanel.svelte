<script lang="ts">
  // Panel: Prompt. The words that say what to make or change. For iPhone Photo the app writes the model's prompt from the scene chips; the final
  // prompt the server built is always shown here, read-only, so nothing the model receives is hidden.
  import PanelCard from './PanelCard.svelte';
  import { SCENES } from '$lib/imajutsu/spec';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s, n = 0 }: { s: Imajutsu; n?: number } = $props();
  const key = $derived(s.key), sp = $derived(s.spec);
  const title = $derived(({ generate: 'Describe the picture', iphone_photo: 'Describe the photo', edit: 'What should change?', replace_subject: 'What to replace', inpaint: 'What goes in the painted part?', outpaint: 'What is in the new area? (optional)', reference: 'Describe the new picture (optional)', restyle: 'Which style?', advanced: 'Prompt' } as Record<string, string>)[key] ?? 'Prompt');
  const ph = $derived(({ generate: 'A fishing boat at dawn, calm water, low mist', iphone_photo: 'My friend laughing at a street market', edit: 'Make the jacket green and remove the bag', replace_subject: 'Replace the woman in the red coat', inpaint: 'A wooden bench', outpaint: 'More of the beach and the sea to the right', reference: 'The same character, walking in a park', restyle: 'Watercolour, soft edges, muted colours', advanced: 'What you want' } as Record<string, string>)[key] ?? '');
  const hint = $derived(key === 'replace_subject' ? 'Say WHO or WHAT to replace. The pictures you add under References are what goes in its place.' : key === 'inpaint' ? 'The painted part tells the model WHERE. The words tell it WHAT.' : key === 'outpaint' ? 'Leave it empty to continue the scene naturally.' : '');
  const final = $derived(s.plan?.prompt?.final ?? '');
  const sentences = $derived(s.plan?.prompt?.sentences ?? []);
  const scenes = $derived(s.caps?.presets?.find((p) => p.id === 'iphone_photo')?.scene_chips?.length ? s.caps!.presets.find((p) => p.id === 'iphone_photo')!.scene_chips! : SCENES);
  const fin = $derived(s.caps?.presets?.find((p) => p.id === 'iphone_photo')?.finish);
</script>

<PanelCard id="prompt" {title} {n} {hint}>
  <label class="st-field"><span class="sr-only">{title}</span>
    <textarea class="input prompt" rows={key === 'generate' || key === 'iphone_photo' ? 4 : 3} data-testid="prompt-input" value={sp.prompt} oninput={(e) => s.edit((x) => { x.prompt = e.currentTarget.value; })} placeholder={ph} maxlength="4000" aria-label={title}></textarea></label>
  {#if key === 'replace_subject'}
    <p class="st-note">If you also mark the area on the picture (Selection), the model knows exactly where; the words still say what.</p>
  {/if}

  {#if key === 'iphone_photo'}
    <div class="st-sec" style="padding:0;border:0">
      <h3 id="scene-h">Scene</h3>
      <div class="st-seg" role="group" aria-labelledby="scene-h" data-testid="scene-chips">
        {#each scenes as c (c.id)}<button type="button" aria-pressed={sp.preset.scenes.includes(c.id)} onclick={() => s.toggleChip(c.id)}>{c.label}</button>{/each}
      </div>
      <p class="st-note">You do not need to write phone words. The app writes a natural description of the light and framing for the model you chose, and shows it below.</p>
      <label class="fin"><input type="checkbox" checked={sp.preset.finish} onchange={(e) => s.edit((x) => { x.preset.finish = e.currentTarget.checked; })} data-testid="phone-finish" />
        <span><strong>{fin?.label ?? 'Phone finish'}</strong> <span class="st-note">{fin?.note ?? 'A subtle photographic touch (gentle contrast curve, fine grain, light sharpening). It is not AI, it is made as a separate copy, and you can switch it off.'}</span></span></label>
    </div>
  {/if}

  {#if final || key === 'iphone_photo'}
    <details class="st-details" open={key === 'iphone_photo'} data-testid="final-prompt-box">
      <summary>The prompt the model receives</summary>
      <div class="st-in">
        {#if final}<p class="final" data-testid="final-prompt" aria-label="Final prompt, read only">{final}</p>
        {:else}<p class="st-note" role="status">{s.planBusy ? 'Writing the prompt…' : 'It appears here as soon as the server has built it.'}</p>{/if}
        {#if sentences.length}
          <ul class="sent" aria-label="Sentences added for the pictures" data-testid="ref-sentences">
            {#each sentences as x, i (i)}<li><span class="st-badge">{x.ref === 'source' ? 'Your picture' : `Reference ${x.position ?? ''}`}</span> {x.sentence}</li>{/each}
          </ul>
        {/if}
        <p class="st-note">Read only. It is built from your words{key === 'iphone_photo' ? ', the scene' : ''} and the roles you gave your pictures.</p>
      </div>
    </details>
  {/if}

  {#if s.advanced}
    <div class="adv">
      <label class="st-field"><span>Negative prompt (what to avoid)</span>
        <textarea class="input" rows="2" data-testid="negative-input" value={sp.negative} oninput={(e) => s.edit((x) => { x.negative = e.currentTarget.value; })} placeholder="blurry, extra fingers" maxlength="4000"></textarea></label>
      <p class="st-note">Only some models read a negative prompt. If the chosen one does not, the plan says so.</p>
    </div>
  {/if}
</PanelCard>

<style>
  .prompt { min-height: 96px; font-size: 14px; resize: vertical; }
  .fin { display: flex; gap: 10px; align-items: flex-start; font-size: 13px; cursor: pointer; padding: 6px 0; } .fin input { margin-top: 3px; width: 18px; height: 18px; flex: none; }
  .final { margin: 0; padding: 10px 12px; border-radius: var(--radius-sm); background: var(--m-card); border: 1px solid var(--border); font-size: 13px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; }
  .sent { list-style: none; margin: 0; padding: 0; display: grid; gap: 4px; font-size: 12.5px; } .sent li { display: flex; gap: 8px; align-items: baseline; }
  .adv { display: grid; gap: var(--s2); padding-top: var(--s2); border-top: 1px dashed var(--border-strong); }
</style>
