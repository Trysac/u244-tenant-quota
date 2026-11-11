# Testing

## Running

```sh
python -m pytest tests/test_tenant_quota.py
```

The repository contains one test module, `tests/test_tenant_quota.py`. It imports `tenant_quota` and uses a `fixed_clock` fixture plus a `manager` fixture that builds a `QuotaManager` with plans for `free` (`api_calls` day limit and `exports` month limit) and an override for tenant `t2`.

## Policy

Tests call the public API and assert observable results: returned `Decision` and `Reservation` values, emitted event dictionaries, and `QuotaError.code` values. They use the injected clock instead of wall time. No network access is required.

The visible test functions include `test_quota_rule_valid`, `test_quota_rule_invalid_limit`, `test_quota_rule_invalid_soft_ratio` and `test_quota_rule_invalid_window`. The same module contains further tests for manager decisions, reservations, soft-limit events, overrides, validation errors and `InMemoryCounterStore` capacity.

## Tiers

There is a single pytest tier in the repository. No conformance-marked tests, `tests/conftest.py`, or CI-only setup script are present in the provided tree.

## Layout

`tests/test_tenant_quota.py` is the only test file. It contains the fixtures and all tests for `src/tenant_quota/__init__.py`.
