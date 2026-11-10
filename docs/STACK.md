# Stack

## Used

| Tool | Why |
|---|---|
| Python >= 3.10 | The code uses `X | None` unions and `from __future__ import annotations`; no runtime dependencies are allowed. |
| Standard library | `logging`, `re`, `threading`, `uuid`, `collections.abc`, `dataclasses`, `datetime`, `math` and `typing` implement the package. |
| `py.typed` | `src/tenant_quota/py.typed` marks the package as typed. |
| pytest | `tests/test_tenant_quota.py` is a pytest module using fixtures and `pytest.raises`. |

## Discarded

| Tool | Why not |
|---|---|
| Runtime dependencies | The package is standard-library only. |
| Redis or SQL store | Out of scope; callers can implement the `CounterStore` protocol. |
| Rate limiter / token bucket | Out of scope; quotas use fixed calendar windows, not per-second limiting. |
| Rolling windows | Out of scope; windows are fixed UTC calendar windows. |
