/**
 * Effect #3 — Sources + negative-verification panel.
 * Shows grounded_facts with source_node_id. "absent" polarity = negative verification
 * (renders with a strikethrough/badge to make the "no late fee [verified]" claim visible).
 * execution §6, §2.2 VERIFY
 */

"use client";

import type { Fact } from "@/lib/types";

interface Props {
  facts: Fact[];
}

export function SourcesPanel({ facts }: Props) {
  return (
    <section
      data-component="SourcesPanel"
      style={{
        border: "1px solid #333",
        borderRadius: 8,
        padding: "12px 16px",
        background: "#0d0d0d",
        minWidth: 280,
      }}
    >
      <h2
        style={{ margin: "0 0 10px", fontSize: 11, letterSpacing: 2, color: "#666", textTransform: "uppercase" }}
      >
        Grounded Facts
      </h2>

      {facts.length === 0 ? (
        <p style={{ color: "#444", fontSize: 12, margin: 0 }}>Awaiting retrieval…</p>
      ) : (
        <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
          {facts.map((f, i) => (
            <FactRow key={i} fact={f} />
          ))}
        </ul>
      )}
    </section>
  );
}

function FactRow({ fact }: { fact: Fact }) {
  const isAbsent = fact.polarity === "absent";
  const isUngrounded = fact.source_node_id === null;

  return (
    <li
      data-marker={isAbsent ? "negative-verification" : "positive-fact"}
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: "6px 8px",
        borderRadius: 4,
        background: isAbsent ? "#1a0f0f" : "#0f1a0f",
        borderLeft: `3px solid ${isAbsent ? "#ef4444" : "#22c55e"}`,
        opacity: isUngrounded ? 0.45 : 1,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {/* Polarity badge */}
        <span
          style={{
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: 1,
            padding: "1px 4px",
            borderRadius: 3,
            background: isAbsent ? "#7f1d1d" : "#14532d",
            color: isAbsent ? "#fca5a5" : "#86efac",
            textTransform: "uppercase",
          }}
        >
          {isAbsent ? "absent" : "present"}
        </span>

        {/* Verified badge */}
        {fact.verified && (
          <span
            style={{
              fontSize: 9,
              letterSpacing: 1,
              padding: "1px 4px",
              borderRadius: 3,
              background: "#1e3a5f",
              color: "#93c5fd",
              textTransform: "uppercase",
            }}
          >
            verified
          </span>
        )}

        {/* Ungrounded warning */}
        {isUngrounded && (
          <span style={{ fontSize: 9, color: "#f97316", letterSpacing: 1 }}>
            UNGROUNDED
          </span>
        )}
      </div>

      <span
        style={{
          fontSize: 13,
          color: isAbsent ? "#fca5a5" : "#d1fae5",
          textDecoration: isAbsent ? "line-through" : "none",
        }}
      >
        {fact.value}
      </span>

      {fact.source_node_id && (
        <span style={{ fontSize: 10, color: "#555" }}>
          src: {fact.source_node_id}
        </span>
      )}
    </li>
  );
}
