// The iPhone Photo option of the chat picture operation. What it is (label, 9:16, the quality tiers and the model each one runs on) comes from
// GET /api/imajutsu/capabilities (`presets[0]`), which reads the routing table; nothing here names a model. The prompt wording and the size are decided
// by the server with the same code Imajutsu uses; this file only offers the sizes a model can be asked for.

export type PresetTier = { id: string; label: string; model_id: string | null; alias: string; status: string; reason?: string; why?: string; seconds_hint?: string | null; default?: boolean };
export type PresetScene = { id: string; label: string };
export type PresetInfo = { id: string; label: string; description: string; aspect: string; tiers: PresetTier[]; default_tier?: string; scene_chips: PresetScene[]; model_id: string | null };

export type PortraitSize = { w: number; h: number; label: string; note: string };

const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a);

/** 9:16 sizes a model can be asked for: both sides must be multiples of its `multiple` and inside its side range.
 *  The first is the standard 720 × 1280 (when valid); the second is the largest one up to about 2 megapixels, offered only when it really is larger. */
export function portraitSizes(limits: { min_side?: number; max_side?: number; multiple?: number } | null | undefined): PortraitSize[] {
  const mult = Math.max(1, Number(limits?.multiple) || 16), lo = Number(limits?.min_side) || 256, hi = Number(limits?.max_side) || 1024;
  // 9a and 16a must both be multiples of `mult`: a is a multiple of mult / gcd(mult, 9) and of mult / gcd(mult, 16)
  const step = lcm(mult / gcd(mult, 9), mult / gcd(mult, 16));
  const fits = (a: number) => a > 0 && 9 * a >= lo && 16 * a >= lo && 9 * a <= hi && 16 * a <= hi;
  const out: PortraitSize[] = [];
  const standard = 80; // 720 × 1280
  if (standard % step === 0 && fits(standard)) out.push({ w: 720, h: 1280, label: '720 × 1280', note: 'standard' });
  let best = 0;
  for (let a = step; 16 * a <= hi; a += step) if (fits(a) && 9 * a * 16 * a <= 2_100_000) best = a;
  if (best && !out.length) out.push({ w: 9 * best, h: 16 * best, label: `${9 * best} × ${16 * best}`, note: 'the largest this model allows' });
  else if (best && 9 * best * 16 * best > 720 * 1280 * 1.2) out.push({ w: 9 * best, h: 16 * best, label: `${9 * best} × ${16 * best}`, note: 'larger, slower' });
  return out;
}

function lcm(a: number, b: number) { return (a / gcd(a, b)) * b; }

/** The tier a model id belongs to, if any. */
export const tierOf = (info: PresetInfo | null | undefined, modelId: string | undefined) => info?.tiers.find((t) => t.model_id && t.model_id === modelId) ?? null;
