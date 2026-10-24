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
      return `<pre><code class="hljs${l ? ' language-' + esc(l) : ''}">${body}</code></pre>`;
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
