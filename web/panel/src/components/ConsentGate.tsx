/**
 * Effect #5 — Consent gate as visible state.
 * Renders idle / awaiting_yes / approved / rejected with a bold "AWAITING YOUR YES" state.
 * execution §6, §2.3
 */

"use client";

import type { PanelState } from "@/lib/types";

interface Props {
  state: PanelState;
}

const LABELS: Record<PanelState["consent_state"], string> = {
  idle: "Idle",
  awaiting_yes: "AWAITING YOUR YES",
  approved: "Approved",
  rejected: "Rejected",
};

const COLORS: Record<PanelState["consent_state"], { bg: string; text: string; border: string }> = {
  idle: { bg: "#111", text: "#555", border: "#222" },
  awaiting_yes: { bg: "#1a1200", text: "#fbbf24", border: "#d97706" },
  approved: { bg: "#0f1a0f", text: "#4ade80", border: "#16a34a" },
  rejected: { bg: "#1a0909", text: "#f87171", border: "#dc2626" },
};

export function ConsentGate({ state }: Props) {
  const { consent_state, proposal } = state;
  const isAwaiting = consent_state === "awaiting_yes";
  const style = COLORS[consent_state];

  return (
    <section
      data-component="ConsentGate"
      data-marker={`consent-${consent_state}`}
      style={{
        border: `2px solid ${style.border}`,
        borderRadius: 8,
        padding: "14px 16px",
        background: style.bg,
        transition: "all 0.35s",
        minWidth: 280,
      }}
    >
      <h2
        style={{ margin: "0 0 8px", fontSize: 11, letterSpacing: 2, color: "#666", textTransform: "uppercase" }}
      >
        Consent Gate
      </h2>

      {/* State badge */}
      <div
        data-marker="consent-state-label"
        style={{
          fontSize: isAwaiting ? 20 : 14,
          fontWeight: isAwaiting ? 900 : 600,
          letterSpacing: isAwaiting ? 3 : 1,
          color: style.text,
          marginBottom: 10,
          transition: "all 0.3s",
          animation: isAwaiting ? "flash 1.2s ease-in-out infinite" : "none",
          textTransform: "uppercase",
        }}
      >
        {LABELS[consent_state]}
      </div>

      {/* Proposal text */}
      {proposal && (
        <div
          data-marker="proposal-text"
          style={{
            fontSize: 12,
            color: "#bbb",
            borderLeft: `3px solid ${style.border}`,
            paddingLeft: 10,
            lineHeight: 1.5,
          }}
        >
          {proposal}
        </div>
      )}

      <style>{`
        @keyframes flash {
          0%,100% { opacity: 1; }
          50% { opacity: 0.55; }
        }
      `}</style>
    </section>
  );
}
