// Shapes of /api/work/goose (docs/WORK_MODE_GOOSE.md on branch consol/work, contract v1). Optional fields may be absent; the screen shows what it is given.
export type Decision = 'allow_once' | 'allow_always' | 'deny' | 'deny_always';
export type SessionState = 'idle' | 'starting' | 'loading_model' | 'running' | 'awaiting_approval' | 'cancelling' | 'error';

export type GooseModel = { id: string; label: string; context?: number; state?: 'loaded' | 'loading' | 'unloaded'; memory_mib?: number; default?: boolean };
export type GooseStatus = {
  enabled: boolean; ready: boolean; problems: string[]; goose_version?: string;
  models: GooseModel[];
  autonomy: { id: string; label: string; default?: boolean }[];
  extensions?: { id: string; label: string; builtin?: boolean; required?: boolean }[];
  mcp_servers?: { id: string; label: string; description?: string }[];
  skills?: { id: string; name: string; title?: string; description?: string; scope?: string }[];
  limits?: Record<string, number>;
};

export type Approval = {
  id: string; tool_call_id?: string; tool: string; extension?: string; title: string; input?: Record<string, unknown>;
  options: Decision[]; created_at?: number; expires_at?: number;
};

export type GooseSession = {
  id: string; conversation_id: string; state: SessionState; model: string; model_state?: string; autonomy: string; mcp?: string[]; skills?: string[];
  goose_session_id?: string | null; container?: 'absent' | 'stopped' | 'running' | 'error';
  turn: { id: string; started: number } | null; pending_approvals: Approval[]; last_seq: number; title?: string;
  created_at?: number; updated_at?: number; last_active_at?: number;
};

export type FileChange = { path: string; status: 'added' | 'modified' | 'deleted'; additions?: number; deletions?: number; binary?: boolean };

/** One event as it comes over the wire. `type` decides which of the other fields exist. */
export type WorkEvent = { seq: number; type: string; ts?: number; turn_id?: string; [k: string]: any };

export type ToolStatus = 'running' | 'completed' | 'failed' | 'cancelled';
export type Item =
  | { key: string; k: 'user'; turn?: string; text: string }
  | { key: string; k: 'assistant'; id: string; turn?: string; text: string; reasoning: string }
  | { key: string; k: 'tool'; id: string; turn?: string; tool: string; extension?: string; title: string; kind?: string; input?: Record<string, unknown>; status: ToolStatus; output: string; exit_code?: number | null; diff?: { path: string; unified: string } | null }
  | { key: string; k: 'approval'; approval: Approval; decision?: string; by?: string }
  | { key: string; k: 'files'; turn: string; files: FileChange[]; truncated?: boolean }
  | { key: string; k: 'model'; model: string; phase: 'loading' | 'ready' | 'failed'; message?: string }
  | { key: string; k: 'notice' | 'error'; code?: string; message: string; retryable?: boolean }
  | { key: string; k: 'end'; turn?: string; reason: string };
