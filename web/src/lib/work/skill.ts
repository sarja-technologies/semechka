// Markdown skills: front matter parsing and writing, and the checks the editor makes before the server does.
// A skill is a .md file with YAML front matter (name, description, category; any other key is ignored) and a body.

export const CATEGORIES = ['coding', 'research', 'image-generation', 'video-production', 'media-editing', 'document-creation', 'data-analysis', 'general'] as const;
export const categoryLabel = (c: string) => { const t = c.replace(/-/g, ' '); return t.charAt(0).toUpperCase() + t.slice(1); };

export const NAME_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;
export const BODY_MAX = 64 * 1024;
export const FILES_MAX = 1024 * 1024;
export const DESCRIPTION_MAX = 300;

export type FrontMatter = { name: string; description: string; category: string };
export type ParsedSkill = { meta: Partial<FrontMatter>; body: string; hasFrontMatter: boolean; problems: string[] };

function unquote(v: string): string {
  v = v.trim();
  if (v.length >= 2 && v[0] === '"' && v.endsWith('"')) { try { return JSON.parse(v); } catch { return v.slice(1, -1); } }
  if (v.length >= 2 && v[0] === "'" && v.endsWith("'")) return v.slice(1, -1).replace(/''/g, "'");
  return v.replace(/\s+#.*$/, '');
}

/** Read `---` delimited front matter. Only flat `key: value` lines are understood; nested values, lists and unknown
 *  keys are ignored on purpose (a skill can never carry settings, only a name, a description and a category). */
export function parseSkillMarkdown(src: string): ParsedSkill {
  const text = src.replace(/^﻿/, '').replace(/\r\n?/g, '\n');
  const m = text.match(/^---[ \t]*\n([\s\S]*?)\n---[ \t]*(?:\n|$)/);
  if (!m) return { meta: {}, body: text, hasFrontMatter: false, problems: ['The file has no front matter. It must start with a line of three dashes, then name, description and category, then three dashes again.'] };
  const meta: Partial<FrontMatter> = {};
  const lines = m[1].split('\n');
  for (let i = 0; i < lines.length; i++) {
    const kv = lines[i].match(/^([A-Za-z_][\w-]*):[ \t]*(.*)$/);
    if (!kv) continue;
    const key = kv[1].toLowerCase();
    let val = kv[2];
    if (/^[|>][+-]?$/.test(val.trim())) { // a folded or literal block: take the indented lines that follow
      const parts: string[] = [];
      while (i + 1 < lines.length && /^[ \t]+\S|^\s*$/.test(lines[i + 1])) parts.push(lines[++i].trim());
      val = parts.join(val.trim().startsWith('|') ? '\n' : ' ').trim();
      if (key === 'name' || key === 'description' || key === 'category') (meta as any)[key] = val;
      continue;
    }
    if (key === 'name' || key === 'description' || key === 'category') (meta as any)[key] = unquote(val);
  }
  const body = text.slice(m[0].length).replace(/^\n+/, '');
  const problems = validateMeta(meta);
  return { meta, body, hasFrontMatter: true, problems };
}

export function validateMeta(meta: Partial<FrontMatter>): string[] {
  const p: string[] = [];
  if (!meta.name) p.push('The front matter needs a name.');
  else if (!NAME_RE.test(meta.name)) p.push('The name may use lower-case letters, digits and hyphens only, and must start and end with a letter or digit.');
  if (!meta.description) p.push('The front matter needs a one-line description.');
  else if (meta.description.length > DESCRIPTION_MAX) p.push(`The description is longer than ${DESCRIPTION_MAX} characters.`);
  if (meta.category && !(CATEGORIES as readonly string[]).includes(meta.category)) p.push(`The category "${meta.category}" is not one of the known categories.`);
  return p;
}

const scalar = (v: string) => (v === '' || /^[\s>|&*!%@`'"#,\[\]{}?-]|[:#]\s|\s$|^(true|false|null|yes|no|~|\d[\d.]*)$/i.test(v) || /\n/.test(v) ? JSON.stringify(v) : v);

/** The file a person downloads: front matter followed by the body. */
export function serializeSkill(meta: FrontMatter, body: string): string {
  return `---\nname: ${scalar(meta.name)}\ndescription: ${scalar(meta.description.replace(/\s*\n\s*/g, ' '))}\ncategory: ${scalar(meta.category)}\n---\n\n${body.replace(/^\n+/, '')}${body.endsWith('\n') ? '' : '\n'}`;
}

/** Turn free text into a valid skill name, for suggesting one from a title. */
export function slugify(s: string): string {
  return s.toLowerCase().normalize('NFKD').replace(/[^\x00-\x7f]/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 64).replace(/-+$/g, '');
}

export type SkillDraft = { name: string; title: string; description: string; category: string; body_md: string; files: { name: string; content: string }[] };
const FILE_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const utf8 = (s: string) => (typeof TextEncoder !== 'undefined' ? new TextEncoder().encode(s).length : s.length);

/** Problems keyed by field, so each message can sit beside its input. Empty object means ready to save. */
export function validateDraft(d: SkillDraft): Record<string, string> {
  const e: Record<string, string> = {};
  if (!d.name) e.name = 'Give the skill a name.';
  else if (!NAME_RE.test(d.name)) e.name = 'Use lower-case letters, digits and hyphens only, for example data-cleanup.';
  if (!d.title.trim()) e.title = 'Give the skill a title.';
  if (!d.description.trim()) e.description = 'Add a one-line description. The agent sees only this line until it opens the skill.';
  else if (d.description.length > DESCRIPTION_MAX) e.description = `Keep the description under ${DESCRIPTION_MAX} characters.`;
  if (!(CATEGORIES as readonly string[]).includes(d.category)) e.category = 'Choose a category.';
  if (!d.body_md.trim()) e.body = 'The skill needs some instructions.';
  else if (utf8(d.body_md) > BODY_MAX) e.body = `The instructions are over the ${BODY_MAX / 1024} KB limit.`;
  const seen = new Set<string>();
  let total = 0;
  for (const f of d.files) {
    total += utf8(f.content);
    if (!FILE_NAME_RE.test(f.name)) { e.files = `"${f.name || 'unnamed'}" is not a valid file name. Use letters, digits, dots, hyphens and underscores.`; break; }
    if (seen.has(f.name)) { e.files = `Two files are called "${f.name}".`; break; }
    seen.add(f.name);
  }
  if (!e.files && total > FILES_MAX) e.files = 'Supporting files are over the 1 MB limit together.';
  return e;
}
