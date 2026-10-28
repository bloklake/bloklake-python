import os
import random
import re
import time
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, TypeVar, cast

import requests

from . import __version__
from .decode import decode_arrow
from .errors import BloklakeError
from .fields import Block, Log, Transaction
from .types import Account, ChainStatus, Page, PageMeta, Prepared, Query, Schema, SetCreated, SetInfo


T = TypeVar("T")


def _camel(name: str) -> str:
    """``block_number`` -> ``blockNumber``. Every field's request name is its column name in
    camelCase (scripts/generate.py checks this), and so is every snake_case request key."""
    return re.sub(r"_([a-z0-9])", lambda m: m.group(1).upper(), name)


def _camel_keys(d: Mapping[str, Any], where: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, v in d.items():
        key = _camel(k)
        if key in out:
            raise ValueError(f"{where}: {key!r} is given twice (camelCase and snake_case); give it one way")
        out[key] = v
    return out


def _camel_query(q: Mapping[str, Any]) -> Dict[str, Any]:
    """The query with snake_case keys and field names (``from_block``, ``block_number``) in the
    API's camelCase. camelCase input passes through unchanged."""
    out = _camel_keys(q, "query")
    for k in ("logs", "transactions"):
        if k in out:
            out[k] = [_camel_keys(f, k) for f in out[k]]
    if "fields" in out:
        out["fields"] = {rt: [_camel(n) for n in names] for rt, names in out["fields"].items()}
    return out


def _wire_body(q: Mapping[str, Any]) -> Dict[str, Any]:
    """Turn typed field lists into the API's ``{name: true}`` objects."""
    body = {k: v for k, v in q.items() if k != "fields"}
    if "fields" in q:
        body["fields"] = {rt: {n: True for n in names} for rt, names in q["fields"].items() if names}
    return body


def _page_meta(m: Mapping[str, str]) -> PageMeta:
    def num(k: str) -> Optional[int]:
        v = m.get(k)
        return int(v) if v not in (None, "") else None

    def req(k: str) -> int:
        return int(m[k])

    return PageMeta(
        from_block=req("bl.from_block"), to_block=req("bl.to_block"), next_block=req("bl.next_block"),
        objects=req("bl.objects"), archive_height=req("bl.archive_height"),
        finalized_block=num("bl.finalized_block"), to_block_hash=m.get("bl.to_block_hash") or None,
        query_id=m.get("bl.query_id_template", ""),
        unavailable_fields=[f for f in m.get("bl.unavailable_fields", "").split(",") if f],
    )


class Bloklake:
    """A Bloklake client for one chain. Queries travel as Apache Arrow and are decoded here
    into plain JSON-like rows (the same values ``/query/json`` would give).

    Queries use the API's camelCase names or the same names in snake_case; rows always come
    back in snake_case:

    >>> bl = Bloklake(chain="eth")                      # key from BLOKLAKE_API_KEY
    >>> for page in bl.paginate({"from_block": 19_000_000, "to_block": 19_100_000,
    ...         "logs": [{"address": [USDC], "topic0": [TRANSFER]}],
    ...         "fields": {"log": ["block_number", "topic1", "topic2", "data"]}}):
    ...     for log in page.logs:
    ...         print(log["block_number"], log["data"])
    """

    def __init__(self, api_key: Optional[str] = None, chain: Optional[str] = None, base_url: Optional[str] = None,
                 max_retries: int = 4, timeout: float = 120.0, session: Optional[requests.Session] = None) -> None:
        """
        :param api_key: ``bl_live_…``; defaults to the ``BLOKLAKE_API_KEY`` environment variable.
        :param chain: chain name in URLs (``"eth"``, ``"rh"``); ignored when ``base_url`` is set.
        :param base_url: full base URL of one chain, e.g. ``https://api.bloklake.com/v1/chains/eth``.
        :param max_retries: retries for rate limits (429), busy servers (503), 5xx and broken connections.
        :param timeout: seconds to wait for the server to respond and between bytes of a response.
        """
        if not base_url and not chain:
            raise ValueError('Bloklake: pass chain ("eth", "rh") or base_url')
        self.base_url = (base_url or f"https://api.bloklake.com/v1/chains/{chain}").rstrip("/")
        self.max_retries = max_retries
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = f"bloklake-python/{__version__}"
        key = api_key if api_key is not None else os.environ.get("BLOKLAKE_API_KEY")
        if key:
            self.session.headers["Authorization"] = f"Bearer {key}"

    # ---- queries ---------------------------------------------------------------------------

    def query(self, q: Query) -> Page:
        """Run a query and return one page. Read ``page.meta.next_block`` to continue, or use
        :meth:`paginate`."""
        return self._query_body(_wire_body(_camel_query(q)))

    def paginate(self, q: Query, use_prepared_query: bool = True) -> Iterator[Page]:
        """Every page of a query, following ``next_block`` until ``toBlock`` (or the chain
        head when ``toBlock`` is omitted). After the first page only the prepared-query id is
        sent; if it expired, the full filter is sent again."""
        q = _camel_query(q)  # type: ignore[assignment]  # the same Query, in camelCase
        full = _wire_body(q)
        frm = q.get("fromBlock", 0)
        query_id: Optional[str] = None
        while True:
            rng = {"fromBlock": frm}
            for k in ("toBlock", "maxObjects", "strictFields"):
                if k in q:
                    rng[k] = q[k]  # type: ignore[literal-required,unused-ignore]
            body = {"queryId": query_id, **rng} if (query_id and use_prepared_query) else {**full, **rng}
            try:
                page = self._query_body(body)
            except BloklakeError as e:
                if e.code == "unknown_query_id" and query_id:
                    query_id = None  # expired, or a server that never saw it: send the filter again
                    continue
                raise
            query_id = page.meta.query_id or None
            yield page
            end = min(q["toBlock"], page.meta.archive_height) if "toBlock" in q else page.meta.archive_height
            if page.meta.next_block > end:
                return
            frm = page.meta.next_block

    def collect(self, q: Query) -> Page:
        """All pages of a query merged into one. Mind memory for large ranges; prefer
        :meth:`paginate`."""
        merged: Optional[Page] = None
        for page in self.paginate(q):
            if merged is None:
                merged = page
                continue
            merged.logs.extend(page.logs)
            merged.transactions.extend(page.transactions)
            merged.blocks.extend(page.blocks)
            first, objects = merged.meta.from_block, merged.meta.objects + page.meta.objects
            merged.meta = page.meta
            merged.meta.from_block, merged.meta.objects = first, objects
        assert merged is not None
        return merged

    def _query_body(self, body: Dict[str, Any]) -> Page:
        def run() -> Page:
            resp = self._send("POST", "/query", body, stream=True)
            try:
                resp.raw.decode_content = True
                rows, meta = decode_arrow(resp.raw)
            finally:
                resp.close()
            return Page(logs=cast(List[Log], rows.get("logs", [])),
                        transactions=cast(List[Transaction], rows.get("transactions", [])),
                        blocks=cast(List[Block], rows.get("blocks", [])), meta=_page_meta(meta))

        return self._with_retries(run)

    # ---- sets and prepared queries -----------------------------------------------------------

    def create_set(self, kind: str, values: Sequence[str], based_on: Optional[str] = None) -> SetCreated:
        """Store addresses (``kind="address"``, 20 bytes) or topics (``"topic"``, 32 bytes).
        Idempotent: the same values always give the same id."""
        body: Dict[str, Any] = {"kind": kind, "values": list(values)}
        if based_on:
            body["basedOn"] = based_on
        j = self._json("POST", "/v1/sets", body)
        return SetCreated(set_id=j["set_id"], size=j["size"], created=j["created"])

    def get_set(self, set_id: str) -> SetInfo:
        j = self._json("GET", f"/v1/sets/{set_id}")
        return SetInfo(set_id=j["set_id"], kind=j["kind"], size=j["size"], created_at=j["created_at"],
                       last_used_at=j["last_used_at"])

    def delete_set(self, set_id: str) -> None:
        self._with_retries(lambda: self._send("DELETE", f"/v1/sets/{set_id}").close())

    def prepare(self, q: Mapping[str, Any]) -> Prepared:
        """Register a query's filter and get its id without running it."""
        j = self._json("POST", "/v1/queries", _wire_body(_camel_query(q)))
        return Prepared(query_id=j["query_id"], expires_in=j["expires_in"])

    # ---- chain and account -------------------------------------------------------------------

    def status(self) -> ChainStatus:
        """The block range served and the finalized block. Free."""
        j = self._json("GET", "/status")
        return ChainStatus(chain_id=j["chain_id"], first_block=j.get("first_block"),
                           archive_height=j.get("archive_height"), finalized_block=j.get("finalized_block"))

    def schema(self) -> Schema:
        """Every field and whether this chain has it. Free."""
        return cast(Schema, self._json("GET", "/v1/schema"))

    def account(self) -> Account:
        """The key's plan, limits and balance or usage. Free and not rate limited."""
        j = self._json("GET", "/v1/account")
        return Account(**{k: j.get(k) for k in Account.__dataclass_fields__})

    # ---- transport ---------------------------------------------------------------------------

    def _json(self, method: str, path: str, body: Any = None) -> Any:
        def run() -> Any:
            resp = self._send(method, path, body)
            try:
                return resp.json()
            finally:
                resp.close()

        return self._with_retries(run)

    def _send(self, method: str, path: str, body: Any = None, stream: bool = False) -> requests.Response:
        try:
            resp = self.session.request(method, self.base_url + path, json=body, stream=stream, timeout=(10, self.timeout))
        except requests.Timeout as e:
            raise BloklakeError(0, "timeout", str(e)) from e
        except requests.RequestException as e:
            raise BloklakeError(0, "network", str(e)) from e
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            ra = resp.headers.get("Retry-After")
            resp.close()
            raise BloklakeError(resp.status_code, err.get("code", "http_error"), err.get("message", resp.reason or ""),
                                float(ra) if ra else None, resp.headers.get("X-Request-Id"))
        return resp

    def _with_retries(self, fn: Callable[[], T]) -> T:
        attempt = 0
        while True:
            try:
                return fn()
            except BloklakeError as e:
                if not e.retryable or attempt >= self.max_retries:
                    raise
                wait = e.retry_after if e.retry_after is not None else min(2 ** attempt, 30) * (0.5 + random.random() / 2)
                time.sleep(wait)
                attempt += 1
