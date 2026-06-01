/**
 * Effect #1 — Speculative-retrieval visualization.
 * Shows a "query fired while user still talking" pulse when a GROUND enter
 * trace event is present. The waveform-style bars animate to simulate the
 * microphone still being active while the query fires in the background.
 * execution §5, §6, §8
 */

"use client";

import type { PanelState } from "@/lib/types";

interface Props {
  state: PanelState;
}

export function SpeculativeRetrievalViz({ state }: Props) {
  const { trace_tail } = state;

  const isSpeculative = trace_tail.some(
    (t) => t.node === "GROUND" && t.event === "enter"
  );
  const isResolved = trace_tail.some(
    (t) => t.node === "GROUND" && t.event === "exit"
  );

  const status = isResolved
    ? "resolved"
    : isSpeculative
    ? "firing"
    : "idle";

  const label: Record<string, string> = {
    idle: "Waiting for speech…",
    firing: "Query fired — user still talking",
    resolved: "Query resolved",
  };

  const labelColor: Record<string, string> = {
    idle: "#444",
    firing: "#fbbf24",
    resolved: "#4ade80",
  };

  return (
    <section
      data-component="SpeculativeRetrievalViz"
      data-marker={`speculative-${status}`}
      style={{
        border: "1px solid #333",
        borderRadius: 8,
        padding: "12px 16px",
        background: "#0d0d0d",
        minWidth: 200,
      }}
    >
      <h2
        style={{ margin: "0 0 10px", fontSize: 11, letterSpacing: 2, color: "#666", textTransform: "uppercase" }}
      >
        Speculative Retrieval
      </h2>

      {/* Waveform bars — animate when firing */}
      <div
        style={{
          display: "flex",
          alignItems: "flex-end",
          gap: 3,
          height: 32,
          marginBottom: 10,
        }}
      >
        {[5, 12, 20, 28, 22, 14, 6, 16, 24, 18, 9].map((h, i) => (
          <div
            key={i}
            data-marker="waveform-bar"
            style={{
              width: 4,
              height: isSpeculative && !isResolved ? undefined : h,
              borderRadius: 2,
              background: status === "firing" ? "#fbbf24" : status === "resolved" ? "#4ade80" : "#2a2a2a",
              animation:
                status === "firing"
                  ? `waveBar 0.6s ease-in-out ${i * 0.06}s infinite alternate`
                  : "none",
              transition: "background 0.3s",
            }}
          />
        ))}
      </div>

      <div
        style={{
          fontSize: 12,
          color: labelColor[status],
          fontWeight: status === "firing" ? 700 : 400,
          transition: "color 0.3s",
        }}
      >
        {label[status]}
      </div>

      <style>{`
        @keyframes waveBar {
          from { height: 4px; }
          to { height: 28px; }
        }
      `}</style>
    </section>
  );
}
