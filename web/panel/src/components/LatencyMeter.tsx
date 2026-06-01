/**
 * Effect #2 — Live latency meter: "Moss 6ms" vs greyed cold-RAG baseline "cold 340ms".
 * Also shows the speculative-retrieval query firing marker (effect #1).
 * execution §6, §8
 */

"use client";

import type { PanelState } from "@/lib/types";

interface Props {
  state: PanelState;
}

export function LatencyMeter({ state }: Props) {
  const { retrieval_ms, baseline_ms, trace_tail } = state;

  const speculativeFired = trace_tail.some(
    (t) => t.node === "GROUND" && t.event === "enter"
  );

  return (
    <section
      data-component="LatencyMeter"
      style={{
        border: "1px solid #333",
        borderRadius: 8,
        padding: "12px 16px",
        background: "#0d0d0d",
        minWidth: 220,
      }}
    >
      <h2
        style={{ margin: "0 0 10px", fontSize: 11, letterSpacing: 2, color: "#666", textTransform: "uppercase" }}
      >
        Retrieval Latency
      </h2>

      {/* Speculative retrieval marker */}
      <div
        data-marker="speculative-query-fired"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          marginBottom: 10,
          opacity: speculativeFired ? 1 : 0.25,
          transition: "opacity 0.4s",
        }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: speculativeFired ? "#4ade80" : "#333",
            animation: speculativeFired ? "pulse 1s infinite" : "none",
            display: "inline-block",
          }}
        />
        <span style={{ fontSize: 11, color: "#aaa" }}>
          query fired while user still talking
        </span>
      </div>

      {/* Live number */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
        <span
          data-marker="retrieval-ms"
          style={{
            fontSize: 36,
            fontWeight: 700,
            fontVariantNumeric: "tabular-nums",
            color: retrieval_ms !== null ? "#4ade80" : "#444",
          }}
        >
          {retrieval_ms !== null ? retrieval_ms.toFixed(0) : "—"}
        </span>
        <span style={{ fontSize: 14, color: "#888" }}>ms</span>
        <span style={{ fontSize: 12, color: "#555", marginLeft: 4 }}>Moss</span>
      </div>

      {/* Baseline (greyed) */}
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span
          data-marker="baseline-ms"
          style={{
            fontSize: 20,
            fontVariantNumeric: "tabular-nums",
            color: "#444",
            textDecoration: "line-through",
          }}
        >
          {baseline_ms !== null ? baseline_ms.toFixed(0) : "—"}
        </span>
        <span style={{ fontSize: 12, color: "#444" }}>ms cold-RAG baseline</span>
      </div>

      <style>{`
        @keyframes pulse {
          0%,100% { box-shadow: 0 0 0 0 rgba(74,222,128,0.5); }
          50% { box-shadow: 0 0 0 6px rgba(74,222,128,0); }
        }
      `}</style>
    </section>
  );
}
