# Project record

## Objective

Enforce per-tenant usage quotas over fixed UTC calendar windows: plan limits with per-tenant overrides, atomic check-and-consume through a `CounterStore` port, reservations for long-running operations, soft-limit events and exact reset times.

## Scope

In scope:

- The public API exported by `tenant_quota`: `QuotaRule`, `Decision`, `Reservation`, `CounterStore`, `InMemoryCounterStore`, `QuotaManager` and `QuotaError`.
- Fixed UTC calendar windows (`minute`, `hour`, `day`, `month`), with `reset_at` equal to the next window start.
- Plan rules and per-tenant overrides; rule resolution checks `overrides[tenant][resource]` before `plans[plan][resource]`.
- Atomic add-if-within check-and-consume through the `CounterStore` protocol and the thread-safe in-memory implementation.
- Reservations with `ttl_s`, lazy expiry, `commit(reservation_id, actual)` and `cancel(reservation_id)`; committed, cancelled and expired reservations are terminal.
- Soft-limit events once per `(tenant, resource, window)` when usage crosses `ceil(soft_ratio * limit)`.
- Input validation and the `QuotaError` codes documented in the README.

Out of scope:

- Request-per-second rate limiting and token buckets.
- Billing and overage pricing.
- Redis or SQL stores; callers may implement `CounterStore`, but no such implementation ships in this package.
- Rolling windows.

## Timeline

| Date | Event |
|---|---|
| 2026 | Repository created from the factory utility template. |

## Effort metrics

| Metric | Value |
|---|---|
| Tokens (input / output) | 679361 / 66957 |
| Hours (human) | pending measurement |
| Cost (USD) | 0.0933 |

## Factory run u244 (measured from API usage)

| Date (UTC) | Phase | Model | Batch | Input | Output | Cache write | Cache read | Cost (USD) |
|---|---|---|---|---|---|---|---|---|
| 2026-11-10 | generation | deepseek-v4-pro | yes | 1840 | 20789 | 0 | 3328 | 0.0425 |
| 2026-11-10 | fix-1 | deepseek-flash | no | 19631 | 10989 | 0 | 205568 | 0.0102 |
| 2026-11-10 | audit | deepseek-flash | no | 24002 | 19126 | 0 | 288640 | 0.0159 |
| 2026-11-10 | fix-2 | deepseek-flash | no | 12136 | 2467 | 0 | 35712 | 0.0034 |
| 2026-11-10 | docs | deepseek-flash | no | 88248 | 13586 | 0 | 256 | 0.0214 |

Total: $0.0933. Prices: deepseek line, as measured by its adapter. Providers serve the current version of each model id, so the date tells which version answered; versions, launch stages and price sources per line are in the factory's model register (docs/MODELS.md).
