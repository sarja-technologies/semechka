import { Marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js/lib/core';
import js from 'highlight.js/lib/languages/javascript';
import ts from 'highlight.js/lib/languages/typescript';
import py from 'highlight.js/lib/languages/python';
import bash from 'highlight.js/lib/languages/bash';
import json from 'highlight.js/lib/languages/json';
import css from 'highlight.js/lib/languages/css';
import xml from 'highlight.js/lib/languages/xml';
import sql from 'highlight.js/lib/languages/sql';
import rust from 'highlight.js/lib/languages/rust';
import go from 'highlight.js/lib/languages/go';
import yaml from 'highlight.js/lib/languages/yaml';
import c from 'highlight.js/lib/languages/c';
for (const [n, l] of Object.entries({ javascript: js, typescript: ts, python: py, bash, json, css, xml, sql, rust, go, yaml, c })) hljs.registerLanguage(n, l);
hljs.registerAliases(['js', 'jsx'], { languageName: 'javascript' });
hljs.registerAliases(['ts', 'tsx'], { languageName: 'typescript' });
hljs.registerAliases(['sh', 'shell', 'zsh'], { languageName: 'bash' });
hljs.registerAliases(['html', 'svg'], { languageName: 'xml' });
hljs.registerAliases(['py'], { languageName: 'python' });

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const md = new Marked({
  gfm: true, breaks: false,
  renderer: {
    code({ text, lang }: { text: string; lang?: string }) {
      const l = (lang || '').split(/\s/)[0];
      const body = l && hljs.getLanguage(l) ? hljs.highlight(text, { language: l, ignoreIllegals: true }).value : esc(text);
      // The Copy button is wired by event delegation in Markdown.svelte; it copies the code's text content.
      return `<div class="codeblock"><div class="codehead"><span class="lang">${l ? esc(l) : 'text'}</span><button type="button" class="copy-code">Copy</button></div><pre><code class="hljs${l ? ' language-' + esc(l) : ''}">${body}</code></pre></div>`;
    },
    html({ text }: { text: string }) { return esc(text); }  // raw HTML from a model is shown as text, never rendered
  }
});

DOMPurify.addHook('afterSanitizeAttributes', (node: Element) => {
  if (node.tagName === 'A') { node.setAttribute('rel', 'noopener noreferrer nofollow'); node.setAttribute('target', '_blank'); }
});

export function renderMarkdown(src: string): string {
  const html = md.parse(src || '', { async: false }) as string;
  return DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, FORBID_TAGS: ['style', 'form', 'input', 'img', 'iframe'], FORBID_ATTR: ['style'] });
}

// ---- caching and streaming ------------------------------------------------------------------------------
// Rendering is the expensive part of showing a reply (parse, highlight, sanitise). Finished messages are rendered once
// per distinct text; while a reply streams, finished blocks are frozen and only the unfinished tail is re-rendered.
const CACHE_MAX = 300;
const cache = new Map<string, string>();

export function renderCached(src: string): string {
  const hit = cache.get(src);
  if (hit !== undefined) { cache.delete(src); cache.set(src, hit); return hit; }
  const html = renderMarkdown(src);
  cache.set(src, html);
  if (cache.size > CACHE_MAX) cache.delete(cache.keys().next().value as string);
  return html;
}

const FENCE = /^\s{0,3}(`{3,}|~{3,})/;

/** Offset after the last blank line that is outside a fenced code block, searching from `from`. Text before it is made of
 *  complete Markdown blocks that the rest of the reply cannot change (block structure only looks forward). */
export function stableBoundary(text: string, from = 0): number {
  let fence: string | null = null, last = from, pos = 0;
  for (const line of text.split('\n')) {
    const end = pos + line.length + 1;
    if (end > text.length) break; // the final line is still being written
    const m = line.match(FENCE);
    if (m) { if (!fence) fence = m[1][0]; else if (m[1][0] === fence) fence = null; }
    else if (!fence && line.trim() === '' && end > from) {
      // a blank line inside a list or before an indented continuation could still join blocks; require the next
      // line to start a new top-level block before freezing
      const next = text.slice(end).split('\n', 1)[0];
      if (next !== undefined && end < text.length && !/^(\s{2,}|\t|[-*+] |\d+[.)] )/.test(next)) last = end;
    }
    pos = end;
  }
  return last;
}

/** Delegated click handler for the Copy button that every rendered code block carries. */
export async function onCodeCopyClick(e: MouseEvent) {
  const btn = (e.target as HTMLElement).closest<HTMLButtonElement>('button.copy-code');
  if (!btn) return;
  const code = btn.closest('.codeblock')?.querySelector('code')?.textContent ?? '';
  try { await navigator.clipboard.writeText(code); btn.textContent = 'Copied'; }
  catch { btn.textContent = 'Copy unavailable'; }
  setTimeout(() => { btn.textContent = 'Copy'; }, 1600);
}
