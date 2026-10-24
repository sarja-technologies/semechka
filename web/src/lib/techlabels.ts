export const TECH_LABELS: Record<string, string> = {
  model_real_id: 'Model ID', family: 'Family', params: 'Parameters', quantization: 'Quantisation', format: 'Format', context: 'Context window', license: 'Licence',
  runtime: 'Runtime', runtime_version: 'Runtime version', gen_params: 'Generation settings', prompt_tokens: 'Prompt tokens', completion_tokens: 'Output tokens',
  tokens_source: 'Token count source', ttft_s: 'Time to first token (s)', tokens_per_s: 'Tokens per second', tps_source: 'Speed source', elapsed_s: 'Duration (s)',
  queue: 'Queue', node: 'Served by', trace_id: 'Trace ID'
};
export function fmtTech(k: string, v: any): string {
  if (v && typeof v === 'object') return k === 'queue' ? `${v.state}${v.position != null ? ` (position ${v.position})` : ''}. ${v.note ?? ''}`.trim() : JSON.stringify(v);
  if (k === 'params' && typeof v === 'number') return v >= 1e9 ? `${(v / 1e9).toFixed(1)} B` : `${Math.round(v / 1e6)} M`;
  if (k === 'context') return `${Number(v).toLocaleString()} tokens`;
  return String(v);
}
