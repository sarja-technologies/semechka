export const TECH_LABELS: Record<string, string> = {
  model_real_id: 'Model ID', family: 'Family', params: 'Parameters', quantization: 'Quantisation', format: 'Format', context: 'Context window', license: 'Licence',
  runtime: 'Runtime', runtime_version: 'Runtime version', gen_params: 'Generation settings', prompt_tokens: 'Prompt tokens', completion_tokens: 'Output tokens',
  tokens_source: 'Token count source', ttft_s: 'Time to first token (s)', tokens_per_s: 'Tokens per second', tps_source: 'Speed source', elapsed_s: 'Duration (s)',
  queue: 'Queue', node: 'Served by', trace_id: 'Trace ID', trinodal: 'Trinodal AI'
};
export function fmtTech(k: string, v: any): string {
  if (v && typeof v === 'object') return k === 'queue' ? `${v.state}${v.position != null ? ` (position ${v.position})` : ''}. ${v.note ?? ''}`.trim() : JSON.stringify(v);
  if (k === 'params' && typeof v === 'number') return v >= 1e9 ? `${(v / 1e9).toFixed(1)} B` : `${Math.round(v / 1e6)} M`;
  if (k === 'context') return `${Number(v).toLocaleString()} tokens`;
  return String(v);
}

/** The execution path of a Trinodal AI reply, e.g. "conversation → mathematics → conversation". Empty when the person may not see it
 *  (the server sends `nodes` only to holders of tech.node.name) or the reply has none. */
export function trinodalPath(v: any): string {
  return Array.isArray(v?.nodes) && v.nodes.length ? v.nodes.join(' → ') : '';
}
/** The rest of the Trinodal run record, as label/value pairs, only for the parts the server sent. */
export function trinodalRows(v: any): [string, string][] {
  if (!v || typeof v !== 'object') return [];
  const out: [string, string][] = [];
  if (v.route) out.push(['Route', String(v.route)]);
  if (v.language_core && (v.language_core.model || v.language_core.backend)) out.push(['Language core', [v.language_core.model, v.language_core.backend && `(${v.language_core.backend})`].filter(Boolean).join(' ')]);
  if (Array.isArray(v.trained_components) && v.trained_components.length) out.push(['Trained components', v.trained_components.join(', ')]);
  if (typeof v.ms === 'number') out.push(['Time in Trinodal', `${v.ms} ms`]);
  if (typeof v.llm_calls === 'number') out.push(['Language model calls', String(v.llm_calls)]);
  if (typeof v.refused === 'number') out.push(['Refused', String(v.refused)]);
  if (typeof v.errors === 'number') out.push(['Errors', String(v.errors)]);
  if (v.trace_id) out.push(['Trinodal trace ID', String(v.trace_id)]);
  return out;
}
