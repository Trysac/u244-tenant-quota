# Security notes

## Threat model

The library is a process-local quota calculator with an in-memory default store. It does not authenticate callers, authorize tenants, persist counters, or provide cross-process atomicity by itself.

Defended:

- Tenant identifiers are validated against `[A-Za-z0-9._-]{1,128}`; invalid values raise `QuotaError('invalid_tenant')`.
- Resource names are validated against `[a-z][a-z0-9_]{0,63}`; invalid values raise `QuotaError('unknown_resource')`.
- Amounts are integers and not `bool`; `consume` and `reserve` require `amount > 0`, while `commit` requires `0 <= actual <= reserved`; violations raise `QuotaError('invalid_amount')`.
- `QuotaRule` rejects a non-int or negative `limit`, an unknown window, and a `soft_ratio` outside `(0, 1)` with `QuotaError('invalid_rule')`.
- A clock that returns a naive datetime raises `QuotaError('invalid_clock')`; aware datetimes are converted to UTC before window arithmetic.
- `InMemoryCounterStore` uses a `threading.Lock` for `add_if_within`, `subtract` and `get`; it purges expired counters before enforcing `max_counters`, then raises `QuotaError('store_full')`.
- `QuotaManager` enforces `max_reservations` (`too_many_reservations`) and rejects unknown, already committed, cancelled or expired reservation ids (`unknown_reservation`).
- Expired reservations are released lazily at the start of a call for the same tenant and resource, and a `reservation_expired` event is emitted.
- Events carry only the event name, tenant, resource and numeric amount/used/limit fields. The library logs through `logging.getLogger(__name__)` with a `NullHandler`.

Not defended:

- No authentication, authorization or tenant isolation beyond the caller-supplied `tenant` string and the per-tenant keys.
- No persistent storage. The default `InMemoryCounterStore` is process-local and its state is lost on restart.
- No distributed atomicity. A custom `CounterStore` must make `add_if_within` and `subtract` atomic for its backend.
- No protection against a malicious `clock`, `id_factory` or `on_event` callback supplied by the caller.
- No rate limiting, token buckets, billing, overage pricing or rolling windows.
- No defence against a caller that deliberately shares a store between unrelated trust domains; the store key is `<tenant>:<resource>:<window start ISO 8601>`.
- No secrets are handled by the library. Deployment secrets, network controls and database security are the caller's responsibility.

Real limits in the code: tenant pattern `[A-Za-z0-9._-]{1,128}`; resource pattern `[a-z][a-z0-9_]{0,63}`; default `max_reservations=10_000`; default `max_counters=100_000`; `soft_ratio` in `(0, 1)`; `ttl_s > 0`; reservation ids are strings produced by `id_factory` (default `uuid.uuid4().hex`).

## Publishing

The package has no runtime dependencies and performs no network or filesystem I/O during import or quota operations. Release credentials and repository publishing are outside the library code.

To report a vulnerability, contact the repository maintainers privately and do not include secrets in public issues.
