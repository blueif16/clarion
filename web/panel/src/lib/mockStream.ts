/**
 * Mock PanelState stream — drives all six §6 effects without a live LiveKit room.
 * Active when the `?live=1` query param is absent (default).
 *
 * The scripted sequence simulates a realistic "pay my electric bill" run:
 *   tick 0  — AUTH stage, step 1/2, idle, speculative retrieval fires
 *   tick 1  — LOCATE stage, step 1/3, retrieval lands (6 ms vs 340 ms baseline)
 *   tick 2  — FILL stage, step 1/3, facts grounded (account + amount)
 *   tick 3  — REVIEW stage, proposal emitted, consent awaiting_yes (BARGE-IN active)
 *   tick 4  — REVIEW stage, consent approved
 *   tick 5  — PAY stage (irreversible), proposal, awaiting_yes again
 *   tick 6  — PAY approved, trace growing with node exits
 *   tick 7  — CONFIRM stage, all facts verified, idle
 *   then loops
 */

import type { PanelState, Fact, TraceEvent } from "./types";

const NOW = () => Date.now() / 1000;

function fact(
  value: string,
  source_node_id: string | null,
  polarity: "present" | "absent" = "present",
  verified = false
): Fact {
  return { value, source_node_id, polarity, verified, retrieved_at: NOW() };
}

function trace(node: string, event: "enter" | "exit" | "info", data: Record<string, unknown> = {}): TraceEvent {
  return { node, event, at: NOW(), data };
}

