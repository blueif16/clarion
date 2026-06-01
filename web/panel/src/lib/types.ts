/**
 * TypeScript mirror of agent/clarion/contracts/events.py PanelState
 * and agent/clarion/contracts/state.py Fact / TraceEvent.
 *
 * DO NOT MODIFY the Python originals — this file is the TS mirror only.
 * Keep in sync with execution §18.4.
 */

// ── state.py mirrors ─────────────────────────────────────────────────────────

/** Mirror of state.py Fact */
export interface Fact {
  value: string;
  /** AXTree node or retriever doc ref the fact was read from. null ⇒ ungrounded ⇒ MUST NOT be spoken. */
  source_node_id: string | null;
  /** "absent" carries negative verification — "no late fee [verified: not present]" */
  polarity: "present" | "absent";
  verified: boolean;
  /** Unix epoch seconds */
  retrieved_at: number;
}

/** Mirror of state.py TraceEvent */
export interface TraceEvent {
  node: string;
  event: "enter" | "exit" | "info";
  /** Unix epoch seconds */
  at: number;
  /** Optional structured payload e.g. { retrieval_ms: 6 } */
  data: Record<string, unknown>;
}

// ── events.py mirror ─────────────────────────────────────────────────────────

/**
 * Mirror of events.py PanelState (execution §18.4).
 * Published as a LiveKit participant attribute (JSON) to drive the six §6 effects.
 * The blind user never needs this; the panel never speaks — two audiences, one state.
 */
export interface PanelState {
  stage: string;
  /** [k, n] within current stage — "k of n steps" */
  step: [number, number];
  proposal: string | null;
  consent_state: "idle" | "awaiting_yes" | "approved" | "rejected";
  grounded_facts: Fact[];
  /** Live Moss number — null until first retrieval */
  retrieval_ms: number | null;
  /** Greyed cold-RAG baseline for contrast */
  baseline_ms: number | null;
  trace_tail: TraceEvent[];
}
