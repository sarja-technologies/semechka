// The shapes the Imajutsu page works with. They follow docs/IMAJUTSU.md and the server's own spec (backend/semechka/imajutsu_svc.py, normalize_spec):
// the page keeps exactly the keys the server stores. Anything the contract leaves open is read defensively in caps.ts / spec.ts, so a server that says
// more or less than expected never breaks the page and never makes it promise more.
import type { Asset } from '$lib/media/assets';

/** ready = recommended and works; available = works but was not benchmarked ("untested"); experimental; unavailable = cannot do it (the reason says why) */
export type Status = 'ready' | 'available' | 'experimental' | 'not_ready' | 'unavailable' | 'missing';

/** What a picture is for. The model takes pictures without a role; the role becomes a sentence in the prompt (shown in the page). */
export type RoleId = 'appearance' | 'identity' | 'composition' | 'lighting' | 'style';

export type RefItem = {
  /** library asset id */
  asset: string;
  /** the person wants this picture used; whether it really is depends on the model's limit (the plan says) */
  active: boolean;
  role: RoleId | string | null;
  /** "keep" note: what must stay as it is from this picture */
  preserve: string;
  label: string;
};

export type Size = { width: number; height: number };
export type Box = { x: number; y: number; w: number; h: number };

export type Crop = { x: number; y: number; w: number; h: number };
/** Photographic adjustment recipe v1. Neutral = absent. crop is in pixels of the picture AFTER turning and flipping. */
export type Recipe = {
  exposure?: number; contrast?: number; saturation?: number; temperature?: number; tint?: number; highlights?: number; shadows?: number;
  sharpness?: number; grain?: number; vignette?: number; rotate?: number; straighten?: number; crop?: Crop; flip?: boolean; output?: Size;
};

export type Spec = {
  version?: number;
  kind: 'image';
  mode: string;
  prompt: string;
  negative: string;
  preset: { id: string | null; scenes: string[]; finish: boolean };
  aspect: { id: string; w: number | null; h: number | null };
  size: { native: Size | null; output: Size | null };
  quality: string;
  seed: number | null;
  count: number;
  source_asset: string | null;
  /** the selection as a picture (white = change); the strokes it was drawn from travel inside it (see mask.ts) */
  mask_asset: string | null;
  selection: { box: Box | null; feather: number; invert: boolean; keep_outside: boolean | null };
  outpaint: { pad: { left: number; top: number; right: number; bottom: number } | null; target: Size | null; anchor: { x: number; y: number }; overlap: number };
  upscale: { refine: boolean; strength: number };
  references: RefItem[];
  /** the model chosen by hand; null = the recommended one */
  model: string | null;
  advanced: { steps?: number; guidance?: number; scheduler?: string; denoise?: number };
  adjustments: Recipe;
};

/** One thing done on the selection canvas. Coordinates are pixels of the SOURCE picture, so the saved mask matches it exactly. */
export type MaskOp =
  | { t: 'brush' | 'erase'; size: number; pts: number[] }
  | { t: 'rect'; x: number; y: number; w: number; h: number; sub?: boolean };
/** The strokes of the selection (the server keeps only the picture; the strokes ride inside it). */
export type MaskDoc = { w: number; h: number; ops: MaskOp[] };

export type Project = {
  id: string; title?: string; spec: Spec; updated_at?: number | string; created_at?: number; source_asset?: string | null; source?: Asset | null; mask?: Asset | null;
  jobs?: { id: string; status: string }[]; [k: string]: any;
};
export type ProjectSummary = { id: string; title?: string; updated_at?: number | string; thumbnail?: string | null; thumb_asset?: string | null; last_job?: { status?: string } | null; [k: string]: any };

export type Derived = { id: string; origin?: string; width?: number; height?: number; created_at?: number; imajutsu?: { kind?: string; label?: string; recipe?: Recipe; [k: string]: any } };
export type Output = { id: string; kind: string; width: number; height: number; seed?: number | null; derived: Derived[] };
export type JobRow = {
  job: string; project_id?: string; status: string; mode?: string; engine_mode?: string; model?: string; model_id?: string; created_at?: number; outputs?: Output[];
  batch?: { id: string; index: number; count: number; seed: number } | null; imajutsu?: any; error?: string | null; progress_text?: string | null; [k: string]: any;
};

export type ModelLimits = { references?: number; min_side?: number; max_side?: number; multiple?: number; native_pixels?: number; negative_prompt?: boolean; steps?: number[]; guidance?: number[]; schedulers?: string[] };
export type ModelEntry = {
  model_id: string | null; engine_id?: string; alias?: string; status: Status; reason?: string; limits?: ModelLimits | null; why?: string | null; evidence?: string[]; slow?: boolean;
  seconds_hint?: string | null; routed?: boolean; needs?: any; [k: string]: any;
};
export type ModeNeeds = { source?: boolean; mask?: boolean; prompt?: boolean; references_min?: number; references_ok?: boolean };
export type ModeEntry = {
  id: string; label: string; engine_mode?: string | null; status: Status; reason?: string;
  recommended?: { model_id: string; alias?: string; why?: string; evidence?: string[] } | null; models: ModelEntry[]; needs?: ModeNeeds;
  scaling?: { method?: string; label?: string; ai?: boolean }; refine?: { available: boolean; models: ModelEntry[]; recommended?: { model_id: string; why?: string } | null };
  [k: string]: any;
};
export type Preset = { id: string; label: string; description?: string; aspect?: string; scene_chips?: { id: string; label: string }[]; model_id?: string | null; finish?: { label?: string; recipe?: Recipe; note?: string } };
export type AspectEntry = { id: string; label: string; w: number | null; h: number | null };
export type Caps = {
  modes: ModeEntry[]; presets: Preset[]; aspects: AspectEntry[]; roles?: { id: string }[]; qualities?: string[]; limits: Record<string, any>;
  adjust?: { ranges?: Record<string, [number, number]>; finish?: Recipe }; [k: string]: any;
};

export type Plan = {
  mode?: string; label?: string; engine_mode?: string | null;
  native?: { width: number; height: number; why?: string; megapixels?: number } | null;
  output?: { width: number; height: number; scaling?: string; note?: string } | null;
  model?: { id: string; alias?: string; why?: string; recommended?: boolean; routed?: boolean; slow?: boolean; seconds_hint?: string | null } | null;
  prompt?: { base?: string; final?: string; negative?: string; negative_sent?: boolean; sentences?: { ref: number | string; position?: number; image?: number; role?: string | null; sentence: string }[]; preset?: any } | null;
  references?: { used: { asset: string; position: number; role?: string | null; label?: string; preserve?: string }[]; not_used: { asset: string; position: number; reason: string }[]; limit: number | null; source_counts: boolean; pictures: number };
  source?: { asset?: string; width: number; height: number } | null;
  mask?: any;
  outpaint?: { pad: { left: number; top: number; right: number; bottom: number }; canvas: Size; overlap?: number } | null;
  estimate?: { seconds?: { estimate: number; basis: string }; memory?: { peak_mb: number; basis: string; note?: string } } | null;
  steps?: number | null;
  warnings?: string[];
  errors?: string[];
  [k: string]: any;
};

export type { Asset };
