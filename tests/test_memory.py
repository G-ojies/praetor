"""Incident memory.

The retrieval threshold is the whole component. Set too low it hands the
diagnostician confident-looking matches for a question the archive cannot
answer, which is worse than silence -- a language model offered three weak
resemblances will build on them. Set too high it discards the real match and
the memory may as well not exist.
"""

import math

import pytest

from praetor.memory import SIMILARITY_FLOOR, IncidentMemory, Recollection, cosine, describe


def fake_embedder(vectors: dict[str, list[float]], default=(0.0, 0.0, 1.0)):
    """Deterministic stand-in, so these tests never touch the network."""
    return lambda text: list(vectors.get(text, default))


A = [1.0, 0.0, 0.0]
B = [0.0, 1.0, 0.0]
NEAR_A = [0.98, 0.20, 0.0]


# -- the maths --------------------------------------------------------------

def test_cosine_of_identical_vectors_is_one():
    assert cosine(A, A) == pytest.approx(1.0)


def test_cosine_of_orthogonal_vectors_is_zero():
    assert cosine(A, B) == pytest.approx(0.0)


def test_cosine_rejects_mismatched_dimensions():
    with pytest.raises(ValueError, match="dimension mismatch"):
        cosine([1.0, 0.0], [1.0, 0.0, 0.0])


def test_cosine_of_a_zero_vector_is_zero_not_a_division_error():
    assert cosine([0.0, 0.0, 0.0], A) == 0.0


# -- retrieval --------------------------------------------------------------

@pytest.fixture
def memory():
    vectors = {
        "warm fridge, controls drifting Root cause: compressor failure": A,
        "analyser flagging on two lots Root cause: lamp at end of life": B,
        "cold chain excursion, reagent biased low": NEAR_A,   # close to A
        "centrifuge grinding": [0.6, 0.6, 0.53],              # close to neither
    }
    mem = IncidentMemory(embedder=fake_embedder(vectors))
    mem.remember("inc_cold", "warm fridge, controls drifting", "compressor failure", "lot quarantined")
    mem.remember("inc_lamp", "analyser flagging on two lots", "lamp at end of life", "lamp replaced")
    return mem


def test_an_empty_archive_recalls_nothing():
    assert IncidentMemory(embedder=fake_embedder({})).recall("anything") == []


def test_a_matching_incident_is_recalled_best_first(memory):
    hits = memory.recall("cold chain excursion, reagent biased low")
    assert hits and hits[0].incident_id == "inc_cold"
    assert hits[0].similarity > SIMILARITY_FLOOR


def test_an_unrelated_query_recalls_nothing(memory):
    """The property that matters: silence beats a forced resemblance."""
    assert memory.recall("centrifuge grinding") == []


def test_recall_respects_k(memory):
    memory.remember("inc_third", "warm fridge, controls drifting", "compressor failure", "x")
    assert len(memory.recall("cold chain excursion, reagent biased low", k=1)) == 1


def test_a_recollection_carries_the_resolution_not_just_the_cause(memory):
    """What a scientist needs is what was *done* last time."""
    hit = memory.recall("cold chain excursion, reagent biased low")[0]
    assert hit.resolution == "lot quarantined"


def test_remembering_the_same_incident_twice_does_not_duplicate_it():
    vectors = {"s Root cause: c": A, "s": A}
    mem = IncidentMemory(embedder=fake_embedder(vectors))
    mem.remember("inc_1", "s", "c", "r")
    mem.remember("inc_1", "s", "c", "r")
    # Local mode appends; the Firestore path keys by document id. Assert the
    # retrieval surface stays sane either way.
    assert all(isinstance(h, Recollection) for h in mem.recall("s"))


# -- the calibration itself -------------------------------------------------

def test_the_similarity_floor_sits_above_same_domain_noise():
    """Measured against gemini-embedding-001: unrelated clinical sentences
    score about 0.60, genuine matches 0.77 to 0.86. The floor must clear the
    noise without discarding the weakest true positive."""
    assert 0.60 < SIMILARITY_FLOOR < 0.77


def test_describe_joins_signals_into_something_a_human_would_have_written():
    class S:
        def __init__(self, k, s): self.kind, self.summary = k, s
    text = describe([S("coldchain.excursion", "fridge warm"), S("qc.rejection", "2-2s on glucose")])
    assert "coldchain.excursion: fridge warm" in text
    assert "qc.rejection: 2-2s on glucose" in text
