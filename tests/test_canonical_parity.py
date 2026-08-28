"""The console must hash exactly what Python hashed.

Both sides of the audit chain independently serialise a payload to canonical
JSON and SHA-256 it. If those two serialisations ever diverge by a single byte,
the console reports tampering on an intact chain, the worst possible failure
for the one feature the whole design rests on.

This is not hypothetical. JavaScript has a single number type, so a value
Python wrote as `1.0` parses to something indistinguishable from `1` and
re-serialises as "1". Live payloads carry `confidence: 1.0`, so a naive
implementation fails immediately on real data. The console avoids it by never
parsing numbers into JS numbers at all, keeping each number's original wire
token instead. These tests hold that line.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from praetor.gate.audit import AuditChain, canonical, compute_hash

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "service" / "console.html"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not CONSOLE.exists(),
    reason="needs node and the built console",
)


def _js_canonical(objects: list) -> list[str]:
    """Run the console's own canonicaliser. Not a reimplementation of it."""
    html = CONSOLE.read_text()
    start = html.index("function parsePreservingNumbers")
    end = html.index("async function sha256hex")
    harness = html[start:end] + """
import { readFileSync } from "fs";
const cases = readFileSync(process.argv[2], "utf8").split("\\n").filter(Boolean);
console.log(JSON.stringify(cases.map(c => canonical(parsePreservingNumbers(c)))));
"""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        js, data = Path(d) / "c.mjs", Path(d) / "cases.jsonl"
        js.write_text(harness)
        data.write_text("\n".join(
            json.dumps(o, sort_keys=True, separators=(",", ":"), default=str) for o in objects
        ))
        out = subprocess.run(["node", str(js), str(data)], capture_output=True, text=True, timeout=60)
        if out.returncode:
            pytest.fail(f"node failed: {out.stderr[:400]}")
        return json.loads(out.stdout)


CASES = [
    {"b": 1, "a": 2},
    # The one that breaks a naive implementation.
    {"confidence": 1.0, "action": "notify.scientist"},
    {"celsius": 4.0, "setpoint_c": 4.0, "window_s": 300},
    {"z": -3.05, "confidence": 0.92, "severity": 2},
    {"at": 1787068336.018136, "seq": 0},
    {"nested": {"z": [1, 2, {"k": "v"}], "a": None}, "ok": True},
    {"summary": "unit:fridge-clinic-2 above 8 C for 3 readings, peak 8.9 C"},
    {"text": "cafe—naive", "tab": "a\tb\nc"},
    {"empty_obj": {}, "empty_list": [], "zero": 0, "zero_float": 0.0},
    {"neg": -1, "big": 1000000, "small": 1e-07},
]


def test_javascript_canonicalisation_matches_python_byte_for_byte():
    py = [json.dumps(c, sort_keys=True, separators=(",", ":"), default=str) for c in CASES]
    js = _js_canonical(CASES)
    for case, a, b in zip(CASES, py, js):
        assert a == b, f"diverged on {case}\n  python: {a}\n  js:     {b}"


def test_whole_floats_survive_the_round_trip():
    """`confidence: 1.0` is the specific value that appears in live payloads."""
    payload = {"confidence": 1.0}
    assert canonical(payload).decode() == '{"confidence":1.0}'
    assert _js_canonical([payload])[0] == '{"confidence":1.0}'


def test_a_real_entry_hashes_identically_on_both_sides():
    chain = AuditChain()
    entry = chain.append("decision", {
        "proposal": {"agent_id": "agent.coldchain", "action_type": "notify.scientist",
                     "confidence": 1.0, "params": {"channel": "sms", "message": "x"}},
        "severity": 3,
        "decision": {"verdict": "allow", "reasons": ["within capability"], "decided_at": 1787068336.018136},
    })
    shaped = {
        "seq": entry.seq, "prev_hash": entry.prev_hash, "kind": entry.kind,
        "payload": entry.payload, "recorded_at": entry.recorded_at,
    }
    assert _js_canonical([shaped])[0] == canonical(shaped).decode()
    assert compute_hash(entry.seq, entry.prev_hash, entry.kind,
                        entry.payload, entry.recorded_at) == entry.entry_hash
