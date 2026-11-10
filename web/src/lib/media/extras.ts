// Experimental tools from the media engine: speech, music, picture tools and 3D (/api/media/extras), plus the engine's video tools (upscale, smooth motion),
// which are editing profiles of the Videos block (/api/media/profile-jobs). Both publish their inputs in the same small vocabulary
// (file / string / enum / int / float / bool), so one form renders either. The server checks everything again; nothing here decides access.
import { get, postForm } from '$lib/api';

export type Group = 'audio' | 'tools' | '3d' | 'video';
export type Spec = {
  type: 'file' | 'string' | 'enum' | 'int' | 'float' | 'bool' | 'json'; label?: string; description?: string; required?: boolean; default?: any;
  min?: number; max?: number; step?: number; values?: any[]; labels?: Record<string, string>; multiline?: boolean; max_length?: number; kind?: 'image' | 'video'; multi?: boolean;
};
export type Tool = {
  id: string; alias: string; description?: string | null; license?: string | null; caution?: string | null; group: Group; output: 'audio' | 'image' | '3d' | 'video';
  ready: boolean; reason?: string | null; inputs_schema: Record<string, Spec>; outputs: string[]; typical_seconds?: number | null; restricted?: boolean;
  via: 'extras' | 'profile';
};
export type Listing = { tools: Tool[]; ephemeral: boolean; retention_days: number; caps: Record<string, any> };

export const GROUPS: Record<Group, { title: string; lead: string; icon: string }> = {
  audio: { title: 'Audio', icon: 'audio', lead: 'Speech from text and music from a description, made on this server. Files are saved to the Library like everything else.' },
  tools: { title: 'Picture tools', icon: 'image', lead: 'Single-purpose picture tools: cut out the subject, split a picture into layers, or borrow the look of a reference picture.' },
  '3d': { title: '3D', icon: 'cube', lead: 'Turn one picture into a 3D relief you can turn around. It shows only the surfaces the camera saw, not a closed object.' },
  video: { title: 'Video tools', icon: 'film', lead: 'Make a video sharper and larger, or smoother with in-between frames. The original stays as it is; the result is a new video.' }
};

/** The engine's video tools among the Videos block's editing profiles: upscale and smooth motion (never the editor, never REA). */
const VIDEO_TOOL = /(video-upscale|smooth-motion)/;
export function videoTools(options: any): Tool[] {
  const out: Tool[] = [];
  for (const m of options?.models ?? []) {
    const p = m.profile;
    if (!p || !VIDEO_TOOL.test(String(p.id ?? ''))) continue;
    out.push({ id: m.id, alias: m.alias, description: m.description ?? p.description ?? null, license: p.license ?? null, caution: p.caution ?? null, group: 'video', output: 'video',
      ready: p.ready !== false, reason: p.reason ?? null, inputs_schema: p.inputs_schema ?? {}, outputs: ['video (MP4)'], typical_seconds: p.typical_seconds ?? null, via: 'profile' });
  }
  return out;
}

export async function loadTools(group: Group): Promise<Listing> {
  if (group === 'video') {
    const o = await get('/api/media/options?kind=video');
    return { tools: videoTools(o), ephemeral: !!o.ephemeral, retention_days: o.retention_days, caps: { ...(o.caps ?? {}), upload_max_mb: o.caps?.profile_upload_max_mb } };
  }
  const r = await get(`/api/media/extras?group=${encodeURIComponent(group)}`);
  return { ...r, tools: (r.tools ?? []).map((t: any) => ({ ...t, via: 'extras' })) };
}

/** Start values for a form: the engine's defaults, nothing invented. */
export function initialValues(schema: Record<string, Spec>): Record<string, any> {
  const v: Record<string, any> = {};
  for (const [k, s] of Object.entries(schema)) if (s.type !== 'file') v[k] = s.default ?? (s.type === 'bool' ? false : s.type === 'enum' ? s.values?.[0] ?? '' : s.type === 'string' ? '' : null);
  return v;
}

/** What is still missing before the tool can start (a plain sentence), or ''. */
export function missing(schema: Record<string, Spec>, values: Record<string, any>, files: Record<string, string | null>): string {
  for (const [k, s] of Object.entries(schema)) {
    if (!s.required) continue;
    if (s.type === 'file' ? !files[k] : values[k] == null || String(values[k]).trim() === '') return `${s.label ?? k} is needed.`;
  }
  return '';
}

/** Send the job. ``files`` maps an input to a Library asset id (uploads go to the Library first, so the same file can be used again). */
export function startTool(t: Tool, values: Record<string, any>, files: Record<string, string | null>, save: boolean): Promise<any> {
  const fd = new FormData();
  fd.append('model_id', t.id);
  fd.append('save', save ? 'true' : 'false');
  const plain: Record<string, any> = {};
  for (const [k, s] of Object.entries(t.inputs_schema)) {
    if (s.type === 'file') { if (files[k]) fd.append(`asset:${k}`, files[k] as string); continue; }
    const v = values[k];
    if (v == null || v === '') continue;
    plain[k] = s.type === 'int' ? Math.round(Number(v)) : s.type === 'float' ? Number(v) : v;
  }
  fd.append('inputs', JSON.stringify(plain));
  return postForm(t.via === 'profile' ? '/api/media/profile-jobs' : '/api/media/extras/jobs', fd);
}

export const assetUrl = (id: string, download = false) => `/api/media/assets/${encodeURIComponent(id)}/content${download ? '?download=true' : ''}`;
export const fmtSeconds = (s?: number | null) => (s == null ? '' : s < 90 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);
