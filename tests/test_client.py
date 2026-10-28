"""Client behavior with a fake HTTP session: request shape, errors, retries."""
import io
import json
from pathlib import Path

import pytest

from bloklake import Bloklake, BloklakeError

FIX = Path(__file__).parent / "fixtures"


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.raw = io.BytesIO(body)
        self.reason = "x"
        self._body = body

    def json(self):
        return json.loads(self._body)

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.calls = []
        self.responses = list(responses)

    def request(self, method, url, json=None, stream=False, timeout=None):
        self.calls.append({"method": method, "url": url, "json": json})
        return self.responses.pop(0)


def err(status, code, headers=None):
    return FakeResponse(status, json.dumps({"error": {"code": code, "message": code}}).encode(), headers)


def test_query_shape_and_rows():
    s = FakeSession([FakeResponse(body=(FIX / "eth-logs-only.arrows").read_bytes())])
    bl = Bloklake(api_key="bl_live_test", chain="eth", session=s)
    page = bl.query({"fromBlock": 19426000, "toBlock": 19426009, "logs": [{"address": ["0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"]}],
                     "fields": {"log": ["blockNumber", "logIndex", "data", "topics"]}})
    call = s.calls[0]
    assert call["url"] == "https://api.bloklake.com/v1/chains/eth/query"
    assert s.headers["Authorization"] == "Bearer bl_live_test"
    assert call["json"]["fields"] == {"log": {"blockNumber": True, "logIndex": True, "data": True, "topics": True}}
    assert len(page.logs) == 85 and page.transactions == [] and page.blocks == []
    assert page.meta.next_block == 19426010 and page.meta.objects == 85
    assert isinstance(page.logs[0]["block_number"], int)


def test_api_error_not_retried():
    s = FakeSession([err(400, "invalid_query", {"X-Request-Id": "req-1"})])
    with pytest.raises(BloklakeError) as e:
        Bloklake(base_url="http://x", session=s).query({"fields": {"block": ["number"]}})
    assert (e.value.status, e.value.code, e.value.request_id, e.value.retryable) == (400, "invalid_query", "req-1", False)
    assert len(s.calls) == 1


def test_rate_limit_and_busy_are_retried():
    s = FakeSession([err(429, "rate_limited", {"Retry-After": "0"}), err(503, "unavailable", {"Retry-After": "0"}),
                     FakeResponse(body=(FIX / "eth-empty.arrows").read_bytes())])
    page = Bloklake(base_url="http://x", session=s).query({"logs": [{}], "fields": {"log": ["blockNumber"]}})
    assert len(s.calls) == 3 and page.logs == []


def test_truncated_body_is_retried():
    full = (FIX / "eth-logs-only.arrows").read_bytes()
    s = FakeSession([FakeResponse(body=full[:3000]), FakeResponse(body=full)])
    bl = Bloklake(base_url="http://x", session=s, max_retries=1)
    bl._with_retries.__func__  # noqa: B018 (exists)
    import bloklake.client as c
    sleep, c.time.sleep = c.time.sleep, lambda s: None
    try:
        page = bl.query({"logs": [{}], "fields": {"log": ["blockNumber"]}})
    finally:
        c.time.sleep = sleep
    assert len(s.calls) == 2 and len(page.logs) == 85


def test_status_and_sets():
    j = lambda o: FakeResponse(body=json.dumps(o).encode())  # noqa: E731
    s = FakeSession([j({"chain_id": 1, "first_block": 0, "archive_height": 100, "finalized_block": 90}),
                     j({"set_id": "0xab", "size": 2, "created": True}), FakeResponse(204)])
    bl = Bloklake(base_url="http://x", session=s)
    st = bl.status()
    assert (st.chain_id, st.first_block, st.archive_height, st.finalized_block) == (1, 0, 100, 90)
    created = bl.create_set("address", ["0x01"], based_on="0xaa")
    assert (created.set_id, created.size, created.created) == ("0xab", 2, True)
    assert s.calls[1]["json"] == {"kind": "address", "values": ["0x01"], "basedOn": "0xaa"}
    bl.delete_set("0xab")
    assert (s.calls[2]["method"], s.calls[2]["url"]) == ("DELETE", "http://x/v1/sets/0xab")


def test_needs_chain_or_base_url():
    with pytest.raises(ValueError):
        Bloklake()


def test_paginate_sends_strict_fields_with_every_page():
    body = (FIX / "eth-logs-only.arrows").read_bytes()
    s = FakeSession([FakeResponse(body=body), FakeResponse(body=body)])
    pages = Bloklake(base_url="http://x", session=s).paginate(
        {"fromBlock": 19426000, "toBlock": 19426019, "strictFields": True, "logs": [{}], "fields": {"log": ["blockNumber"]}})
    next(pages), next(pages)
    first, second = s.calls[0]["json"], s.calls[1]["json"]
    assert first["strictFields"] is True and "fields" in first
    assert second["queryId"].startswith("0x") and "fields" not in second
    assert second["strictFields"] is True


def test_snake_case_request_is_sent_in_camel_case():
    camel = {"fromBlock": 19426000, "toBlock": 19426009, "joinTransactions": True,
             "logs": [{"address": ["0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"], "topic0Set": "0x01"}],
             "transactions": [{"fromSet": "0x02", "status": 1}],
             "fields": {"log": ["blockNumber", "transactionHash", "topics"], "transaction": ["gasUsed", "hash"]}}
    snake = {"from_block": 19426000, "to_block": 19426009, "join_transactions": True,
             "logs": [{"address": ["0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"], "topic0_set": "0x01"}],
             "transactions": [{"from_set": "0x02", "status": 1}],
             "fields": {"log": ["block_number", "transaction_hash", "topics"], "transaction": ["gas_used", "hash"]}}
    body = (FIX / "eth-logs-only.arrows").read_bytes()
    s = FakeSession([FakeResponse(body=body), FakeResponse(body=body)])
    bl = Bloklake(base_url="http://x", session=s)
    bl.query(camel)
    page = bl.query(snake)
    assert s.calls[1]["json"] == s.calls[0]["json"]
    assert s.calls[1]["json"]["fields"]["log"] == {"blockNumber": True, "transactionHash": True, "topics": True}
    assert "block_number" in page.logs[0]  # rows stay snake_case


def test_a_key_given_both_ways_is_refused_before_sending():
    s = FakeSession([])
    bl = Bloklake(base_url="http://x", session=s)
    with pytest.raises(ValueError, match="'fromBlock' is given twice"):
        bl.query({"fromBlock": 1, "from_block": 1, "fields": {"log": ["data"]}})
    with pytest.raises(ValueError, match="'addressSet' is given twice"):
        bl.query({"logs": [{"addressSet": "0x01", "address_set": "0x01"}], "fields": {"log": ["data"]}})
    assert s.calls == []


def test_paginate_reads_snake_case_range_keys():
    body = (FIX / "eth-logs-only.arrows").read_bytes()
    s = FakeSession([FakeResponse(body=body), FakeResponse(body=body)])
    pages = Bloklake(base_url="http://x", session=s).paginate(
        {"from_block": 19426000, "to_block": 19426019, "strict_fields": True, "logs": [{}], "fields": {"log": ["block_number"]}})
    next(pages), next(pages)
    first, second = s.calls[0]["json"], s.calls[1]["json"]
    assert (first["fromBlock"], first["toBlock"], first["fields"]) == (19426000, 19426019, {"log": {"blockNumber": True}})
    assert (second["fromBlock"], second["toBlock"], second["strictFields"]) == (19426010, 19426019, True)
    assert "from_block" not in first and "to_block" not in second
