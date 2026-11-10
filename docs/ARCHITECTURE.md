# Architecture

## Purpose

`tenant_quota` is a single-module Python package that enforces per-tenant usage quotas over fixed UTC calendar windows. It provides plan rules with per-tenant overrides, atomic check-and-consume through a `CounterStore` port, reservations for long-running operations, soft-limit events and exact reset times.

## Shape

```text
caller code
  |
  v
src/tenant_quota/__init__.py
  - QuotaError
  - QuotaRule, Decision, Reservation (frozen dataclasses)
  - CounterStore (runtime-checkable Protocol)
  - InMemoryCounterStore (threading.Lock)
  - QuotaManager (rules, windows, reservations, events)
```

- The public API is exactly the names in `__all__`: `CounterStore`, `Decision`, `InMemoryCounterStore`, `QuotaError`, `QuotaManager`, `QuotaRule`, `Reservation`.
- Errors are raised as `QuotaError(code)`; `code` is one of `invalid_amount`, `invalid_tenant`, `unknown_plan`, `unknown_resource`, `unknown_reservation`, `too_many_reservations`, `store_full`, `invalid_clock`, `invalid_rule`.
- `QuotaManager` stores counters through the `CounterStore` protocol. The default `InMemoryCounterStore` keeps a dictionary guarded by `threading.Lock`, purges expired keys and enforces `max_counters`.
- Reservations are held in `QuotaManager` memory. `commit` and `cancel` are terminal; expired reservations are swept lazily for the same tenant and resource.
- Side effects are limited to calls to the injected `on_event` callback and `logging.getLogger(__name__)` with a `NullHandler`. The package does no network or filesystem I/O.

## Quality contract

`src/tenant_quota` is fully type-annotated and ships `src/tenant_quota/py.typed`. The only runtime dependency is the Python standard library. Tests live in `tests/test_tenant_quota.py` and use the public import `tenant_quota`.

## Decisions

The code uses fixed UTC calendar windows, not rolling windows. Rule resolution is override-first. Reservations belong to the window in which they were made; when that window has ended, `commit` and `cancel` release nothing from the counter. These choices are visible in `QuotaManager._resolve_rule`, `_window_start`, `_next_window_start`, `commit` and `cancel`.
