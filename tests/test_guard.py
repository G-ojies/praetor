"""The guard on a publicly reachable control plane.

Going public means judges need no access grant -- worth a lot, since a failed
invite means an unjudgeable entry -- but it also means every endpoint that
costs money is reachable by anyone. These tests hold the two lines that matter:
telemetry ingestion is never open, and paid-model endpoints are bounded.
"""

import time

import pytest

from service.guard import PAID, READS, Bucket


@pytest.fixture(autouse=True)
def _reset():
    for b in (READS, PAID):
        b.tokens.clear(); b.seen.clear()
    yield


# -- the bucket -------------------------------------------------------------

def test_a_fresh_client_starts_full():
    b = Bucket(capacity=3, per_second=1)
    now = 1000.0
    assert [b.take("ip", now) for _ in range(3)] == [True, True, True]


def test_it_refuses_once_drained():
    b = Bucket(capacity=2, per_second=0.01)
    now = 1000.0
    b.take("ip", now); b.take("ip", now)
    assert b.take("ip", now) is False


def test_tokens_refill_over_time():
    b = Bucket(capacity=2, per_second=1)
    b.take("ip", 1000.0); b.take("ip", 1000.0)
    assert b.take("ip", 1000.0) is False
    assert b.take("ip", 1002.0) is True


def test_clients_are_bucketed_separately():
    """One noisy client must not lock out a judge."""
    b = Bucket(capacity=1, per_second=0.001)
    assert b.take("noisy", 1000.0) is True
    assert b.take("noisy", 1000.0) is False
    assert b.take("judge", 1000.0) is True


def test_retry_after_is_never_zero():
    b = Bucket(capacity=1, per_second=0.01)
    b.take("ip", 1000.0)
    b.take("ip", 1000.0)
    assert b.retry_after("ip") >= 1


# -- the tiers --------------------------------------------------------------

def test_paid_endpoints_are_far_tighter_than_reads():
    """Each media or memory call invokes a model that bills. Reads do not."""
    assert PAID.capacity < READS.capacity
    assert PAID.per_second < READS.per_second / 100


def test_the_paid_bucket_allows_a_demo_but_not_a_loop():
    now = time.time()
    taken = sum(PAID.take("ip", now) for _ in range(20))
    assert 4 <= taken <= 8, f"{taken} paid calls allowed in a burst"


# -- ingest is never open ---------------------------------------------------

def test_ingest_rejects_a_request_with_no_token(monkeypatch):
    from fastapi import HTTPException
    from service import guard

    monkeypatch.setattr(guard, "ALLOW_UNVERIFIED_INGEST", False)

    class Req:
        headers: dict = {}
    with pytest.raises(HTTPException) as e:
        guard.verify_push_token(Req())
    assert e.value.status_code == 401


def test_ingest_rejects_a_malformed_bearer_token(monkeypatch):
    from fastapi import HTTPException
    from service import guard

    monkeypatch.setattr(guard, "ALLOW_UNVERIFIED_INGEST", False)

    class Req:
        headers = {"authorization": "Bearer not-a-real-token"}
    with pytest.raises(HTTPException) as e:
        guard.verify_push_token(Req())
    assert e.value.status_code == 401


def test_ingest_rejects_a_valid_token_from_the_wrong_principal(monkeypatch):
    """A signed Google token is not enough -- it must be *our* push identity.
    Any Google account can mint a valid OIDC token."""
    from fastapi import HTTPException
    from service import guard

    monkeypatch.setattr(guard, "ALLOW_UNVERIFIED_INGEST", False)
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *a, **k: {"email": "someone-else@gmail.com", "email_verified": True})

    class Req:
        headers = {"authorization": "Bearer valid-but-wrong"}
    with pytest.raises(HTTPException) as e:
        guard.verify_push_token(Req())
    assert e.value.status_code == 403


def test_ingest_accepts_the_push_service_account(monkeypatch):
    from service import guard

    monkeypatch.setattr(guard, "ALLOW_UNVERIFIED_INGEST", False)
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *a, **k: {"email": guard.PUSH_SA, "email_verified": True})

    class Req:
        headers = {"authorization": "Bearer good"}
    assert guard.verify_push_token(Req()) == guard.PUSH_SA


def test_an_unverified_email_claim_is_rejected(monkeypatch):
    from fastapi import HTTPException
    from service import guard

    monkeypatch.setattr(guard, "ALLOW_UNVERIFIED_INGEST", False)
    monkeypatch.setattr(
        "google.oauth2.id_token.verify_oauth2_token",
        lambda *a, **k: {"email": guard.PUSH_SA, "email_verified": False})

    class Req:
        headers = {"authorization": "Bearer good"}
    with pytest.raises(HTTPException):
        guard.verify_push_token(Req())
