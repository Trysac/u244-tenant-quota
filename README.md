# trysac-tenant-quota

[![PyPI version](https://img.shields.io/pypi/v/trysac-tenant-quota)](https://pypi.org/project/trysac-tenant-quota/)
[![Python versions](https://img.shields.io/pypi/pyversions/trysac-tenant-quota)](https://pypi.org/project/trysac-tenant-quota/)
[![CI](https://github.com/Trysac/u244-tenant-quota/actions/workflows/ci.yml/badge.svg)](https://github.com/Trysac/u244-tenant-quota/actions)

Per-tenant usage quotas over **UTC calendar windows** for SaaS backends.

- **Plan limits** with per-tenant overrides
- **Atomic check-and-consume** through a `CounterStore` port
- **Reservations** for long-running operations, with lazy expiry and explicit commit/cancel
- **Soft-limit events** fired once per tenant, resource and window when usage crosses `ceil(soft_ratio * limit)`
- **Exact reset times** returned as timezone-aware `reset_at` datetimes

## Installation

```bash
pip install trysac-tenant-quota
```

The import name is `tenant_quota`. The package uses only the Python standard library and declares no runtime dependencies.

## Quick start

```python
from datetime import datetime, timezone
from tenant_quota import QuotaManager, QuotaRule

plans = {"free": {"api_calls": QuotaRule(limit=1000, window="day")}}
clock = lambda: datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
manager = QuotaManager(plans, clock=clock)
decision = manager.consume("tenant-1", "free", "api_calls", 999)
print(decision.allowed, decision.remaining)  # True 1
```

## Public API

`tenant_quota.__all__` exports `CounterStore`, `Decision`, `InMemoryCounterStore`, `QuotaError`, `QuotaManager`, `QuotaRule` and `Reservation`.

- `QuotaRule(limit: int, window: Literal['minute', 'hour', 'day', 'month'], soft_ratio: float | None = None)` is frozen. `limit` must be an int >= 0 (not bool), `window` must be one of the four literals, and `soft_ratio` must be in `(0, 1)`.
- `Decision(allowed, used, limit, remaining, reset_at)` is returned by `consume`, `peek` and failed `reserve` calls. `reset_at` is the next UTC calendar window start.
- `Reservation(id, tenant, resource, amount, expires_at)` is returned by a successful `reserve`.
- `CounterStore` is a `Protocol` with `add_if_within(key, amount, limit, expires_at) -> tuple[bool, int]`, `subtract(key, amount) -> None` (never below zero and ignores missing keys) and `get(key) -> int`.
- `InMemoryCounterStore(*, max_counters: int = 100_000, clock: Callable[[], datetime])` implements `CounterStore` with a `threading.Lock`; it purges expired counters before refusing a new key with `QuotaError('store_full')`.
- `QuotaManager(plans, *, overrides=None, store=None, clock=..., id_factory=..., max_reservations=10_000, on_event=None)` exposes `consume(tenant, plan, resource, amount=1)`, `peek(tenant, plan, resource)`, `reserve(tenant, plan, resource, amount, *, ttl_s)`, `commit(reservation_id, actual)` and `cancel(reservation_id)`.

Rules are resolved as `overrides[tenant][resource]`, then `plans[plan][resource]`. Tenant ids match `[A-Za-z0-9._-]{1,128}` and resource names match `[a-z][a-z0-9_]{0,63}`. Windows are fixed UTC calendar windows; `minute` and `hour` truncate, `day` starts at 00:00 UTC and `month` starts on day 1 at 00:00 UTC. The clock must return timezone-aware datetimes and is converted to UTC.

`QuotaError.code` is one of `invalid_amount`, `invalid_tenant`, `unknown_plan`, `unknown_resource`, `unknown_reservation`, `too_many_reservations`, `store_full`, `invalid_clock`, `invalid_rule`. Examples: amount `0`, `-1` or `True` raises `invalid_amount`; tenant `a b` raises `invalid_tenant`; unknown plan raises `unknown_plan`; a second `commit` or `cancel` raises `unknown_reservation`; a third live counter in `InMemoryCounterStore(max_counters=2)` raises `store_full`.

Limits: `max_reservations` defaults to `10_000`; `max_counters` defaults to `100_000`; `commit` accepts `actual` from `0` to the reserved amount; `ttl_s` must be positive. Events contain only the event name, tenant, resource and numeric amount/used/limit fields. Logging uses `logging.getLogger(__name__)` with a `NullHandler`.

## Why not a rate limiter

A request rate limiter answers whether a request may run now against a moving time base. This library answers how much of a plan's calendar quota a tenant has used and returns the exact reset time. It also supports per-plan rules with tenant overrides, reservations for long jobs and lazy release of expired reservations. Request-per-second rate limiting, token buckets and rolling windows are out of scope.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Project record](docs/PROJECT.md)
- [Security](docs/SECURITY.md)
- [Testing](docs/TESTING.md)
- [Stack](docs/STACK.md)

## License

MIT
