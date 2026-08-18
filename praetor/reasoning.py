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
    # Flash-Lite, not Gemma. Gemma is not served on Vertex's publisher endpoint
    # at all -- it requires a self-deployed Model Garden endpoint on a GPU that
    # cannot scale to zero, which is the wrong shape for a clinic's budget --
    # and it 404s on the Gemini API path for this account too. Flash-Lite is
    # roughly an order of magnitude cheaper than Flash per token and answers
    # "is this reading interesting?" perfectly well, which is all this tier does.
    Tier.TRIAGE: os.environ.get("PRAETOR_TRIAGE_MODEL", "gemini-3.5-flash-lite"),
}

# Vertex splits its catalogue across endpoints, and not the way you would guess:
# the Gemini text models are served from `global` and 404 in every region, while
# the media models are served regionally and 404 on `global`. One client cannot
# reach both, so the media clients are built separately.
TEXT_LOCATION = os.environ.get("PRAETOR_TEXT_LOCATION", "global")
MEDIA_LOCATION = os.environ.get("PRAETOR_MEDIA_LOCATION", "us-central1")

# Additional Google models, each earning its place rather than bolted on.
MEDIA_MODELS = {
    "video": os.environ.get("PRAETOR_VIDEO_MODEL", "veo-3.1-generate-preview"),
    "music": os.environ.get("PRAETOR_MUSIC_MODEL", "lyria-002"),
    "image": os.environ.get("PRAETOR_IMAGE_MODEL", "gemini-3-pro-image"),
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
            # otherwise falls back to GEMINI_API_KEY. Text models live on the
            # `global` endpoint; see TEXT_LOCATION above.
            os.environ.setdefault("GOOGLE_CLOUD_LOCATION", TEXT_LOCATION)
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


def media_client(kind: str):
    """A client for Veo / Lyria / image generation, on the regional endpoint.

    Separate from the text client because the two live on different Vertex
    endpoints and a single client cannot address both.
    """
    from google import genai

    return genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=MEDIA_LOCATION,
    ), MEDIA_MODELS[kind]


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