const SEQUENCE: PanelState[] = [
  // tick 0 — AUTH: speculative retrieval fires while user is still talking
  {
    stage: "AUTH",
    step: [1, 2],
    proposal: null,
    consent_state: "idle",
    grounded_facts: [],
    retrieval_ms: null,
    baseline_ms: 340,
    trace_tail: [trace("GROUND", "enter", { note: "speculative: query fired on partial STT" })],
  },
  // tick 1 — LOCATE: retrieval lands, live 6 ms vs 340 ms baseline
  {
    stage: "LOCATE",
    step: [1, 3],
    proposal: null,
    consent_state: "idle",
    grounded_facts: [
      fact("Account #: 00847-221", "ax-node-12"),
    ],
    retrieval_ms: 6,
    baseline_ms: 340,
    trace_tail: [
      trace("GROUND", "enter", { note: "speculative: query fired on partial STT" }),
      trace("GROUND", "exit", { retrieval_ms: 6 }),
    ],
  },
  // tick 2 — FILL: more facts grounded, negative verification on autopay
  {
    stage: "FILL",
    step: [1, 3],
    proposal: null,
    consent_state: "idle",
    grounded_facts: [
      fact("Account #: 00847-221", "ax-node-12", "present", true),
      fact("Amount due: $84.17", "ax-node-27", "present", true),
      fact("Due: Jun 15 2026", "ax-node-31", "present", true),
      fact("No autopay already scheduled", "ax-node-44", "absent", true),
    ],
    retrieval_ms: 6,
    baseline_ms: 340,
    trace_tail: [
      trace("GROUND", "exit", { retrieval_ms: 6 }),
      trace("VERIFY", "enter"),
      trace("VERIFY", "exit", { verified_count: 4, negative_checks: 1 }),
    ],
  },
  // tick 3 — REVIEW: proposal emitted, awaiting consent, barge-in active
  {
    stage: "REVIEW",
    step: [2, 3],
    proposal: "Fill card field with ••••. Amount $84.17 to Pacific Gas & Electric. Say yes to continue.",
    consent_state: "awaiting_yes",
    grounded_facts: [
      fact("Account #: 00847-221", "ax-node-12", "present", true),
      fact("Amount due: $84.17", "ax-node-27", "present", true),
      fact("Due: Jun 15 2026", "ax-node-31", "present", true),
      fact("No autopay already scheduled", "ax-node-44", "absent", true),
    ],
    retrieval_ms: 6,
    baseline_ms: 340,
    trace_tail: [
      trace("VERIFY", "exit", { verified_count: 4 }),
      trace("PROPOSE", "exit", { utterance_chars: 82 }),
      trace("CONSENT", "enter", { irreversible: false }),
    ],
  },
  // tick 4 — REVIEW: consent approved
  {
    stage: "REVIEW",
    step: [2, 3],
    proposal: "Fill card field with ••••. Amount $84.17 to Pacific Gas & Electric. Say yes to continue.",
    consent_state: "approved",
    grounded_facts: [
      fact("Account #: 00847-221", "ax-node-12", "present", true),
      fact("Amount due: $84.17", "ax-node-27", "present", true),
      fact("Due: Jun 15 2026", "ax-node-31", "present", true),
      fact("No autopay already scheduled", "ax-node-44", "absent", true),
    ],
    retrieval_ms: 6,
    baseline_ms: 340,
    trace_tail: [
      trace("CONSENT", "exit", { decision: "approve" }),
      trace("ACT", "enter"),
      trace("ACT", "exit", { action: "fill", index: 7 }),
    ],
  },
  // tick 5 — PAY stage (irreversible), awaiting consent
  {
    stage: "PAY",
    step: [3, 3],
    proposal: "Submit payment of $84.17 to Pacific Gas & Electric — this step cannot be undone. Say yes to confirm.",
    consent_state: "awaiting_yes",
    grounded_facts: [
      fact("Account #: 00847-221", "ax-node-12", "present", true),
      fact("Amount due: $84.17", "ax-node-27", "present", true),
      fact("No surprise fee added", "ax-node-55", "absent", true),
    ],
    retrieval_ms: 6,
    baseline_ms: 340,
    trace_tail: [
      trace("ACT", "exit", { action: "fill" }),
      trace("PROPOSE", "exit", { irreversible: true }),
      trace("CONSENT", "enter", { irreversible: true }),
    ],
  },
  // tick 6 — PAY approved, ACT running
  {
    stage: "PAY",
    step: [3, 3],
    proposal: "Submit payment of $84.17 to Pacific Gas & Electric — this step cannot be undone. Say yes to confirm.",
    consent_state: "approved",
    grounded_facts: [
      fact("Account #: 00847-221", "ax-node-12", "present", true),
      fact("Amount due: $84.17", "ax-node-27", "present", true),
      fact("No surprise fee added", "ax-node-55", "absent", true),
    ],
    retrieval_ms: 6,
    baseline_ms: 340,
    trace_tail: [
      trace("CONSENT", "exit", { decision: "approve" }),
      trace("ACT", "enter", { irreversible: true }),
      trace("ACT", "exit", { action: "submit" }),
      trace("CONFIRM", "enter"),
    ],
  },
  // tick 7 — CONFIRM: success, all verified
  {
    stage: "CONFIRM",
    step: [3, 3],
    proposal: null,
    consent_state: "idle",
    grounded_facts: [
      fact("Confirmation #: 2026-06-0092", "ax-node-61", "present", true),
      fact("Payment posted successfully", "ax-node-62", "present", true),
      fact("No timeout or error state", "ax-node-63", "absent", true),
    ],
    retrieval_ms: 6,
    baseline_ms: 340,
    trace_tail: [
      trace("CONFIRM", "exit", { done: true, silent_fail: false }),
      trace("CONFIRM", "info", { metric: "found → verified → completed in 90s, unaided" }),
    ],
  },
];

export type StateCallback = (state: PanelState) => void;

export function startMockStream(
  onState: StateCallback,
  tickMs = 2800
): () => void {
  let idx = 0;

  // emit immediately
  onState(SEQUENCE[0]);

  const id = setInterval(() => {
    idx = (idx + 1) % SEQUENCE.length;
    // Re-stamp retrieved_at so timestamps look live
    const s = SEQUENCE[idx];
    const now = NOW();
    onState({
      ...s,
      grounded_facts: s.grounded_facts.map((f) => ({ ...f, retrieved_at: now })),
      trace_tail: s.trace_tail.map((t) => ({ ...t, at: now })),
    });
  }, tickMs);

  return () => clearInterval(id);
}
