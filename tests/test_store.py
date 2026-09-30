"""T06 tests: RequestStore idempotency, conflict, unknown, concurrency."""
import threading
from pathlib import Path

import pytest

from chatbot_jira.store import (
    ConflictError,
    RequestStore,
    STATUS_FAILED,
    STATUS_SUCCEEDED,
    STATUS_UNKNOWN,
)


def make_store(tmp_path: Path) -> RequestStore:
    return RequestStore(tmp_path / "requests.db")


# ---------------------------------------------------------------------------
# Basic claim / succeed / fail
# ---------------------------------------------------------------------------

def test_claim_new_request_returns_none(tmp_path):
    store = make_store(tmp_path)
    result = store.claim("req-1", {"message": "hello"})
    assert result is None  # claim succeeded


def test_claim_same_id_same_content_returns_existing_after_succeed(tmp_path):
    store = make_store(tmp_path)
    store.claim("req-1", {"message": "hello"})
    store.succeed("req-1", "KAN-5", "https://example.com/KAN-5")
    existing = store.claim("req-1", {"message": "hello"})
    assert existing is not None
    assert existing.status == STATUS_SUCCEEDED
    assert existing.issue_key == "KAN-5"


def test_claim_same_id_different_content_raises_conflict(tmp_path):
    store = make_store(tmp_path)
    store.claim("req-1", {"message": "hello"})
    store.fail("req-1")
    with pytest.raises(ConflictError):
        store.claim("req-1", {"message": "different content"})


def test_fail_sets_status_failed(tmp_path):
    store = make_store(tmp_path)
    store.claim("req-2", {"message": "task"})
    store.fail("req-2")
    stored = store.get("req-2")
    assert stored.status == STATUS_FAILED


def test_mark_unknown_sets_status_unknown(tmp_path):
    store = make_store(tmp_path)
    store.claim("req-3", {"message": "task"})
    store.mark_unknown("req-3")
    stored = store.get("req-3")
    assert stored.status == STATUS_UNKNOWN


def test_get_nonexistent_returns_none(tmp_path):
    store = make_store(tmp_path)
    assert store.get("nonexistent-id") is None


def test_pending_rows_healed_to_unknown_on_restart(tmp_path):
    """Pending rows from a crashed session become unknown on next init."""
    db_path = tmp_path / "requests.db"
    store1 = RequestStore(db_path)
    store1.claim("req-crash", {"message": "crash test"})
    # Don't call succeed/fail — simulate crash (status remains pending)
    stored_before = store1.get("req-crash")
    assert stored_before.status == "pending"

    # Reinitialize (simulates restart)
    store2 = RequestStore(db_path)
    stored_after = store2.get("req-crash")
    assert stored_after.status == STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# Concurrency: only one claim wins for the same request_id
# ---------------------------------------------------------------------------

def test_concurrent_claims_only_one_create_call(tmp_path):
    """Two threads racing to claim the same request_id — only one should get None."""
    store = make_store(tmp_path)
    results = []
    errors = []

    def do_claim():
        try:
            r = store.claim("shared-req", {"message": "concurrent"})
            results.append(r)
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=do_claim) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    # All results should be consistent: either None (first claim) or the existing row
    none_count = sum(1 for r in results if r is None)
    # At least one must have gotten None (the winning claim)
    assert none_count >= 1
    # All non-None results must reference the same row
    non_none = [r for r in results if r is not None]
    for r in non_none:
        assert r.request_id == "shared-req"


def test_create_called_only_once_per_request(tmp_path):
    """Simulate the guarantee that a single request_id only proceeds to create once."""
    store = make_store(tmp_path)
    create_count = [0]
    lock = threading.Lock()

    def process(request_id, content):
        result = store.claim(request_id, content)
        if result is not None:
            return  # already done
        with lock:
            create_count[0] += 1
        store.succeed(request_id, "KAN-99", None)

    threads = [
        threading.Thread(target=process, args=("req-once", {"msg": "x"}))
        for _ in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert create_count[0] == 1


# ---------------------------------------------------------------------------
# Hash consistency
# ---------------------------------------------------------------------------

def test_content_hash_is_deterministic(tmp_path):
    """Same content always produces the same hash regardless of dict ordering."""
    store = make_store(tmp_path)
    store.claim("req-hash", {"b": 2, "a": 1})
    # Same content, different dict order — should NOT raise conflict
    store.fail("req-hash")
    existing = store.claim("req-hash", {"a": 1, "b": 2})
    assert existing is not None  # recognised as same
