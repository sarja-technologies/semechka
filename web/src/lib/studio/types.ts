// The shapes the Studio page works with. They follow docs/OPENJUTSU_STUDIO.md section 3 (the Semechka backend contract). Anything the contract
// leaves open is read defensively in caps.ts / spec.ts, so a server that says more or less than expected never breaks the page.
import type { Asset } from '$lib/media/assets';

export type Rect = { x: number; y: number; w: number; h: number };
export type Treatment = 'crop' | 'pad' | 'stretch';
export type SubjectKind = 'person' | 'vehicle' | 'animal' | 'object' | 'other';
export type Status = 'ready' | 'experimental' | 'not_ready' | 'unavailable';

export type RefItem = {
  /** library asset id of the picture */
  asset: string;
  /** the person wants this picture used; whether it really is depends on the operation's limit (see refs.ts) */
  active: boolean;
  /** which target in the video this picture stands for (1-based), or null = by order */
  subject: number | null;
  label: string;
};

export type Spec = {
  source_asset: string | null;
  /** seconds in the source file; null = the whole video */
  trim: { start: number; end: number } | null;
  /** the part of the picture to keep, in pixels of the picture as it is DISPLAYED (after rotation); null = all of it */
  crop: Rect | null;
  frame: { aspect: string; ratio: number | null; lock: boolean; treatment: Treatment };
  resolution: { preset: string; width: number | null; height: number | null; lock_aspect: boolean };
  /** keep the sound or remove it from the prepared copy */
  audio: 'keep' | 'mute';
  references: RefItem[];
  mode: string | null;
  subject: { text: string; kind: SubjectKind; box: [number, number, number, number] | null; points: { x: number; y: number; positive: boolean }[] };
  model: string | null;
  /** engine settings by name from the model's inputs_schema (seed, clip_seconds, hat_fix ...) */
  settings: Record<string, any>;
  accept_experimental: boolean;
};

export type Project = {
  id: string;
  title?: string;
  spec: Partial<Spec>;
  updated_at?: number | string;
  created_at?: number;
  source_asset?: string | null;
  prepared_asset?: string | Asset | null;
  jobs?: JobRow[];
  [k: string]: any;
};

export type ProjectSummary = { id: string; title?: string; updated_at?: number | string; thumbnail?: string | null; thumb_asset?: string | null; last_job?: { status?: string } | null; [k: string]: any };

export type JobRow = { job: string; operation?: string; project_id?: string; status: string; mode?: string; model?: string; created_at?: number; outputs?: any[]; [k: string]: any };

export type Policy = { min: number; active_max: number };

export type ModelEntry = {
  model_id: string; alias?: string; family?: string; status: Status; reason?: string;
  needs?: any; reference_policy?: any; limits?: any; subject_selection?: any; operations?: string[]; [k: string]: any;
};
export type ModeEntry = {
  id: string; label: string; operation: string; fixed_inputs?: Record<string, any>; subject_kinds?: string[]; status: Status; reason?: string;
  models: ModelEntry[]; needs?: any; [k: string]: any;
};
export type CapModel = { model_id: string; alias?: string; family?: string; kind: 'editing_profile' | 'generator' | string; modes: string[]; unsupported: { mode: string; reason: string }[]; [k: string]: any };
export type Caps = { modes: ModeEntry[]; models: CapModel[]; limits: Record<string, any>; [k: string]: any };

export type Plan = {
  input?: { width: number; height: number; duration?: number; rotation?: number; fps?: number; audio?: boolean };
  trim?: { start: number; end: number; seconds: number; mode?: string; why?: string };
  frame?: { aspect?: string; treatment?: string; crop?: Rect };
  prepared?: { width: number; height: number; scale?: boolean };
  processing?: { short_edge?: number; width: number; height: number; preset?: string; exact?: boolean; why?: string; memory?: { peak_mb: number | null; basis: string; note?: string } };
  output?: { requested?: { preset?: string; width: number; height: number }; native?: { width: number; height: number }; scaling?: 'upscale' | 'downscale' | 'none'; note?: string };
  seconds?: { estimate?: number | null; basis?: string };
  warnings?: string[];
  errors?: string[];
};

export type Inspect = {
  job_id?: string; status?: string; stage?: string; stage_label?: string; elapsed_s?: number;
  progress?: { fraction: number | null; indeterminate?: boolean; basis?: string };
  scenes?: { total?: number; completed?: number; current?: number; segments?: { index: number; state: string }[] };
  error?: { message: string; node_id?: string; node_title?: string; class_type?: string } | null;
  output?: { state?: string };
  comfy?: { available: boolean };
  user_status?: any;
  next_seq?: number;
  [k: string]: any;
};
