"""``MinimaxReasoner`` — the PRIMARY ``Reasoner`` over MiniMax-M3, behind the frozen
port. This is the LLM decider for the de-hardcoded task plane (plan_goal +
decide_step), replacing the Gemini/Qwen pair.

MiniMax's text API is **OpenAI-compatible** (``https://api.minimax.io/v1`` →
``/chat/completions``, model id ``MiniMax-M3``, Bearer key, NO GroupId), so this is
a thin specialization of ``OpenAIReasoner``: it REUSES the entire structured-output
strategy (strict ``json_schema`` → ``json_object`` fallback), the shared
``GeminiReasoner`` building blocks (the per-call schema with ``scratch_reasoning``
FIRST + live-index / Fact.id enums, the prompt builders, the verbatim-``say``
grounding) and the SAME ``kernel.reasoner_guard`` post-decode fence (one re-ask
then fail-closed). Only the TRANSPORT CONFIG differs.

Provider config (env, NEVER invented):
  - key      ``MINIMAX_API_KEY``
  - model    ``MINIMAX_LLM_MODEL``   (default ``MiniMax-M3``)
  - base_url ``MINIMAX_BASE_URL``    (default ``https://api.minimax.io/v1``)

The ``openai`` SDK import lives in the ``OpenAIReasoner`` parent only; ``contracts/``
+ ``kernel/`` stay SDK-free. The client is built LAZILY on first use, so the adapter
is importable / constructible without a key.
"""

from __future__ import annotations

import os

from clarion.adapters.openai_reasoner import OpenAIReasoner

__all__ = ["MinimaxReasoner"]

# OpenAI-compatible base for the MiniMax text API (verified live 2026-06-06):
# `https://api.minimax.io/v1` + the standard `/chat/completions` path, Bearer key,
# no GroupId for the OpenAI-compatible endpoint.
_DEFAULT_BASE_URL = "https://api.minimax.io/v1"
_DEFAULT_MODEL = "MiniMax-M3"


class MinimaxReasoner(OpenAIReasoner):
    """A ``Reasoner`` over MiniMax-M3 via the OpenAI-compatible chat endpoint.

    Emits the SAME ``StepProposal`` / ``list[Subgoal]`` shape and runs the SAME
    post-decode guard fence as every other reasoner — only the endpoint config
    (base_url / model / key) changes. ``load_dotenv`` on construct; key from env
    (``MINIMAX_API_KEY``); lazy client (no I/O until first call)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
    ) -> None:
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except Exception:  # noqa: BLE001 - dotenv optional; env may be exported
            pass
        resolved_key = api_key or os.environ.get("MINIMAX_API_KEY")
        resolved_model = model or os.environ.get("MINIMAX_LLM_MODEL", _DEFAULT_MODEL)
        resolved_base = base_url or os.environ.get("MINIMAX_BASE_URL", _DEFAULT_BASE_URL)
        # Reuse the full OpenAI-compatible transport + structured-output + guard
        # path; only the config is MiniMax's.
        super().__init__(
            api_key=resolved_key, model=resolved_model, base_url=resolved_base
        )

    def _ensure_client(self):
        # Same lazy OpenAI client as the parent, but with a MiniMax-accurate
        # missing-key message (the parent names NEBIUS_API_KEY).
        if self._client is None and not self._api_key:
            raise RuntimeError(
                "MINIMAX_API_KEY is not set; cannot construct the MinimaxReasoner "
                "client."
            )
        return super()._ensure_client()
