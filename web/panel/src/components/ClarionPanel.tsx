/**
 * ClarionPanel — the root client component.
 *
 * MODE SELECTION (execution §6, U1 contract):
 *   Default (?live=1 absent): MOCK MODE — scripted PanelState sequence drives all six panels.
 *   ?live=1: LIVE MODE — subscribes to LiveKit participant attributes
 *     (RoomEvent.ParticipantAttributesChanged) on the agent participant's
 *     "panel_state" key. Requires NEXT_PUBLIC_LK_URL + NEXT_PUBLIC_LK_TOKEN
 *     env vars (or the token API route).
 *
 * Context7 refs:
 *   @livekit/components-react /livekit/components-js — LiveKitRoom, useRoomContext,
 *     useParticipantAttribute, participantAttributesObserver
 *   livekit-client /livekit/client-sdk-js — Room, RoomEvent.ParticipantAttributesChanged,
 *     participant.attributes API
 *   Next.js /vercel/next.js/v16.2.2 — 'use client', App Router
 */

"use client";

import { useEffect, useState, useRef } from "react";
import type { PanelState } from "@/lib/types";
import { startMockStream } from "@/lib/mockStream";
import { LatencyMeter } from "./LatencyMeter";
import { SourcesPanel } from "./SourcesPanel";
import { ConsentGate } from "./ConsentGate";
import { GlassBoxTrace } from "./GlassBoxTrace";
import { SpeculativeRetrievalViz } from "./SpeculativeRetrievalViz";
import { BargeInIndicator } from "./BargeInIndicator";

// ── Live mode: LiveKit imports (only used when ?live=1) ───────────────────────
// Dynamically imported so the mock path has zero runtime cost from LiveKit.
// The actual import happens inside useLiveState().

interface ClarionPanelProps {
  liveMode: boolean;
  lkUrl?: string;
  lkToken?: string;
}

// PANEL_STATE_ATTR — the participant attribute key published by the Python task plane.
const PANEL_STATE_ATTR = "panel_state";

// ── Hook: mock state ──────────────────────────────────────────────────────────
function useMockState(): PanelState | null {
  const [state, setState] = useState<PanelState | null>(null);

  useEffect(() => {
    const stop = startMockStream((s) => setState(s), 2800);
    return stop;
  }, []);

  return state;
}

// ── Hook: live LiveKit state ──────────────────────────────────────────────────
function useLiveState(lkUrl: string, lkToken: string): PanelState | null {
  const [state, setState] = useState<PanelState | null>(null);
  const roomRef = useRef<import("livekit-client").Room | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function connect() {
      const { Room, RoomEvent } = await import("livekit-client");
      const room = new Room({ adaptiveStream: false });
      roomRef.current = room;

      // Listen for attribute changes on any participant (the agent publishes on its identity)
      room.on(
        RoomEvent.ParticipantAttributesChanged,
        (
          changedAttrs: Record<string, string>,
          participant: import("livekit-client").RemoteParticipant
        ) => {
          if (cancelled) return;
          // Only act if the agent participant updated panel_state
          if (PANEL_STATE_ATTR in changedAttrs) {
            try {
              const raw = participant.attributes[PANEL_STATE_ATTR];
              if (raw) setState(JSON.parse(raw) as PanelState);
            } catch (e) {
              console.error("[ClarionPanel] bad PanelState JSON", e);
            }
          }
        }
      );

      // Also pick up initial attributes after connect
      room.on(RoomEvent.Connected, () => {
        if (cancelled) return;
        for (const p of room.remoteParticipants.values()) {
          const raw = p.attributes?.[PANEL_STATE_ATTR];
          if (raw) {
            try { setState(JSON.parse(raw) as PanelState); } catch (_) { /* ignore */ }
            break;
          }
        }
      });

      await room.connect(lkUrl, lkToken, { autoSubscribe: false });
    }

    connect().catch((e) => console.error("[ClarionPanel] connect failed", e));

    return () => {
      cancelled = true;
      roomRef.current?.disconnect();
    };
  }, [lkUrl, lkToken]);

  return state;
}

// ── Panel shell ───────────────────────────────────────────────────────────────
function PanelShell({ state }: { state: PanelState }) {
  return (
    <main
      style={{
        minHeight: "100vh",
        background: "#050505",
        color: "#e5e7eb",
        fontFamily: "'Inter', system-ui, sans-serif",
        padding: 24,
      }}
    >
      {/* Stage / step header */}
      <header
        data-marker="stage-header"
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          marginBottom: 24,
          paddingBottom: 16,
          borderBottom: "1px solid #1f2937",
        }}
      >
        <div>
          <span style={{ fontSize: 11, color: "#6b7280", letterSpacing: 2, textTransform: "uppercase" }}>Stage</span>
          <div style={{ fontSize: 22, fontWeight: 700, letterSpacing: 1 }}>{state.stage}</div>
        </div>
        <div style={{ width: 1, height: 32, background: "#1f2937" }} />
        <div>
          <span style={{ fontSize: 11, color: "#6b7280", letterSpacing: 2, textTransform: "uppercase" }}>Step</span>
          <div style={{ fontSize: 22, fontVariantNumeric: "tabular-nums" }}>
            {state.step[0]}<span style={{ color: "#374151" }}>/{state.step[1]}</span>
          </div>
        </div>
        <div style={{ marginLeft: "auto", fontSize: 10, color: "#374151", letterSpacing: 1 }}>
          CLARION · LEGIBILITY PANEL
        </div>
      </header>

      {/* Top row: effects 1, 2, 4 */}
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          marginBottom: 16,
        }}
      >
        <SpeculativeRetrievalViz state={state} />
        <LatencyMeter state={state} />
        <BargeInIndicator state={state} />
      </div>

      {/* Middle row: effect 5 (consent) */}
      <div style={{ marginBottom: 16 }}>
        <ConsentGate state={state} />
      </div>

      {/* Bottom row: effects 3 + 6 */}
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
        <SourcesPanel facts={state.grounded_facts} />
        <GlassBoxTrace traceTail={state.trace_tail} />
      </div>
    </main>
  );
}

// ── Root export ───────────────────────────────────────────────────────────────
export function ClarionPanel({ liveMode, lkUrl = "", lkToken = "" }: ClarionPanelProps) {
  const mockState = useMockState();
  const liveState = useLiveState(liveMode ? lkUrl : "", liveMode ? lkToken : "");

  const state = liveMode ? liveState : mockState;

  if (!state) {
    return (
      <main
        style={{ minHeight: "100vh", background: "#050505", display: "flex", alignItems: "center", justifyContent: "center" }}
      >
        <div style={{ color: "#374151", fontSize: 14, fontFamily: "monospace" }}>
          {liveMode ? "Connecting to LiveKit room…" : "Initialising mock stream…"}
        </div>
      </main>
    );
  }

  return <PanelShell state={state} />;
}
