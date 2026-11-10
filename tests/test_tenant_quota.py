"""Tests for tenant_quota."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone

import pytest

from tenant_quota import (
    Decision,
    InMemoryCounterStore,
    QuotaError,
    QuotaManager,
    QuotaRule,
    Reservation,
)


@pytest.fixture
def fixed_clock() -> Callable[[], datetime]:
    current = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)

    def clock() -> datetime:
        return current

    return clock


@pytest.fixture
def manager(fixed_clock: Callable[[], datetime]) -> QuotaManager:
    plans = {
        "free": {
            "api_calls": QuotaRule(limit=1000, window="day"),
            "exports": QuotaRule(limit=50, window="month"),
        }
    }
    overrides = {"t2": {"api_calls": QuotaRule(limit=5000, window="day")}}
    return QuotaManager(plans, overrides=overrides, clock=fixed_clock)


# Test QuotaRule validation
def test_quota_rule_valid() -> None:
    rule = QuotaRule(limit=10, window="day")
    assert rule.limit == 10
    assert rule.window == "day"
    assert rule.soft_ratio is None


def test_quota_rule_invalid_limit() -> None:
    with pytest.raises(QuotaError) as exc:
        QuotaRule(limit=-1, window="day")
    assert exc.value.code == "invalid_rule"


def test_quota_rule_invalid_soft_ratio() -> None:
    with pytest.raises(QuotaError) as exc:
        QuotaRule(limit=10, window="day", soft_ratio=0)
    assert exc.value.code == "invalid_rule"
    with pytest.raises(QuotaError) as exc:
        QuotaRule(limit=10, window="day", soft_ratio=1)
    assert exc.value.code == "invalid_rule"
    with pytest.raises(QuotaError) as exc:
        QuotaRule(limit=10, window="day", soft_ratio=-0.1)
    assert exc.value.code == "invalid_rule"


def test_quota_rule_invalid_window() -> None:
    with pytest.raises(QuotaError) as exc:
        QuotaRule(limit=10, window="year")  # type: ignore[arg-type]
    assert exc.value.code == "invalid_rule"


# Test Decision and Reservation dataclasses
def test_decision_creation() -> None:
    reset = datetime(2026, 11, 10, tzinfo=timezone.utc)
    d = Decision(allowed=True, used=5, limit=10, remaining=5, reset_at=reset)
    assert d.allowed
    assert d.used == 5
    assert d.remaining == 5
    assert d.reset_at == reset


def test_reservation_creation() -> None:
    expires = datetime(2026, 11, 9, 15, 1, tzinfo=timezone.utc)
    r = Reservation(id="abc", tenant="t1", resource="r", amount=5, expires_at=expires)
    assert r.id == "abc"
    assert r.tenant == "t1"
    assert r.resource == "r"
    assert r.amount == 5
    assert r.expires_at == expires


# Test InMemoryCounterStore
def test_in_memory_store_add_and_get(fixed_clock: Callable[[], datetime]) -> None:
    store = InMemoryCounterStore(max_counters=3, clock=fixed_clock)
    expires = fixed_clock() + timedelta(hours=1)
    added, val = store.add_if_within("k", 5, 10, expires)
    assert added
    assert val == 5
    assert store.get("k") == 5


def test_in_memory_store_add_exceeding() -> None:
    def clock() -> datetime:
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    store = InMemoryCounterStore(max_counters=3, clock=clock)
    expires = clock() + timedelta(hours=1)
    added, val = store.add_if_within("k", 6, 5, expires)
    assert not added
    assert val == 0  # no initial value, so 0
    # Add initial value then exceed
    added, _ = store.add_if_within("k", 3, 5, expires)
    assert added
    added, val = store.add_if_within("k", 3, 5, expires)
    assert not added
    assert val == 3


def test_in_memory_store_subtract(fixed_clock: Callable[[], datetime]) -> None:
    store = InMemoryCounterStore(max_counters=3, clock=fixed_clock)
    expires = fixed_clock() + timedelta(hours=1)
    store.add_if_within("k", 10, 100, expires)
    store.subtract("k", 4)
    assert store.get("k") == 6
    store.subtract("k", 10)  # never below 0
    assert store.get("k") == 0
    store.subtract("missing", 5)  # ignores missing
    assert store.get("missing") == 0


def test_in_memory_store_purge_expired_and_full(fixed_clock: Callable[[], datetime]) -> None:
    # We'll use a mutable clock to simulate time passing
    current = [fixed_clock()]

    def clock() -> datetime:
        return current[0]

    store = InMemoryCounterStore(max_counters=2, clock=clock)
    expires1 = current[0] + timedelta(hours=1)
    expires2 = current[0] + timedelta(hours=2)
    expires3 = current[0] + timedelta(hours=3)
    store.add_if_within("a", 1, 10, expires1)
    store.add_if_within("b", 2, 10, expires2)
    # Now at capacity (2)
    with pytest.raises(QuotaError) as exc:
        store.add_if_within("c", 3, 10, expires3)
    assert exc.value.code == "store_full"
    # Advance clock beyond first expiry
    current[0] = fixed_clock() + timedelta(hours=2)
    # Now add should succeed after purging 'a'
    added, val = store.add_if_within("c", 3, 10, expires3)
    assert added
    assert val == 3
    assert store.get("a") == 0  # purged


def test_in_memory_store_invalid_clock() -> None:
    def naive_clock() -> datetime:
        return datetime(2026, 1, 1)  # no tzinfo

    with pytest.raises(QuotaError) as exc:
        InMemoryCounterStore(clock=naive_clock)
    assert exc.value.code == "invalid_clock"


# Test QuotaManager consume
def test_consume_success(manager: QuotaManager) -> None:
    decision = manager.consume("t1", "free", "api_calls", 999)
    assert decision == Decision(
        allowed=True,
        used=999,
        limit=1000,
        remaining=1,
        reset_at=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc),
    )


def test_consume_denied(manager: QuotaManager) -> None:
    manager.consume("t1", "free", "api_calls", 999)
    decision = manager.consume("t1", "free", "api_calls", 2)
    assert not decision.allowed
    assert decision.used == 999
    assert decision.remaining == 1
    assert decision.limit == 1000
    # Now exactly limit
    decision = manager.consume("t1", "free", "api_calls", 1)
    assert decision.allowed
    assert decision.used == 1000
    assert decision.remaining == 0


def test_consume_amount_validation(manager: QuotaManager) -> None:
    with pytest.raises(QuotaError) as exc:
        manager.consume("t1", "free", "api_calls", 0)
    assert exc.value.code == "invalid_amount"
    with pytest.raises(QuotaError) as exc:
        manager.consume("t1", "free", "api_calls", -1)
    assert exc.value.code == "invalid_amount"
    with pytest.raises(QuotaError) as exc:
        manager.consume("t1", "free", "api_calls", True)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_amount"


def test_consume_invalid_tenant(manager: QuotaManager) -> None:
    with pytest.raises(QuotaError) as exc:
        manager.consume("a b", "free", "api_calls")
    assert exc.value.code == "invalid_tenant"


def test_consume_invalid_resource(manager: QuotaManager) -> None:
    with pytest.raises(QuotaError) as exc:
        manager.consume("t1", "free", "invalid resource")
    assert exc.value.code == "unknown_resource"


def test_consume_unknown_plan(manager: QuotaManager) -> None:
    with pytest.raises(QuotaError) as exc:
        manager.consume("t1", "gold", "api_calls")
    assert exc.value.code == "unknown_plan"


def test_consume_unknown_resource(manager: QuotaManager) -> None:
    with pytest.raises(QuotaError) as exc:
        manager.consume("t1", "free", "nonexistent")
    assert exc.value.code == "unknown_resource"


def test_peek(manager: QuotaManager) -> None:
    manager.consume("t1", "free", "api_calls", 100)
    d = manager.peek("t1", "free", "api_calls")
    assert d == Decision(
        allowed=True,
        used=100,
        limit=1000,
        remaining=900,
        reset_at=datetime(2026, 11, 10, tzinfo=timezone.utc),
    )


def test_peek_at_limit(manager: QuotaManager) -> None:
    manager.consume("t1", "free", "api_calls", 1000)
    d = manager.peek("t1", "free", "api_calls")
    assert not d.allowed
    assert d.used == 1000
    assert d.remaining == 0


def test_zero_limit_always_denied() -> None:
    def clock() -> datetime:
        return datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)

    plans = {"p": {"r": QuotaRule(limit=0, window="day")}}
    mgr = QuotaManager(plans, clock=clock)
    d = mgr.consume("t", "p", "r")
    assert d == Decision(
        allowed=False,
        used=0,
        limit=0,
        remaining=0,
        reset_at=datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc),
    )
    d = mgr.peek("t", "p", "r")
    assert not d.allowed
    assert d.used == 0
    assert d.remaining == 0


# Test window calculations
def test_month_window_reset() -> None:
    current = [datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)]

    def clock() -> datetime:
        return current[0]

    plans = {"p": {"r": QuotaRule(limit=10, window="month")}}
    mgr = QuotaManager(plans, clock=clock)
    d = mgr.peek("t", "p", "r")
    assert d.reset_at == datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)
    # Consume inside December's month window.
    consumed = mgr.consume("t", "p", "r", 7)
    assert consumed.allowed
    assert consumed.used == 7
    assert consumed.reset_at == datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)
    # At the reset instant the used count starts again from 0.
    current[0] = datetime(2027, 1, 1, 0, 0, tzinfo=timezone.utc)
    d = mgr.consume("t", "p", "r", 7)
    assert d == Decision(
        allowed=True,
        used=7,
        limit=10,
        remaining=3,
        reset_at=datetime(2027, 2, 1, 0, 0, tzinfo=timezone.utc),
    )


def test_minute_window_reset() -> None:
    def clock() -> datetime:
        return datetime(2026, 1, 1, 12, 34, 56, tzinfo=timezone.utc)

    plans = {"p": {"r": QuotaRule(limit=10, window="minute")}}
    mgr = QuotaManager(plans, clock=clock)
    d = mgr.peek("t", "p", "r")
    assert d.reset_at == datetime(2026, 1, 1, 12, 35, tzinfo=timezone.utc)


def test_hour_window_reset() -> None:
    def clock() -> datetime:
        return datetime(2026, 1, 1, 12, 34, 56, tzinfo=timezone.utc)

    plans = {"p": {"r": QuotaRule(limit=10, window="hour")}}
    mgr = QuotaManager(plans, clock=clock)
    d = mgr.peek("t", "p", "r")
    assert d.reset_at == datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)


def test_day_window_reset() -> None:
    def clock() -> datetime:
        return datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    plans = {"p": {"r": QuotaRule(limit=10, window="day")}}
    mgr = QuotaManager(plans, clock=clock)
    d = mgr.peek("t", "p", "r")
    assert d.reset_at == datetime(2026, 1, 2, 0, 0, tzinfo=timezone.utc)


# Test reservations
def test_reserve_success(manager: QuotaManager) -> None:
    r = manager.reserve("t1", "free", "exports", 10, ttl_s=60)
    assert isinstance(r, Reservation)
    assert r.amount == 10
    assert r.tenant == "t1"
    assert r.resource == "exports"
    # Used should be 10
    d = manager.peek("t1", "free", "exports")
    assert d.used == 10


def test_reserve_denied(manager: QuotaManager) -> None:
    # Fill quota
    for _ in range(5):
        manager.consume("t1", "free", "exports", 10)
    # Now used=50, cannot reserve more
    d = manager.reserve("t1", "free", "exports", 1, ttl_s=60)
    assert isinstance(d, Decision)
    assert not d.allowed
    assert d.used == 50
    assert d.limit == 50
    assert d.remaining == 0


def test_reserve_amount_validation(manager: QuotaManager) -> None:
    with pytest.raises(QuotaError) as exc:
        manager.reserve("t1", "free", "exports", 0, ttl_s=60)
    assert exc.value.code == "invalid_amount"
    with pytest.raises(QuotaError) as exc:
        manager.reserve("t1", "free", "exports", -1, ttl_s=60)
    assert exc.value.code == "invalid_amount"
    with pytest.raises(QuotaError) as exc:
        manager.reserve("t1", "free", "exports", True, ttl_s=60)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_amount"
    with pytest.raises(QuotaError) as exc:
        manager.reserve("t1", "free", "exports", 1, ttl_s=0)
    assert exc.value.code == "invalid_amount"


def test_reserve_ttl_validation(manager: QuotaManager) -> None:
    with pytest.raises(QuotaError) as exc:
        manager.reserve("t1", "free", "exports", 1, ttl_s=-1)
    assert exc.value.code == "invalid_amount"


def test_commit_adjustment(manager: QuotaManager) -> None:
    r = manager.reserve("t1", "free", "exports", 10, ttl_s=60)
    assert isinstance(r, Reservation)
    d = manager.commit(r.id, 4)
    assert d.allowed
    assert d.used == 4
    assert d.limit == 50
    assert d.remaining == 46
    # Second commit fails
    with pytest.raises(QuotaError) as exc:
        manager.commit(r.id, 4)
    assert exc.value.code == "unknown_reservation"


def test_reservation_committed_after_midnight() -> None:
    current = [datetime(2026, 11, 9, 23, 59, 59, tzinfo=timezone.utc)]

    def clock() -> datetime:
        return current[0]

    plans = {"p": {"r": QuotaRule(limit=100, window="day")}}
    mgr = QuotaManager(plans, clock=clock)
    r = mgr.reserve("t", "p", "r", 10, ttl_s=600)
    assert isinstance(r, Reservation)
    # Cross midnight: the reservation belongs to the previous day's window.
    current[0] = datetime(2026, 11, 10, 0, 0, 5, tzinfo=timezone.utc)
    d = mgr.commit(r.id, 4)
    assert d == Decision(
        allowed=True,
        used=0,
        limit=100,
        remaining=100,
        reset_at=datetime(2026, 11, 11, 0, 0, tzinfo=timezone.utc),
    )
    # Committing again raises: the reservation is terminal and removed.
    with pytest.raises(QuotaError) as exc:
        mgr.commit(r.id, 4)
    assert exc.value.code == "unknown_reservation"


def test_commit_invalid_actual(manager: QuotaManager) -> None:
    r = manager.reserve("t1", "free", "exports", 10, ttl_s=60)
    assert isinstance(r, Reservation)
    with pytest.raises(QuotaError) as exc:
        manager.commit(r.id, 11)
    assert exc.value.code == "invalid_amount"
    with pytest.raises(QuotaError) as exc:
        manager.commit(r.id, -1)
    assert exc.value.code == "invalid_amount"
    with pytest.raises(QuotaError) as exc:
        manager.commit(r.id, True)  # type: ignore[arg-type]
    assert exc.value.code == "invalid_amount"


def test_cancel_releases(manager: QuotaManager) -> None:
    r = manager.reserve("t1", "free", "exports", 10, ttl_s=60)
    assert isinstance(r, Reservation)
    manager.cancel(r.id)
    d = manager.peek("t1", "free", "exports")
    assert d.used == 0
    with pytest.raises(QuotaError) as exc:
        manager.cancel(r.id)
    assert exc.value.code == "unknown_reservation"


def test_reservation_expiry_sweep(manager: QuotaManager) -> None:
    # Use a mutable clock to simulate time passing
    current = [datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)]

    def clock() -> datetime:
        return current[0]

    plan = {"p": {"r": QuotaRule(limit=100, window="day")}}
    events = []
    mgr = QuotaManager(plan, clock=clock, on_event=events.append)
    r = mgr.reserve("t", "p", "r", 10, ttl_s=60)
    assert isinstance(r, Reservation)
    # Advance clock beyond ttl
    current[0] = current[0] + timedelta(seconds=61)
    # Next call for same tenant/resource triggers sweep
    d = mgr.peek("t", "p", "r")
    assert d.used == 0  # released
    # Verify event
    assert len(events) == 1
    assert events[0]["event"] == "reservation_expired"
    assert events[0]["amount"] == 10


def test_reservation_max_limit(manager: QuotaManager) -> None:
    # Set max_reservations low for test
    plans = {"p": {"r": QuotaRule(limit=100, window="day")}}

    def clock() -> datetime:
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    mgr = QuotaManager(plans, clock=clock, max_reservations=1)
    r1 = mgr.reserve("t", "p", "r", 1, ttl_s=60)
    assert isinstance(r1, Reservation)
    with pytest.raises(QuotaError) as exc:
        mgr.reserve("t", "p", "r", 1, ttl_s=60)
    assert exc.value.code == "too_many_reservations"


# Test soft limit events
def test_soft_limit_event_once_per_window() -> None:
    def clock() -> datetime:
        return datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)

    plans = {"free": {"api_calls": QuotaRule(limit=1000, window="day", soft_ratio=0.8)}}
    events = []
    mgr = QuotaManager(plans, clock=clock, on_event=events.append)
    # consume to 799, no event
    mgr.consume("t1", "free", "api_calls", 799)
    assert len(events) == 0
    # consume 1 to cross 800
    mgr.consume("t1", "free", "api_calls", 1)
    assert len(events) == 1
    assert events[0] == {
        "event": "soft_limit",
        "tenant": "t1",
        "resource": "api_calls",
        "used": 800,
        "limit": 1000,
    }
    # further consume should not re-emit
    mgr.consume("t1", "free", "api_calls", 100)
    assert len(events) == 1


def test_soft_limit_event_in_reserve() -> None:
    def clock() -> datetime:
        return datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)

    plans = {"free": {"api_calls": QuotaRule(limit=100, window="day", soft_ratio=0.5)}}
    events = []
    mgr = QuotaManager(plans, clock=clock, on_event=events.append)
    mgr.consume("t1", "free", "api_calls", 49)
    assert len(events) == 0
    mgr.reserve("t1", "free", "api_calls", 5, ttl_s=60)
    assert len(events) == 1
    assert events[0]["used"] == 54


# Test overrides
def test_override_rule(manager: QuotaManager) -> None:
    d = manager.peek("t2", "free", "api_calls")
    assert d.limit == 5000


def test_override_missing_falls_back_to_plan(manager: QuotaManager) -> None:
    d = manager.peek("t1", "free", "api_calls")
    assert d.limit == 1000


# Test clock handling
def test_non_utc_aware_clock() -> None:
    # A non-UTC aware clock (e.g., +05:00)
    offset = timezone(timedelta(hours=5))
    now = datetime(2026, 11, 9, 20, 0, tzinfo=offset)  # 15:00 UTC

    def clock() -> datetime:
        return now

    plans = {"p": {"r": QuotaRule(limit=10, window="day")}}
    mgr = QuotaManager(plans, clock=clock)
    d = mgr.peek("t", "p", "r")
    # The reset_at should be midnight UTC, which corresponds to 05:00 local next day
    expected_reset = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
    assert d.reset_at == expected_reset


def test_invalid_clock_naive() -> None:
    plans = {"p": {"r": QuotaRule(limit=10, window="day")}}
    with pytest.raises(QuotaError) as exc:
        QuotaManager(plans, clock=lambda: datetime.now())  # naive
    assert exc.value.code == "invalid_clock"


def test_clock_returns_naive_after_init() -> None:
    # Initial call valid, later call naive
    call_count = 0

    def clock() -> datetime:
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            return datetime(2026, 1, 1, tzinfo=timezone.utc)
        return datetime(2026, 1, 1)  # naive

    plans = {"p": {"r": QuotaRule(limit=10, window="day")}}
    mgr = QuotaManager(plans, clock=clock)
    with pytest.raises(QuotaError) as exc:
        mgr.peek("t", "p", "r")
    assert exc.value.code == "invalid_clock"


# Test store_full acceptance
def test_store_full_acceptance() -> None:
    def clock() -> datetime:
        return datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    plans = {
        "p1": {"r1": QuotaRule(limit=10, window="day")},
        "p2": {"r2": QuotaRule(limit=10, window="day")},
    }
    mgr = QuotaManager(plans, clock=clock, store=InMemoryCounterStore(max_counters=2, clock=clock))
    # Consume first counter
    mgr.consume("t1", "p1", "r1", 1)
    mgr.consume("t2", "p2", "r2", 1)
    # Third different key (different tenant/resource/window) should raise store_full
    with pytest.raises(QuotaError) as exc:
        mgr.consume("t3", "p1", "r1", 1)
    assert exc.value.code == "store_full"
