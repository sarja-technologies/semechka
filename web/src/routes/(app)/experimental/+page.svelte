<script lang="ts">
  // Everything advanced, in one list. Each tool opens on its own screen; a tool the server does not offer to this person is not listed.
  import { app } from '$lib/session.svelte';
  import Icon from '$lib/components/Icon.svelte';
  const o = $derived(app.me?.ui?.operations ?? {});
  type Tool = { href: string; icon: string; title: string; what: string; on: boolean };
  const groups = $derived<{ title: string; tools: Tool[] }[]>([
    { title: 'Pictures', tools: [
      { href: '/imajutsu', icon: 'image', title: 'Picture studio', what: 'Edit a picture with words, replace a subject, paint over or extend an area, work from reference pictures, restyle, adjust. Shows exactly what is sent to the model (Imajutsu).', on: !!o.imajutsu?.enabled },
      { href: '/imajutsu?mode=upscale', icon: 'zoomin', title: 'Upscale', what: 'Make a picture larger: plain scaling (no new detail), or AI detail restoration with SeedVR2 or Real-ESRGAN where the server offers them.', on: !!o.imajutsu?.enabled },
      { href: '/imajutsu?mode=iphone_photo', icon: 'phone', title: 'iPhone Photo', what: 'A natural-looking phone photo; the app writes the prompt for the model and keeps the 9:16 shape.', on: !!o.imajutsu?.enabled },
      { href: '/experimental/image', icon: 'sliders', title: 'Advanced image', what: 'The Images screen with the model’s own controls: size, seed, negative prompt and steps.', on: !!o.image?.enabled }
    ] },
    { title: 'In a conversation', tools: [
      { href: '/experimental/studio', icon: 'chat', title: 'Conversation studio', what: 'Make pictures and videos inside a thread: attach several pictures, use editing profiles, edit and make another take. It appears in Chat history too.', on: !!(o.image?.enabled || o.video?.enabled) }
    ] },
    { title: 'Videos', tools: [
      { href: '/openjutsu', icon: 'film', title: 'Video editor', what: 'Replace or edit an object in a video you upload, with reference pictures, masks and a step-by-step plan (OpenJutsu Studio).', on: !!o.openjutsu?.enabled },
      { href: '/experimental/video', icon: 'sliders', title: 'Advanced video', what: 'The Videos screen with the model’s own controls: resolution, frame rate, seed, negative prompt and steps.', on: !!o.video?.enabled },
      { href: '/experimental/rea', icon: 'search', title: 'REA, video reverse engineer', what: 'Upload a video and the engine measures what is visible in it: shots, camera, light, colour and transitions, with a plan you can read.', on: !!o.video?.enabled }
    ] }
  ].map((g) => ({ ...g, tools: g.tools.filter((t) => t.on) })).filter((g) => g.tools.length));
</script>
<svelte:head><title>Experimental</title></svelte:head>
<div class="wrap">
  <h1>Experimental</h1>
  <p class="muted lead">Advanced tools. They give you more control and may be slower, rougher or change without notice. What you make is saved to the Library like everything else.</p>
  {#each groups as g (g.title)}
    <section aria-labelledby={`g-${g.title}`}>
      <h2 id={`g-${g.title}`}>{g.title}</h2>
      <ul>
        {#each g.tools as t (t.href)}
          <li><a href={t.href} class="tool"><span class="ic"><Icon name={t.icon} size={18} /></span><span class="tx"><strong>{t.title}</strong><span class="small muted">{t.what}</span></span><span class="go"><Icon name="chevron" size={16} /></span></a></li>
        {/each}
      </ul>
    </section>
  {:else}
    <div class="notice info" role="status"><div>No experimental tool is available to you. Pictures and videos are made on their own screens.</div></div>
  {/each}
</div>
<style>
  .wrap { max-width: 860px; width: 100%; margin: 0 auto; padding: 24px 24px 48px; display: grid; gap: 8px; align-content: start; }
  .lead { max-width: 62ch; margin-bottom: 12px; }
  section { margin-top: 12px; } h2 { font-size: 14px; color: var(--fg-muted); font-weight: 500; margin-bottom: 6px; }
  ul { list-style: none; margin: 0; padding: 0; border: 1px solid var(--border); border-radius: var(--radius-menu); background: var(--card-bg); overflow: hidden; }
  li + li { border-top: 1px solid var(--border); }
  .tool { display: grid; grid-template-columns: 36px minmax(0, 1fr) 20px; gap: 12px; align-items: center; padding: 14px 16px; color: var(--fg); text-decoration: none; }
  .tool:hover { background: var(--hover); text-decoration: none; } .tool:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .ic { width: 36px; height: 36px; display: grid; place-items: center; border-radius: var(--radius-sm); background: var(--bg-2); color: var(--fg-2); }
  .tx { display: grid; gap: 2px; min-width: 0; } .tx strong { font-weight: 600; font-size: 15px; } .go { color: var(--fg-muted); display: grid; }
  @media (max-width: 899px) { .wrap { padding: 16px 16px 32px; } }
</style>
