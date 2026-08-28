"""Three additional Google models, each answering a problem text cannot.

The temptation with a bonus like this is to bolt a model on and call it an
integration. These are chosen the other way round: from who actually has to
act on a laboratory incident in a rural clinic network, and what form that
person can use.

  Image (Gemini 3 Pro Image)
      The person physically standing at the failed fridge at 06:00 is usually
      not the laboratory scientist. It is whoever opened the clinic. A written
      remediation step assumes lab training and fluent clinical English; an
      illustrated card assumes neither. This renders the physical actions
      (move these boxes to that fridge, do not open this one) as a picture.

  Speech (Chirp 3 HD)
      The hero covers four sites and is driving between them. A shift handover
      that must be read is a handover that gets read at the wheel or not at
      all, so the same briefing is available as speech.

      This slot was originally Veo. Veo is not accessible on this project at
      any published version, and rather than claim an integration that does not
      run, the need it was covering, a handover consumable without hands or
      eyes, is met by speech, which serves it better anyway.

  Music (Lyria)
      At the bench, hands are gloved and eyes are down a microscope. Audio is
      the only channel left. Severity gets a distinct short motif rather than
      one undifferentiated beep, so a scientist knows whether to look up now or
      at the end of the run without breaking sterility to check a screen.

Generation is explicit and cached. Veo in particular bills by the second of
output, so nothing here runs automatically on an event: an incident that
generated a video every time a control drifted would be an expensive incident.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Any

from praetor.reasoning import MEDIA_LOCATIONS, MEDIA_MODELS, media_client


@dataclass
class Media:
    kind: str  # image | video | music
    model: str
    mime: str
    data: bytes | None = None
    uri: str | None = None
    cache_key: str = ""

    @property
    def size(self) -> int:
        return len(self.data) if self.data else 0


def _key(kind: str, prompt: str) -> str:
    return f"{kind}-" + hashlib.sha256(prompt.encode()).hexdigest()[:16]


# --------------------------------------------------------------------------
# Prompts. Written from the recipient's position, not the system's.
# --------------------------------------------------------------------------
def remediation_prompt(unit: str, lots: list[str], destination: str,
                       peak_c: float, target_c: float = 4.0) -> str:
    """Real temperatures, not invented ones.

    Left to itself the model will render a plausible-looking number on the
    fridge display. A picture that a clinic worker acts on must not carry a
    figure the system made up, so the measured peak is passed in explicitly.
    """
    return (
        "A clear, friendly instructional illustration for a rural clinic worker who is "
        "not a laboratory scientist. Show two refrigerators side by side in a small clinic "
        "room. The left fridge is marked with a red cross and a thermometer reading too "
        f"warm and reads {peak_c:.0f} degrees Celsius; the right fridge is marked with a "
        f"green tick and reads {target_c:.0f} degrees Celsius. "
        "An arrow shows boxes of reagent being moved from the left fridge to the right. "
        "Simple, uncluttered, high contrast, no text labels, no logos. Flat instructional "
        f"style. Context: {unit} has failed; {len(lots)} reagent boxes must move to "
        f"{destination}."
    )


def alarm_prompt(severity: int) -> str:
    tone = {
        1: "urgent but not panicked: a firm rising three-note motif, low strings and a clear bell",
        2: "attentive: a measured two-note motif, warm marimba, unhurried",
        3: "informational: a single soft chime with a gentle decay",
        4: "ambient: one very quiet low tone, almost unnoticeable",
    }.get(severity, "a single soft chime")
    return (
        f"A short instrumental alert motif for a clinical laboratory, {tone}. "
        "Three seconds. No vocals, no percussion loop, no melody that invites humming. "
        "It must read as an instrument signal, not as music playing in the room."
    )


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def generate_image(prompt: str) -> Media:
    client, model = media_client("image")
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
    )
    for part in response.candidates[0].content.parts:
        inline = getattr(part, "inline_data", None)
        if inline and inline.data:
            return Media("image", model, inline.mime_type or "image/png",
                         data=inline.data, cache_key=_key("image", prompt))
    raise RuntimeError("image model returned no image part")


def generate_music(prompt: str) -> Media:
    """Lyria answers on `:predict`, not `generateContent`.

    The unified generate_content surface does not cover it, so this speaks the
    prediction API directly rather than pretending the SDK abstraction reaches
    further than it does.
    """
    import base64
    import json
    import urllib.request

    import google.auth
    import google.auth.transport.requests

    model = MEDIA_MODELS["music"]
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = MEDIA_LOCATIONS["music"]

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())

    url = (f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}"
           f"/locations/{location}/publishers/google/models/{model}:predict")
    body = json.dumps({"instances": [{"prompt": prompt}], "parameters": {"sample_count": 1}}).encode()
    request = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"})

    with urllib.request.urlopen(request, timeout=300) as response:
        payload = json.loads(response.read())

    prediction = payload["predictions"][0]
    encoded = prediction.get("bytesBase64Encoded") or prediction.get("audioContent")
    if not encoded:
        raise RuntimeError(f"music model returned no audio: {list(prediction)}")
    return Media("music", model, "audio/wav",
                 data=base64.b64decode(encoded), cache_key=_key("music", prompt))


def speak(text: str, voice: str = "en-GB-Chirp3-HD-Achernar") -> Media:
    """Read a handover aloud.

    The scientist covering four clinics is frequently driving between them. A
    briefing that must be read is a briefing that gets read at the wheel or not
    at all, so the same text the console shows is also available as speech.
    """
    import base64
    import json
    import urllib.request

    import google.auth
    import google.auth.transport.requests

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    project = os.environ["GOOGLE_CLOUD_PROJECT"]

    body = json.dumps({
        "input": {"text": text},
        "voice": {"languageCode": voice.rsplit("-", 3)[0] if voice.count("-") > 3 else "en-GB",
                  "name": voice},
        "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.0},
    }).encode()
    request = urllib.request.Request(
        "https://texttospeech.googleapis.com/v1/text:synthesize", data=body,
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json",
                 "x-goog-user-project": project})

    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.loads(response.read())
    if "audioContent" not in payload:
        raise RuntimeError(f"speech synthesis returned no audio: {list(payload)}")
    return Media("speech", f"chirp3-hd:{voice}", "audio/mpeg",
                 data=base64.b64decode(payload["audioContent"]), cache_key=_key("speech", text))


def models_in_use() -> dict[str, str]:
    """What the submission claims, read from the same place the code uses."""
    return dict(MEDIA_MODELS)
