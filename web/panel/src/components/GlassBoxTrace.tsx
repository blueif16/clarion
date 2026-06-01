/**
 * Effect #6 — Glass-box trace + one-line metric.
 * Renders trace_tail as a growing log of node entry/exit events.
 * execution §6, §2.1 trace[]
 */

"use client";

import type { TraceEvent } from "@/lib/types";

interface Props {
  traceTail: TraceEvent[];
}

const EVENT_COLORS: Record<TraceEvent["event"], string> = {
  enter: "#60a5fa",
  exit: "#34d399",
  info: "#a78bfa",
};

const NODE_SHORT: Record<string, string> = {
  GROUND: "GRD",
  VERIFY: "VRF",
  PROPOSE: "PRP",
  CONSENT: "CSN",
  ACT: "ACT",
  CONFIRM: "CNF",
};

function fmtTime(unixSec: number): string {
  const d = new Date(unixSec * 1000);
  return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function GlassBoxTrace({ traceTail }: Props) {
  return (
    <section
      data-component="GlassBoxTrace"
      style={{
        border: "1px solid #333",
        borderRadius: 8,
        padding: "12px 16px",
        background: "#080808",
        minWidth: 300,
        fontFamily: "monospace",
      }}
    >
      <h2
        style={{ margin: "0 0 10px", fontSize: 11, letterSpacing: 2, color: "#666", textTransform: "uppercase", fontFamily: "sans-serif" }}
      >
        Glass-Box Trace
      </h2>

      {traceTail.length === 0 ? (
        <div style={{ color: "#333", fontSize: 12 }}>No events yet…</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          {traceTail.map((evt, i) => (
            <div
              key={i}
              data-marker="trace-event"
              style={{
                display: "grid",
                gridTemplateColumns: "56px 32px 38px 1fr",
                gap: 8,
                fontSize: 11,
                lineHeight: 1.6,
              }}
            >
              <span style={{ color: "#444" }}>{fmtTime(evt.at)}</span>
              <span style={{ color: EVENT_COLORS[evt.event], fontWeight: 700 }}>
                {evt.event.toUpperCase()}
              </span>
              <span style={{ color: "#9ca3af" }}>
                {NODE_SHORT[evt.node] ?? evt.node.slice(0, 6)}
              </span>
              <span style={{ color: "#6b7280", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {Object.entries(evt.data)
                  .map(([k, v]) => `${k}=${JSON.stringify(v)}`)
                  .join(" ")}
              </span>
            </div>
          ))}
        </div>
      )}

      {/* One-line metric from CONFIRM info events */}
      {traceTail
        .filter((t) => t.node === "CONFIRM" && t.event === "info" && t.data.metric)
        .map((t, i) => (
          <div
            key={i}
            data-marker="one-line-metric"
            style={{
              marginTop: 10,
              paddingTop: 8,
              borderTop: "1px solid #1f2937",
              fontSize: 12,
              color: "#a78bfa",
              fontFamily: "sans-serif",
            }}
          >
            {String(t.data.metric)}
          </div>
        ))}
    </section>
  );
}
