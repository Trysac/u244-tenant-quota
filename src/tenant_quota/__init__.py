"""Per-tenant usage quotas over UTC calendar windows.

Provides plan limits with per-tenant overrides, atomic check-and-consume
through a store port, reservations for long-running operations, soft-limit
events and reset times.
"""

from __future__ import annotations

import logging
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Literal, Protocol, runtime_checkable

__all__ = [
    "CounterStore",
    "Decision",
    "InMemoryCounterStore",
    "QuotaError",
    "QuotaManager",
    "QuotaRule",
    "Reservation",
]


class QuotaError(Exception):
    """Error raised for quota-related issues."""

    def __init__(
        self,
        code: Literal[
            "invalid_amount",
            "invalid_tenant",
            "unknown_plan",
            "unknown_resource",
            "unknown_reservation",
            "too_many_reservations",
            "store_full",
            "invalid_clock",
            "invalid_rule",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class QuotaRule:
    """Definition of a quota rule for a resource.

    Attributes:
        limit: Maximum allowed amount in the window.
        window: Fixed UTC calendar window: 'minute', 'hour', 'day', or 'month'.
        soft_ratio: Optional ratio (0, 1) that triggers a soft-limit event
            when usage first crosses ``ceil(soft_ratio * limit)``.
    """

    limit: int
    window: Literal["minute", "hour", "day", "month"]
    soft_ratio: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.limit, int) or isinstance(self.limit, bool) or self.limit < 0:
            raise QuotaError("invalid_rule")
        if self.window not in ("minute", "hour", "day", "month"):
            raise QuotaError("invalid_rule")
        if self.soft_ratio is not None:
            if not isinstance(self.soft_ratio, (int, float)) or isinstance(self.soft_ratio, bool):
                raise QuotaError("invalid_rule")
            soft = float(self.soft_ratio)
            if soft <= 0.0 or soft >= 1.0:
                raise QuotaError("invalid_rule")
            object.__setattr__(self, "soft_ratio", soft)  # Ensure float


@dataclass(frozen=True)
class Decision:
    """Result of a quota check or consumption attempt.

    Attributes:
        allowed: Whether the operation is permitted.
        used: Current usage count in the relevant window.
        limit: The limit being enforced.
        remaining: Quota remaining (``limit - used``).
        reset_at: Datetime when the window resets (next window start).
    """

    allowed: bool
    used: int
    limit: int
    remaining: int
    reset_at: datetime


@dataclass(frozen=True)
class Reservation:
    """A capacity reservation for a future operation.

    Attributes:
        id: Unique reservation identifier.
        tenant: Tenant identifier.
        resource: Resource name.
        amount: Reserved amount.
        expires_at: Datetime when the reservation expires (UTC).
    """

    id: str
    tenant: str
    resource: str
    amount: int
    expires_at: datetime


@runtime_checkable
class CounterStore(Protocol):
    """Protocol for atomic counter stores used by :class:`QuotaManager`.

    Implementations must be thread-safe and handle expiry of window counters.
    """

    def add_if_within(
        self, key: str, amount: int, limit: int, expires_at: datetime
    ) -> tuple[bool, int]:
        """Atomically add ``amount`` to the counter if within ``limit``.

        Args:
            key: Unique counter key for the tenant/resource/window.
            amount: Amount to add (must be positive).
            limit: Maximum allowed total after addition.
            expires_at: Datetime when the counter becomes stale (window end).

        Returns:
            Tuple of (added, current_value_after_attempt).
        """
        ...

    def subtract(self, key: str, amount: int) -> None:
        """Subtract ``amount`` from the counter, never below zero."""
        ...

    def get(self, key: str) -> int:
        """Return the current value for ``key`` (0 if missing)."""
        ...


class InMemoryCounterStore:
    """Thread-safe in-memory implementation of :class:`CounterStore`.

    Args:
        max_counters: Maximum number of live (non-expired) counters to keep.
        clock: Callable returning a timezone-aware datetime (UTC).
    """

    def __init__(
        self,
        *,
        max_counters: int = 100_000,
        clock: Callable[[], datetime],
    ) -> None:
        if not isinstance(max_counters, int) or max_counters <= 0:
            raise ValueError("max_counters must be a positive integer")
        self._max_counters = max_counters
        self._clock = clock
        # Validate clock early by calling once
        test_now = self._clock()
        if not self._is_aware(test_now):
            raise QuotaError("invalid_clock")
        self._data: dict[str, tuple[int, datetime]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _is_aware(dt: datetime) -> bool:
        return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None

    def _purge_expired_locked(self, now: datetime) -> None:
        purge_keys = [key for key, (_, expires) in self._data.items() if expires <= now]
        for key in purge_keys:
            del self._data[key]

    def add_if_within(
        self, key: str, amount: int, limit: int, expires_at: datetime
    ) -> tuple[bool, int]:
        now = self._clock()
        if not self._is_aware(now):
            raise QuotaError("invalid_clock")
        now_utc = now.astimezone(timezone.utc)
        with self._lock:
            self._purge_expired_locked(now_utc)
            if key in self._data:
                current, _exp = self._data[key]
                if current + amount <= limit:
                    self._data[key] = (current + amount, expires_at)
                    return True, current + amount
                return False, current
            else:
                if amount <= limit:
                    if len(self._data) >= self._max_counters:
                        raise QuotaError("store_full")
                    self._data[key] = (amount, expires_at)
                    return True, amount
                return False, 0

    def subtract(self, key: str, amount: int) -> None:
        with self._lock:
            if key in self._data:
                current, expires = self._data[key]
                new_val = max(0, current - amount)
                self._data[key] = (new_val, expires)

    def get(self, key: str) -> int:
        with self._lock:
            if key in self._data:
                return self._data[key][0]
            return 0


class QuotaManager:
    """Enforce per-tenant quotas over UTC calendar windows.

    Args:
        plans: Mapping of plan name to mapping of resource name to
            :class:`QuotaRule`.
        overrides: Optional mapping of tenant to mapping of resource to
            :class:`QuotaRule` that takes precedence over plan rules.
        store: Optional :class:`CounterStore` implementation; defaults to
            :class:`InMemoryCounterStore`.
        clock: Callable returning a timezone-aware datetime; must be
            timezone-aware.
        id_factory: Callable returning a string for reservation IDs;
            defaults to ``uuid.uuid4().hex``.
        max_reservations: Maximum active reservations allowed.
        on_event: Optional callback receiving event dictionaries.
    """

    _TENANT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
    _RESOURCE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

    def __init__(
        self,
        plans: Mapping[str, Mapping[str, QuotaRule]],
        *,
        overrides: Mapping[str, Mapping[str, QuotaRule]] | None = None,
        store: CounterStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        max_reservations: int = 10_000,
        on_event: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        # Validate clock by calling it once
        test_now = clock()
        if not self._is_aware(test_now):
            raise QuotaError("invalid_clock")
        if not isinstance(max_reservations, int) or max_reservations < 0:
            raise ValueError("max_reservations must be a non-negative integer")
        self._plans = dict(plans)
        self._overrides = dict(overrides or {})
        self._store = store if store is not None else InMemoryCounterStore(clock=clock)
        self._clock = clock
        self._id_factory = id_factory
        self._max_reservations = max_reservations
        self._on_event = on_event
        self._reservations: dict[str, _ReservationEntry] = {}
        self._soft_events_fired: set[tuple[str, str, str]] = set()  # (tenant, resource, key)
        self._logger = logging.getLogger(__name__)
        self._logger.addHandler(logging.NullHandler())

    @staticmethod
    def _is_aware(dt: datetime) -> bool:
        return dt.tzinfo is not None and dt.tzinfo.utcoffset(dt) is not None

    def _now_utc(self) -> datetime:
        now = self._clock()
        if not self._is_aware(now):
            raise QuotaError("invalid_clock")
        return now.astimezone(timezone.utc)

    def _validate_tenant(self, tenant: str) -> None:
        if not isinstance(tenant, str) or not self._TENANT_RE.fullmatch(tenant):
            raise QuotaError("invalid_tenant")

    def _validate_resource(self, resource: str) -> None:
        if not isinstance(resource, str) or not self._RESOURCE_RE.fullmatch(resource):
            raise QuotaError("unknown_resource")

    def _validate_amount(self, amount: int, *, allow_zero: bool = False) -> None:
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise QuotaError("invalid_amount")
        if allow_zero:
            if amount < 0:
                raise QuotaError("invalid_amount")
        else:
            if amount <= 0:
                raise QuotaError("invalid_amount")

    def _resolve_rule(self, tenant: str, plan: str, resource: str) -> QuotaRule:
        # Check overrides first
        override = self._overrides.get(tenant, {}).get(resource)
        if override is not None:
            return override
        # Then plan
        if plan not in self._plans:
            raise QuotaError("unknown_plan")
        plan_rules = self._plans[plan]
        if resource not in plan_rules:
            raise QuotaError("unknown_resource")
        return plan_rules[resource]

    def _window_start(
        self, now_utc: datetime, window: Literal["minute", "hour", "day", "month"]
    ) -> datetime:
        if window == "minute":
            return now_utc.replace(second=0, microsecond=0)
        elif window == "hour":
            return now_utc.replace(minute=0, second=0, microsecond=0)
        elif window == "day":
            return now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        else:  # month
            return now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    def _next_window_start(
        self, now_utc: datetime, window: Literal["minute", "hour", "day", "month"]
    ) -> datetime:
        start = self._window_start(now_utc, window)
        if window == "minute":
            return start + timedelta(minutes=1)
        elif window == "hour":
            return start + timedelta(hours=1)
        elif window == "day":
            return start + timedelta(days=1)
        else:  # month
            if start.month == 12:
                return start.replace(year=start.year + 1, month=1)
            else:
                return start.replace(month=start.month + 1)

    def _key(self, tenant: str, resource: str, window_start: datetime) -> str:
        return f"{tenant}:{resource}:{window_start.isoformat()}"

    def _sweep_expired(self, tenant: str, resource: str, now_utc: datetime) -> None:
        expired_ids = []
        for rid, entry in self._reservations.items():
            if (
                entry.tenant == tenant
                and entry.resource == resource
                and entry.expires_at <= now_utc
            ):
                expired_ids.append(rid)
        for rid in expired_ids:
            entry = self._reservations.pop(rid)
            self._store.subtract(entry.key, entry.amount)
            if self._on_event is not None:
                self._on_event(
                    {
                        "event": "reservation_expired",
                        "tenant": tenant,
                        "resource": resource,
                        "amount": entry.amount,
                    }
                )

    def consume(self, tenant: str, plan: str, resource: str, amount: int = 1) -> Decision:
        """Attempt to consume ``amount`` of ``resource`` for ``tenant``.

        Returns a :class:`Decision` indicating whether the consumption was
        allowed and the current usage.
        """
        self._validate_tenant(tenant)
        self._validate_resource(resource)
        self._validate_amount(amount, allow_zero=False)
        now_utc = self._now_utc()
        self._sweep_expired(tenant, resource, now_utc)
        rule = self._resolve_rule(tenant, plan, resource)
        window_start = self._window_start(now_utc, rule.window)
        next_start = self._next_window_start(now_utc, rule.window)
        key = self._key(tenant, resource, window_start)
        current = self._store.get(key)
        added, value_after = self._store.add_if_within(key, amount, rule.limit, next_start)
        if added:
            # Check soft limit
            if rule.soft_ratio is not None:
                threshold = ceil(rule.soft_ratio * rule.limit)
                soft_key = (tenant, resource, key)
                if current < threshold <= value_after and soft_key not in self._soft_events_fired:
                    self._soft_events_fired.add(soft_key)
                    if self._on_event is not None:
                        self._on_event(
                            {
                                "event": "soft_limit",
                                "tenant": tenant,
                                "resource": resource,
                                "used": value_after,
                                "limit": rule.limit,
                            }
                        )
            return Decision(
                allowed=True,
                used=value_after,
                limit=rule.limit,
                remaining=rule.limit - value_after,
                reset_at=next_start,
            )
        else:
            return Decision(
                allowed=False,
                used=current,
                limit=rule.limit,
                remaining=rule.limit - current,
                reset_at=next_start,
            )

    def peek(self, tenant: str, plan: str, resource: str) -> Decision:
        """Check current usage without consuming."""
        self._validate_tenant(tenant)
        self._validate_resource(resource)
        now_utc = self._now_utc()
        self._sweep_expired(tenant, resource, now_utc)
        rule = self._resolve_rule(tenant, plan, resource)
        window_start = self._window_start(now_utc, rule.window)
        next_start = self._next_window_start(now_utc, rule.window)
        key = self._key(tenant, resource, window_start)
        used = self._store.get(key)
        return Decision(
            allowed=used < rule.limit,
            used=used,
            limit=rule.limit,
            remaining=rule.limit - used,
            reset_at=next_start,
        )

    def reserve(
        self,
        tenant: str,
        plan: str,
        resource: str,
        amount: int,
        *,
        ttl_s: float,
    ) -> Reservation | Decision:
        """Reserve capacity for a future operation.

        Args:
            ttl_s: Time-to-live in seconds from now.

        Returns:
            A :class:`Reservation` if successful, otherwise a :class:`Decision`
            with ``allowed=False``.
        """
        self._validate_tenant(tenant)
        self._validate_resource(resource)
        self._validate_amount(amount, allow_zero=False)
        if not isinstance(ttl_s, (int, float)) or ttl_s <= 0:
            raise QuotaError(
                "invalid_amount"
            )  # treat as invalid amount? spec says ttl_s float; we'll validate
        now_utc = self._now_utc()
        self._sweep_expired(tenant, resource, now_utc)
        rule = self._resolve_rule(tenant, plan, resource)
        # Check reservation count
        if len(self._reservations) >= self._max_reservations:
            raise QuotaError("too_many_reservations")
        window_start = self._window_start(now_utc, rule.window)
        next_start = self._next_window_start(now_utc, rule.window)
        key = self._key(tenant, resource, window_start)
        current = self._store.get(key)
        added, value_after = self._store.add_if_within(key, amount, rule.limit, next_start)
        if added:
            reservation_id = self._id_factory()
            expires_at = now_utc + timedelta(seconds=ttl_s)
            entry = _ReservationEntry(
                id=reservation_id,
                tenant=tenant,
                resource=resource,
                amount=amount,
                expires_at=expires_at,
                window_start=window_start,
                key=key,
                plan=plan,
            )
            self._reservations[reservation_id] = entry
            # Soft limit check
            if rule.soft_ratio is not None:
                threshold = ceil(rule.soft_ratio * rule.limit)
                soft_key = (tenant, resource, key)
                if current < threshold <= value_after and soft_key not in self._soft_events_fired:
                    self._soft_events_fired.add(soft_key)
                    if self._on_event is not None:
                        self._on_event(
                            {
                                "event": "soft_limit",
                                "tenant": tenant,
                                "resource": resource,
                                "used": value_after,
                                "limit": rule.limit,
                            }
                        )
            return Reservation(
                id=reservation_id,
                tenant=tenant,
                resource=resource,
                amount=amount,
                expires_at=expires_at,
            )
        else:
            return Decision(
                allowed=False,
                used=current,
                limit=rule.limit,
                remaining=rule.limit - current,
                reset_at=next_start,
            )

    def commit(self, reservation_id: str, actual: int) -> Decision:
        """Commit a reservation with the actual amount used.

        The reserved amount is adjusted to ``actual``; excess capacity is
        released back to the store.
        """
        if not isinstance(reservation_id, str):
            raise QuotaError("unknown_reservation")
        if isinstance(actual, bool) or not isinstance(actual, int):
            raise QuotaError("invalid_amount")
        # Get reservation entry
        entry = self._reservations.get(reservation_id)
        if entry is None:
            raise QuotaError("unknown_reservation")
        # Sweep for that tenant/resource in case it expired but not swept
        now_utc = self._now_utc()
        self._sweep_expired(entry.tenant, entry.resource, now_utc)
        entry = self._reservations.get(reservation_id)
        if entry is None:
            raise QuotaError("unknown_reservation")
        if actual < 0 or actual > entry.amount:
            raise QuotaError("invalid_amount")
        # Adjust store if reservation window is current
        rule = self._resolve_rule(entry.tenant, entry.plan, entry.resource)
        current_window_start = self._window_start(now_utc, rule.window)
        if entry.window_start == current_window_start:
            adjust = entry.amount - actual
            if adjust > 0:
                self._store.subtract(entry.key, adjust)
        # Remove reservation
        del self._reservations[reservation_id]
        # Return current decision
        current_key = self._key(entry.tenant, entry.resource, current_window_start)
        used = self._store.get(current_key)
        next_start = self._next_window_start(now_utc, rule.window)
        return Decision(
            allowed=True,
            used=used,
            limit=rule.limit,
            remaining=rule.limit - used,
            reset_at=next_start,
        )

    def cancel(self, reservation_id: str) -> None:
        """Cancel a reservation and release its full capacity."""
        if not isinstance(reservation_id, str):
            raise QuotaError("unknown_reservation")
        entry = self._reservations.get(reservation_id)
        if entry is None:
            raise QuotaError("unknown_reservation")
        now_utc = self._now_utc()
        self._sweep_expired(entry.tenant, entry.resource, now_utc)
        entry = self._reservations.get(reservation_id)
        if entry is None:
            raise QuotaError("unknown_reservation")
        # Adjust store if reservation window is current
        rule = self._resolve_rule(entry.tenant, entry.plan, entry.resource)
        current_window_start = self._window_start(now_utc, rule.window)
        if entry.window_start == current_window_start:
            self._store.subtract(entry.key, entry.amount)
        del self._reservations[reservation_id]


@dataclass(frozen=True)
class _ReservationEntry:
    id: str
    tenant: str
    resource: str
    amount: int
    expires_at: datetime
    window_start: datetime
    key: str
    plan: str
