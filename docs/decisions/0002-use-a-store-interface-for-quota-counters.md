# Use a store interface for quota counters

Status: accepted (generated, pending human review)

## Context

Enforcing per-tenant quotas requires atomic increments and resets across calendar windows. Different deployment environments may need different storage backends (in-memory, Redis, SQL).

## Decision

Define a `CounterStore` protocol with `add_if_within`, `subtract`, and `get` methods. Provide a thread-safe `InMemoryCounterStore` as the default implementation. The `QuotaManager` relies only on the protocol, enabling custom stores for production.

## Consequences

The core quota logic is decoupled from storage implementation. Users can implement custom stores for distributed systems. The in-memory store is suitable for single-process applications or testing.
