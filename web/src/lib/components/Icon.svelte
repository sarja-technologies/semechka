<script lang="ts">
  // Minimal stroke icon set (24x24, 1.75 stroke). Product icons only; never decorative, never emoji.
  let { name, size = 18, label = '' }: { name: string; size?: number; label?: string } = $props();
  const P: Record<string, string> = {
    chat: 'M4 5h16v11H9l-5 4V5z', code: 'M8 7l-5 5 5 5M16 7l5 5-5 5M14 4l-4 16', image: 'M4 5h16v14H4zM4 15l5-5 4 4 3-3 4 4M9 9.5h.01',
    video: 'M3 6h12v12H3zM15 10l6-3v10l-6-3', audio: 'M5 10v4M9 7v10M13 4v16M17 8v8M21 11v2', knowledge: 'M5 4h11a3 3 0 013 3v13H8a3 3 0 01-3-3zM5 17a3 3 0 013-3h11',
    agents: 'M14 7l3-3 3 3-3 3zM4 20l9-9M4 14l3 3', usage: 'M4 20V10M10 20V4M16 20v-7M22 20H2', help: 'M12 17v.01M9.5 9a2.5 2.5 0 115 0c0 2-2.5 2-2.5 4M3 12a9 9 0 1018 0 9 9 0 10-18 0',
    settings: 'M4 7h10M18 7h2M4 17h2M10 17h10M14 4v6M6 14v6', users: 'M9 11a3.5 3.5 0 100-7 3.5 3.5 0 000 7zM2 20c0-3.5 3-6 7-6s7 2.5 7 6M17 5a3 3 0 010 6M22 20c0-2.5-1.5-4.5-4-5.3',
    shield: 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z', cpu: 'M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4', list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
    grip: 'M9 6h.01M15 6h.01M9 12h.01M15 12h.01M9 18h.01M15 18h.01', up: 'M12 19V5M6 11l6-6 6 6', down: 'M12 5v14M6 13l6 6 6-6',
    eyeoff: 'M3 3l18 18M10.6 6.1A9.7 9.7 0 0112 6c6 0 10 6 10 6a17 17 0 01-3.2 3.8M6.6 6.6C3.9 8.3 2 12 2 12s4 7 10 7c1.8 0 3.3-.5 4.6-1.2M9.9 9.9a3 3 0 004.2 4.2',
    plus: 'M12 5v14M5 12h14', trash: 'M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v6M14 11v6', edit: 'M4 20h4L19 9l-4-4L4 16zM13 7l4 4', branch: 'M6 4v10a4 4 0 004 4h4M6 4a2 2 0 100 .01M18 18a2 2 0 100 .01M6 8a4 4 0 004 4h4',
    stop: 'M6 6h12v12H6z', send: 'M4 12l16-8-6 16-3-7z', search: 'M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-5-5', check: 'M5 12l5 5 9-10', x: 'M6 6l12 12M18 6L6 18',
    chevron: 'M9 6l6 6-6 6', chevdown: 'M6 9l6 6 6-6', menu: 'M4 7h16M4 12h16M4 17h16', lock: 'M6 11h12v9H6zM8 11V8a4 4 0 018 0v3', eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z',
    copy: 'M9 9h11v11H9zM5 15V4h11', download: 'M12 4v11M7 11l5 5 5-5M5 20h14', refresh: 'M20 11a8 8 0 00-14-4M4 13a8 8 0 0014 4M4 4v4h4M20 20v-4h-4', sun: 'M12 8a4 4 0 100 8 4 4 0 000-8zM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5',
    moon: 'M20 14A8 8 0 019.5 4 8 8 0 1020 14z', server: 'M4 4h16v6H4zM4 14h16v6H4zM8 7h.01M8 17h.01', key: 'M14 10a4 4 0 11-8 0 4 4 0 018 0zM12.5 13l8 8M17 17.5l2-2', log: 'M6 3h9l4 4v14H6zM9 12h7M9 16h7M9 8h3',
    palette: 'M12 3a9 9 0 100 18c1.5 0 2-1 1.5-2s0-2 1.5-2h2a3 3 0 003-3c0-6-4-11-8-11zM7.5 11h.01M10 7.5h.01M14.5 7.5h.01', nav: 'M3 12l18-9-6 18-3-8z', mail: 'M3 6h18v12H3zM3 7l9 7 9-7',
    info: 'M12 8h.01M11 12h1v5M3 12a9 9 0 1018 0 9 9 0 10-18 0', warn: 'M12 4l10 17H2zM12 10v5M12 18h.01', user: 'M12 12a4 4 0 100-8 4 4 0 000 8zM4 21c0-4 3.5-6 8-6s8 2 8 6',
    history: 'M3 12a9 9 0 109-9 9 9 0 00-7 3.5M3 4v4h4M12 8v5l3 2', attach: 'M20 11l-8.5 8.5a5 5 0 01-7-7L13 4a3.5 3.5 0 015 5l-8.5 8.5a2 2 0 01-3-3L14 7',
    pin: 'M9 4h6l-1 6 4 3v2H6v-2l4-3zM12 15v6', folder: 'M3 6h6l2 2h10v11H3z', sidebar: 'M3 4h18v16H3zM9 4v16',
    more: 'M5 12h.01M12 12h.01M19 12h.01', library: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z', sliders: 'M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1M15 4v4M9 10v4M17 16v4',
    upload: 'M12 16V4M7 9l5-5 5 5M5 20h14', filter: 'M3 5h18l-7 8v6l-4 2v-8z', external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
    star: 'M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8-5.2-2.7-5.2 2.7 1-5.8-4.3-4.1 5.9-.9z', arrowup: 'M12 19V5M5.5 11.5L12 5l6.5 6.5'
  };
</script>
<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden={label ? undefined : 'true'} role={label ? 'img' : undefined} aria-label={label || undefined}><path d={P[name] ?? P.info} /></svg>
