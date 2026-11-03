// Shapes the Work Mode UI expects from /api/work. docs/WORK_MODE.md section 6 fixes the routes; the response bodies
// below are what the interface was built against (see "UI notes" in that document). Optional fields may be absent.

export type SessionStatus = 'idle' | 'running' | 'awaiting_approval' | 'paused' | 'failed' | 'cancelled';
export type ContainerState = 'absent' | 'stopped' | 'running' | 'error';
export type Autonomy = 'contained' | 'ask';
export type NetworkMode = 'none' | 'internet';

export type WorkSession = {
  id: string;
  title: string;
  objective?: string;
  model_id: string;
  model_alias?: string;
  status: SessionStatus;
  container_state: ContainerState;
  network: NetworkMode;
  autonomy: Autonomy;
  skill_ids: string[];
  created_at: string;
  updated_at?: string;
  last_active_at: string;
};

export type WorkModel = { id: string; alias: string; tools?: boolean; vision?: boolean; context?: number; ready?: boolean };

export type WorkOptions = {
  models: WorkModel[];
  skills: WorkSkill[];
  /** Platform ceilings, all optional: upload_max_mb, workspace_max_mb, max_sessions_per_user, max_steps ... */
  limits?: Record<string, number>;
  network_allowed: boolean;
  perms: { network: boolean; skills_manage: boolean };
  image_ready?: boolean;
};

export type SkillFile = { name: string; size?: number; content?: string };
export type WorkSkill = {
  id: string;
  scope: 'org' | 'user';
  name: string;
  title: string;
  description: string;
  category: string;
  enabled: boolean;
  /** Only on GET /skills/{id}. */
  body_md?: string;
  files?: SkillFile[];
  /** True when the signed-in person may change or delete it; when absent the UI falls back to scope and permissions. */
  can_edit?: boolean;
  updated_at?: string;
};
export type SkillInput = {
  scope: 'org' | 'user'; name: string; title: string; description: string; category: string; enabled: boolean;
  body_md: string; files: { name: string; content: string }[];
};

// ---- events (the timeline) -----------------------------------------------------------------------------------------
export type EventKind = 'message' | 'tool_call' | 'tool_result' | 'job' | 'delegate' | 'artifact' | 'approval' | 'state' | 'error';

/** One row of work_events. `id` is monotonic per session and is the polling cursor. `data` is the decrypted payload. */
export type WorkEvent = {
  id: number;
  kind: EventKind;
  run_id?: string | null;
  step_n?: number | null;
  summary?: string;
  created_at: string;
  data?: any;
};
export type EventsPage = { events: WorkEvent[]; last_id?: number };

// data by kind (all fields optional on the wire; the UI shows what it is given and nothing else)
export type MessageData = { role: 'user' | 'assistant' | 'system'; content: string };
export type ToolCallData = { call_id: string; tool: string; command?: string; args?: Record<string, unknown> };
export type ToolResultData = { call_id: string; tool?: string; ok?: boolean; exit_code?: number | null; duration_ms?: number; output?: string; truncated?: boolean; error?: string };
export type JobData = { job_id: string; job_kind?: 'image' | 'video' | string; model?: string; status: JobStatus; phase?: string; progress?: number | null; error?: string; artifact_id?: string };
export type DelegateData = { delegate_id: string; model: string; task: string; depth?: number; status: 'running' | 'done' | 'failed'; result?: string; error?: string };
export type ArtifactData = { artifact_id: string; name: string; path?: string; mime?: string; size?: number };
export type ApprovalData = { step: number | string; tool: string; command?: string; reason?: string; status: 'pending' | 'approved' | 'denied' | 'expired' };
export type StateData = { status: SessionStatus; note?: string };
export type ErrorData = { code?: string; message: string };

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

export type EngineJob = {
  id: string; kind?: string; model?: string; status: JobStatus; phase?: string;
  /** 0 to 1, present only when the engine reports a number. */
  progress?: number | null;
  queue_position?: number | null;
  error?: string; artifact_id?: string; started_at?: string; finished_at?: string;
};

export type WorkState = {
  status: SessionStatus;
  container: { state: ContainerState; note?: string };
  network?: NetworkMode;
  autonomy?: Autonomy;
  model_id?: string;
  active_run?: { id: string; step_n?: number; max_steps?: number } | null;
  jobs?: EngineJob[];
  usage?: { steps?: number; tokens_in?: number; tokens_out?: number; workspace_bytes?: number; workspace_limit_bytes?: number };
};

export type FileEntry = { name: string; path: string; type: 'file' | 'dir'; size?: number; modified?: string; mime?: string };
export type FileListing = { path: string; entries: FileEntry[] };

export type WorkArtifact = { id: string; name: string; path?: string; mime: string; size: number; created_at: string; saved_to_library?: boolean };
