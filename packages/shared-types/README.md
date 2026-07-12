# packages/shared-types

Lightweight, cross-app schemas and utilities shared by the `dss-system` monorepo.

## What's inside

- `coord_schema.py` — `Coordinate`/`LedgerEntrySchema` identifiers, coordinate formatting/payload normalisation, and **BigInt-safe coordinate encoding helpers**.
- `did_models.py` — DID document and principal models.
- `openrouter_client.py` — OpenRouter client wrapper and response normaliser.

## BigInt-safe coordinate encoding

Prime-lattice coordinates can involve products of hundreds of distinct primes, producing integers far beyond `Number.MAX_SAFE_INTEGER` (`2^53 - 1`) in JavaScript and beyond many SQL numeric types. The shared helpers keep Python's arbitrary-precision `int` internally while emitting decimal strings on the wire.

```python
from shared_types.coord_schema import (
    bigint_str,
    parse_bigint,
    sanitize_coordinate_metadata,
    normalize_coordinate_metadata,
)

# On the producer side: stringify coordinate scalars before json.dumps().
payload = {"token_prime_product": 10**500}
safe_payload = sanitize_coordinate_metadata(payload)
# safe_payload == {"token_prime_product": "1" + "0" * 500}

# On the consumer side: parse back to int.
restored = normalize_coordinate_metadata(json.loads(json.dumps(safe_payload)))
# restored["token_prime_product"] == 10**500
```

### Known BigInt coordinate keys

These fields are always emitted as strings by `sanitize_coordinate_metadata`:

- `prime_multiplicative_value`
- `token_prime_product`
- `body_prime`
- `numerator`
- `denominator`

Any other integer value with an absolute value greater than `2^53 - 1` is also stringified as a defensive fallback.

## Vendored copies

`apps/chat-surface` and `apps/control-plane` each vendor this package under `vendor/shared-types` because Vercel does not share the monorepo root at build time. After editing files in this package, copy the changed files to both vendor directories before deploying.
