// Shapes of the documents served by /api/studio/jobs/{job}/inspect, /graph and /history (docs/OPENJUTSU_STUDIO.md, 2.2 and 3).
// Every field the panels draw comes from here; a null means "not reported" and is shown as such.

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';
export type NodeStateName = 'done' | 'running' | 'pending' | 'cached' | 'failed';
export type OutputState = 'none' | 'writing' | 'ready';
export type EventType = 'stage' | 'node_start' | 'node_done' | 'node_cached' | 'progress' | 'scene_saved' | 'loop_end' | 'error' | 'output' | 'log';

export interface InspectEvent {
  seq: number;
  t?: number | null;
  type: EventType | string;
  node_id?: string | null;
  title?: string | null;
  scene?: number | null;
  value?: number | null;
  max?: number | null;
  message?: string | null;
  /** output events: 'writing' | 'ready' */
  state?: string | null;
}

export interface Segment {
  index: number;
  state: string; // 'saved' once the file was written and verified
  frames?: number | null;
  duration_s?: number | null;
  width?: number | null;
  height?: number | null;
  bytes?: number | null;
  seconds_to_make?: number | null;
}

export interface NodeStateInfo {
  state: NodeStateName | string;
  class_type?: string | null;
  title?: string | null;
  runs?: number | null;
  total_ms?: number | null;
  last_ms?: number | null;
}

export interface ReferenceInfo {
  position: number;
  node_id?: string | null;
  file?: string | null;
  used: boolean;
  name?: string | null;
  reason?: string | null;
}

export interface InspectDoc {
  job_id?: string;
  backend?: string | null;
  profile?: string | null;
  operation?: string | null;
  status?: JobStatus | string;
  stage?: string | null;
  stage_label?: string | null;
  prompt_id?: string | null;
  started_at?: number | null;
  updated_at?: number | null;
  elapsed_s?: number | null;
  progress?: { fraction: number | null; indeterminate?: boolean; basis?: string | null } | null;
  sampling?: { node_id?: string | null; title?: string | null; step?: number | null; max?: number | null; scene?: number | null } | null;
  scenes?: { total: number | null; completed: number | null; current: number | null; segments?: Segment[] } | null;
  loop?: { iterations_ended?: number | null; meaning?: string | null } | null;
  nodes?: { total: number; done: number; running: string[]; pending: number; cached: number; failed: number } | null;
  node_states?: Record<string, NodeStateInfo> | null;
  conditioning?: {
    video?: { node_id?: string | null; file?: string | null } | null;
    references?: ReferenceInfo[];
    agree?: boolean | null;
  } | null;
  memory?: { ram_available_mb?: number | null; comfyui_rss_mb?: number | null; vram_mb?: number | null; source?: string | null; basis?: string | null } | null;
  output?: { state: OutputState | string; file?: { width?: number | null; height?: number | null; duration_s?: number | null; bytes?: number | null; has_audio?: boolean | null } | null } | null;
  error?: { message: string; node_id?: string | null; node_title?: string | null; class_type?: string | null } | null;
  events?: InspectEvent[];
  next_seq?: number | null;
  // added by the Semechka backend (section 3)
  comfy?: { available: boolean } | null;
  user_status?: string | null;
  // set when the engine no longer has the detailed trace of this job (null while stale: unknown)
  trace_available?: boolean | null;
  /** the engine is not answering: this document is what Semechka itself knows, not the engine's trace */
  stale?: boolean;
  stale_reason?: string | null;
  /** highest event seq in the engine's trace; next_seq is the cursor to hand back */
  last_seq?: number | null;
  has_preview?: boolean | null;
  /** 'comfyui-api' (default) or 'sarness-workflow' (the engine's own image runtime: the button reads VIEW WORKFLOW) */
  graph_kind?: string | null;
}

export type InspectSummary = Omit<InspectDoc, 'events'>;

export type GraphInputValue = string | number | boolean | null | [string, number] | unknown[] | Record<string, unknown>;
export interface GraphNode { class_type: string; title?: string | null; inputs: Record<string, GraphInputValue> }
export interface GraphDoc { prompt_id?: string | null; format?: string; nodes: Record<string, GraphNode>; order?: string[] }

export interface HistoryDoc { status?: unknown; messages?: unknown[]; outputs?: Record<string, unknown>; node_errors?: Record<string, unknown> }
