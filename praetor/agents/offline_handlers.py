"""Deterministic stand-ins for the model calls.

These exist so the fleet runs end to end with no credentials, and so the
scenario tests assert on agent *wiring* rather than on model output. They are
intentionally shallow: they parse the same signal text Gemini receives and apply
the correlation rule a competent human would. They are not trying to be a model.

If one of these ever starts looking clever, that is a sign the corresponding
real prompt is doing too little.
"""

from __future__ import annotations

import re


def _lots(prompt: str) -> list[str]:
    return sorted(set(re.findall(r"lot:[A-Z0-9-]+", prompt)))


def _units(prompt: str) -> list[str]:
    return sorted(set(re.findall(r"unit:[a-z0-9-]+", prompt)))


def diagnose(prompt: str) -> dict:
    lots, units = _lots(prompt), _units(prompt)
    excursion = "coldchain.excursion" in prompt
    rejection = "qc.rejection" in prompt
    instruments = sorted(set(re.findall(r"instr:[a-z0-9-]+", prompt)))

    # Multiple distinct lots failing on one analyser is the shape that
    # implicates the box rather than the reagent.
    if rejection and len(lots) >= 2 and len(instruments) == 1:
        return {
            "root_cause": f"Analyser fault on {instruments[0]}: {len(lots)} distinct reagent "
                          f"lots rejecting on the same instrument.",
            "confidence": 0.82,
            "reasoning": "The common factor across independent lots is the instrument.",
            "implicates_instrument": True,
            "primary_subject": instruments[0],
        }

    if excursion and rejection and lots and units:
        return {
            "root_cause": f"Cold-chain failure at {units[0]} degraded reagent {lots[0]}, "
                          f"biasing control results low.",
            "confidence": 0.88,
            "reasoning": ("The storage unit went out of range before the control drift began, "
                          "and only the lot stored in that unit is affected. The analyser is "
                          "reading a degraded reagent correctly."),
            "implicates_instrument": False,
            "primary_subject": lots[0],
        }

    if excursion and units:
        return {
            "root_cause": f"Cold-chain excursion at {units[0]}; reagent impact not yet confirmed.",
            "confidence": 0.6,
            "reasoning": "Storage is out of range but no QC rejection has followed yet.",
            "implicates_instrument": False,
            "primary_subject": units[0],
        }

    return {
        "root_cause": "Signals do not cohere into a single cause.",
        "confidence": 0.3,
        "reasoning": "Insufficient or unrelated evidence.",
        "implicates_instrument": False,
        "primary_subject": "fleet",
    }


def narrate(prompt: str) -> dict:
    lots, units = _lots(prompt), _units(prompt)
    return {
        "title": (f"Cold-chain failure at {units[0]} degraded {lots[0]}"
                  if units and lots else "Laboratory incident"),
        "summary": ("A storage unit drifted out of range over roughly two days. The reagent "
                    "lot held in it lost potency, biasing control results low until Westgard "
                    "multirules rejected. The analyser was not at fault and stayed in service."),
        "contributing_factors": [
            "Compressor degraded slowly rather than failing outright, so no alarm threshold fired.",
            "Control drift stayed within 2 SD for roughly 40 hours before the first rejection.",
        ],
        "recommendations": [
            "Add continuous cold-chain alerting at the site, not just daily manual checks.",
            "Correlate reagent in-service times against storage history at lot registration.",
        ],
    }


def triage(prompt: str) -> dict:
    """Cheap-tier filter. Interesting only if a reading is out of range."""
    temps = [float(t) for t in re.findall(r"celsius=([0-9.]+)", prompt)]
    return {"interesting": any(t > 8.0 for t in temps), "max_c": max(temps) if temps else 0.0}


def register_all(reasoner) -> None:
    reasoner.register("diagnose", diagnose)
    reasoner.register("narrate", narrate)
    reasoner.register("triage", triage)
