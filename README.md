# bloklake

The official Python client for the [Bloklake](https://docs.bloklake.com) API.

- **Typed queries**: filters, field names and rows are `TypedDict`s and `Literal`s, so your
  editor and mypy catch a misspelled field.
- **Arrow on the wire, JSON-like rows in your code**: responses travel as Apache Arrow (about
  2–2.5× smaller than JSON, so cheaper) and are decoded here into plain dicts with the same
  values `/query/json` returns.
- **Pagination, retries and integrity built in**: follows `next_block`, reuses prepared
  queries, retries rate limits and busy servers, and refuses truncated responses.

```bash
pip install bloklake
```

Python 3.9+. Depends on `pyarrow` and `requests`.

## Quick start

```python
from bloklake import Bloklake

bl = Bloklake(chain="eth")  # API key from BLOKLAKE_API_KEY

page = bl.query({
    "from_block": 19_426_000,
    "to_block": 19_426_009,
    "logs": [{
        "address": ["0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"],
        "topic0": ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"],
    }],
    "fields": {"log": ["block_number", "transaction_hash", "topic1", "topic2", "data"]},
})

for log in page.logs:
    print(log["block_number"], log["data"])
print(page.meta.next_block)
```

Write the query in snake_case, as above, or in the API's camelCase (`"fromBlock"`,
`"blockNumber"`, as in the HTTP API and curl examples); the client sends camelCase either way.
Give each key one way only. Rows always come back in snake_case (`log["block_number"]`).

## Reading a whole range

`paginate` follows `next_block` until `to_block` (or the chain head when you leave `to_block`
out). After the first page it sends only the prepared-query id, and falls back to the full
query if the id expired.

```python
for page in bl.paginate({
    "from_block": 19_000_000,
    "to_block": 19_100_000,
    "logs": [{"topic0": [TRANSFER]}],
    "fields": {"log": ["block_number", "address", "topic1", "topic2", "data"]},
}):
    store(page.logs)
```

`collect(query)` returns all pages merged into one `Page`. Use it for ranges that fit in memory.

## Results

A `Page` has `logs`, `transactions` and `blocks` lists (empty when not requested) and `meta`
(`from_block`, `to_block`, `next_block`, `objects`, `archive_height`, `finalized_block`,
`to_block_hash`, `query_id`, `unavailable_fields`).

Rows use the API's column names (`block_number`, `transaction_hash`) and its JSON encoding:

| Field kind | Value |
|---|---|
| block numbers, indexes, gas, timestamps | `int` |
| amounts (`value`, gas prices, fees, uint256) | decimal `str` (`int(x)` to compute) |
| hashes, addresses, topics, `input`, `data` | `0x` hex `str` |
| `withdrawals`, `access_list`, `authorization_list` | lists of dicts |
| not available or not applicable | `None` |

To get a dataframe: `pandas.DataFrame(page.logs)` or `polars.DataFrame(page.logs)`. If you'd
rather keep the raw Arrow columns, use the API directly as shown in
[Reading Arrow](https://docs.bloklake.com/responses/reading-arrow).

## Other calls

```python
s = bl.create_set("address", pools)                    # idempotent: same values, same id
bl.query({"logs": [{"address_set": s.set_id, "topic0": [SWAP]}], "fields": {"log": ["address", "data"]}})
bl.get_set(s.set_id)
bl.delete_set(s.set_id)

bl.prepare({"logs": [...], "fields": {...}})  # Prepared(query_id, expires_in)
bl.status()   # ChainStatus(chain_id, first_block, archive_height, finalized_block), free
bl.schema()   # every field and whether this chain has it, free
bl.account()  # plan, rps, balance or usage, free
```

## Errors

Failures raise `BloklakeError` with `status` (0 for client-side failures), `code`
(`invalid_query`, `rate_limited`, `balance_exhausted`, `truncated`, …), `retry_after` and
`request_id`. The client retries `429`, `503`, `5xx`, network errors and truncated responses
itself, up to `max_retries` (default 4), honoring `Retry-After`. Other errors need a fix and are
raised at once. See [Errors](https://docs.bloklake.com/api-reference/errors).

```python
from bloklake import BloklakeError

try:
    bl.query(q)
except BloklakeError as e:
    if e.code == "balance_exhausted":
        alert_someone()
    else:
        raise
```

## Options

```python
Bloklake(
    api_key="bl_live_…",    # default: BLOKLAKE_API_KEY
    chain="rh",             # or base_url="https://api.bloklake.com/v1/chains/rh"
    max_retries=4,
    timeout=120.0,
    session=requests.Session(),  # proxies, custom adapters, tests
)
```

## Development

```bash
pip install -e ".[dev]"
pytest              # unit and fixture tests
mypy src/bloklake   # strict
BLOKLAKE_TEST_URL=https://api.bloklake.com/v1/chains/eth BLOKLAKE_API_KEY=… pytest   # plus live tests
python scripts/generate.py   # regenerate src/bloklake/fields.py after updating scripts/schema.json
```

The fixture tests decode recorded Arrow responses and compare them with the server's own
`/query/json` output for the same requests, field by field.

## License

MIT
