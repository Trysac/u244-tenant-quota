# tenant-quota

[![PyPI version](https://img.shields.io/pypi/v/trysac-tenant-quota)](https://pypi.org/project/trysac-tenant-quota/)
[![License: MIT](https://img.shields.io/pypi/l/trysac-tenant-quota)](LICENSE)
[![CI](https://github.com/Trysac/u244-tenant-quota/actions/workflows/ci.yml/badge.svg)](https://github.com/Trysac/u244-tenant-quota/actions/workflows/ci.yml)

Per-tenant usage quotas over UTC calendar windows: plan limits with overrides, atomic check-and-consume through a store port, reservations, soft-limit events and reset times

## Problem

Describe in two or three sentences the concrete problem this utility solves, who hits it, and what
they do today without it. Replace this paragraph when the first real feature lands.

## Install

```sh
pip install trysac-tenant-quota
# or
uv add trysac-tenant-quota
```

Requires Python 3.10 or newer. No runtime dependencies.

## Usage

```python
import tenant_quota

# normalize() trims surrounding whitespace and lower-cases the text.
print(tenant_quota.normalize("  Hello World "))  # -> "hello world"

# Blank input is rejected explicitly instead of returning an empty string.
try:
    tenant_quota.normalize("   ")
except ValueError as exc:
    print(f"rejected: {exc}")
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) and [decisions](docs/decisions/)
- [Testing](docs/TESTING.md) and [security](docs/SECURITY.md)
- [Project record](docs/PROJECT.md) and [stack](docs/STACK.md)
- [Changelog](CHANGELOG.md) and [contributing guide](CONTRIBUTING.md)

## License

[MIT](LICENSE) (c) 2026 Miguel Gomez
