"""The model seam: two tiers, one interface, and an offline implementation.

Two tiers because the workloads are genuinely different, not to collect a bonus:

  TRIAGE   Thousands of cold-chain readings an hour, almost all of them boring.
           Asking a frontier model "is 4.07 C at setpoint 4.0 C interesting?" is
           the kind of thing that turns a clinic's cloud budget into a problem.
           Gemma is small, cheap, and can answer it. In a rural deployment with
           intermittent connectivity this tier is also the one that can run
           close to the edge.

  REASON   Correlating a fridge excursion, a reagent lot's in-service time and a
           drifting control chart into one root cause. Rare, hard, and worth a
           Gemini call.

`OfflineReasoner` implements the same interface deterministically, so the whole
fleet runs, and is testable, with no network and no credentials. That is not a
mock for the tests' benefit -- it is how the scenario stays reproducible while
the agents around it change.
"""

from __future__ import annotations

import enum
import json
import os
from dataclasses import dataclass
from typing import Any, Protocol


class Tier(enum.Enum):
    TRIAGE = "triage"
    REASON = "reason"


DEFAULT_MODELS = {
    # "Gemini 3.5 or newer" is the contest floor; 3.7 Flash is current stable.
    Tier.REASON: os.environ.get("PRAETOR_REASON_MODEL", "gemini-3.7-flash"),
    Tier.TRIAGE: os.environ.get("PRAETOR_TRIAGE_MODEL", "gemma-3-27b-it"),
}


@dataclass
class Completion:
    data: dict[str, Any]
    model: str
    tier: Tier
    # Populated by the live reasoner; the console reports spend per incident.
    input_tokens: int = 0
    output_tokens: int = 0


class Reasoner(Protocol):
    def complete(
        self, task: str, *, tier: Tier, system: str, prompt: str, schema: dict
    ) -> Completion: ...


# --------------------------------------------------------------------------
# Live
# --------------------------------------------------------------------------
class GeminiReasoner:
    """Backed by the Google GenAI SDK, against either Gemini API or Vertex AI.

    Structured output is required, not requested: every call carries a JSON
    schema and the caller receives parsed data or an exception. An agent that
    has to regex a model's prose is an agent with a second failure mode.
    """

    def __init__(self, client: Any = None, models: dict[Tier, str] | None = None) -> None:
        if client is None:
            from google import genai  # imported lazily so offline runs need no SDK

            # Honours GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_CLOUD_PROJECT when set,
            # otherwise falls back to GEMINI_API_KEY.
            client = genai.Client()
        self._client = client
        self._models = models or dict(DEFAULT_MODELS)

    def complete(self, task: str, *, tier: Tier, system: str, prompt: str, schema: dict) -> Completion:
        model = self._models[tier]
        from google.genai import types

        response = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.2 if tier is Tier.REASON else 0.0,
            ),
        )
        usage = getattr(response, "usage_metadata", None)
        return Completion(
            data=json.loads(response.text),
            model=model,
            tier=tier,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        )


# --------------------------------------------------------------------------
# Offline
# --------------------------------------------------------------------------
class OfflineReasoner:
    """Deterministic stand-in. Same interface, no network, no credentials.

    Handlers are registered per task. An unregistered task raises rather than
    returning something plausible, because a silent default here would let a
    broken agent look like a working one.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Any] = {}
        self.calls: list[tuple[str, Tier]] = []

    def register(self, task: str, handler) -> None:
        self._handlers[task] = handler

    def complete(self, task: str, *, tier: Tier, system: str, prompt: str, schema: dict) -> Completion:
        self.calls.append((task, tier))
        handler = self._handlers.get(task)
        if handler is None:
            raise KeyError(f"OfflineReasoner has no handler for task {task!r}")
        return Completion(data=handler(prompt), model=f"offline:{tier.value}", tier=tier)


def select_reasoner() -> Reasoner:
    """Live when credentials are present, offline otherwise.

    Deliberately explicit: `PRAETOR_OFFLINE=1` forces the stub even with
    credentials available, which is what CI and the scenario tests use.
    """
    if os.environ.get("PRAETOR_OFFLINE") == "1":
        return build_offline_reasoner()
    if os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return GeminiReasoner()
    return build_offline_reasoner()


def build_offline_reasoner() -> OfflineReasoner:
    """Wire the default deterministic handlers. Defined in `praetor.agents`
    so the reasoner module stays free of domain knowledge."""
    from praetor.agents.offline_handlers import register_all

    r = OfflineReasoner()
    register_all(r)
    return r
