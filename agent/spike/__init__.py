"""S1 — the seam spike (execution §7). The GATE.

This package OWNS `agent/spike/`. It MAY import `clarion.contracts`; it does NOT
modify contracts or any other dir. Real providers (LiveKit, Playwright, CDP,
google-genai) live ONLY here — the kernel and contracts stay provider-free
(foundation §6).
"""
