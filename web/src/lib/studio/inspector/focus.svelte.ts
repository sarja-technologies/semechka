// The node both panels agree on: the Process inspector sets it (clicking a node, "Show in graph"), the ComfyUI view reads
// it and centres on it; clicking a card in the graph sets it back so the inspector's list highlights the same node.
class FocusedNode {
  /** graph node id, or null */
  id = $state<string | null>(null);
  /** bumps on every request, so asking for the same node twice still re-centres the graph */
  seq = $state(0);
  set(id: string | null) { this.id = id; this.seq++; }
  clear() { this.id = null; }
}
export const focusedNode = new FocusedNode();
