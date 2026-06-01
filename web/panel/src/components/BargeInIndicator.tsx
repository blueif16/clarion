/**
 * Effect #4 — Barge-in indicator.
 * LiveKit semantic turn detection keeps barge-in live during TTS.
 * In mock mode we simulate barge-in active whenever consent is awaiting_yes
 * (the most realistic moment — agent is speaking the proposal, user can interrupt).
 * In live mode (future) this fires on RoomEvent.ParticipantAttributesChanged
 * carrying { barge_in: "true" }.
 * execution §5, §6
 */

"use client";

import type { PanelState } from "@/lib/types";

interface Props {
  state: PanelState;
}

export function BargeInIndicator({ state }: Props) {
  // In mock mode: barge-in is active when agent is speaking (awaiting_yes)
  const isActive = state.consent_state === "awaiting_yes";

  return (
    <section
      data-component="BargeInIndicator"
      data-marker={isActive ? "barge-in-active" : "barge-in-idle"}
      style={{
        border: `1px solid ${isActive ? "#7c3aed" : "#333"}`,
        borderRadius: 8,
        padding: "12px 16px",
        background: isActive ? "#0f0a1a" : "#0d0d0d",
        minWidth: 180,
        transition: "all 0.3s",
      }}
    >
      <h2
        style={{ margin: "0 0 10px", fontSize: 11, letterSpacing: 2, color: "#666", textTransform: "uppercase" }}
      >
        Barge-In
      </h2>

      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        {/* Mic icon */}
        <div
          style={{
            width: 36,
            height: 36,
            borderRadius: "50%",
            background: isActive ? "#7c3aed" : "#1a1a1a",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            transition: "background 0.3s",
            animation: isActive ? "bargeRing 1s ease-in-out infinite" : "none",
          }}
        >
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke={isActive ? "#e9d5ff" : "#555"} strokeWidth="2">
            <rect x="9" y="2" width="6" height="11" rx="3" />
            <path d="M5 10a7 7 0 0 0 14 0" />
            <line x1="12" y1="19" x2="12" y2="22" />
            <line x1="9" y1="22" x2="15" y2="22" />
          </svg>
        </div>

        <div>
          <div
            style={{
              fontSize: 13,
              fontWeight: 700,
              color: isActive ? "#c4b5fd" : "#444",
              transition: "color 0.3s",
            }}
          >
            {isActive ? "LIVE" : "Inactive"}
          </div>
          <div style={{ fontSize: 10, color: "#555" }}>
            {isActive ? "interrupt any time" : "agent not speaking"}
          </div>
        </div>
      </div>

      <style>{`
        @keyframes bargeRing {
          0%,100% { box-shadow: 0 0 0 0 rgba(124,58,237,0.5); }
          50% { box-shadow: 0 0 0 8px rgba(124,58,237,0); }
        }
      `}</style>
    </section>
  );
}
