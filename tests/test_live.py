"""Against a real server: BLOKLAKE_TEST_URL=https://api.bloklake.com/v1/chains/eth BLOKLAKE_API_KEY=… pytest
Skipped when BLOKLAKE_TEST_URL is unset. Uses Ethereum blocks 19426000..19427499."""
import os

import pytest
import requests

from bloklake import Bloklake

URL = os.environ.get("BLOKLAKE_TEST_URL")
pytestmark = pytest.mark.skipif(not URL, reason="set BLOKLAKE_TEST_URL to run")
TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def server_json(body):
    key = os.environ.get("BLOKLAKE_API_KEY")
    return requests.post(f"{URL}/query/json", json=body, headers={"Authorization": f"Bearer {key}"} if key else {}).json()


def test_every_field_matches_query_json():
    bl = Bloklake(base_url=URL)
    schema = bl.schema()
    fields = {k: [f["dsl_name"] for f in schema["row_types"][rt]] for k, rt in
              (("transaction", "transactions"), ("log", "logs"), ("block", "blocks"))}
    q = {"fromBlock": 19426597, "toBlock": 19426597, "transactions": [{}], "logs": [{"address": [USDC]}], "fields": fields}
    page = bl.query(q)
    expected = server_json({**q, "fields": {k: {n: True for n in v} for k, v in fields.items()}})
    assert page.transactions == expected["transactions"]
    assert page.logs == expected["logs"]
    assert page.blocks == expected["blocks"]


class SpySession(requests.Session):
    def __init__(self, fail_first_id=False):
        super().__init__()
        self.bodies, self.fail_first_id = [], fail_first_id

    def request(self, method, url, json=None, **kw):
        if url.endswith("/query"):
            self.bodies.append(json)
            if self.fail_first_id and json.get("queryId"):
                self.fail_first_id = False
                r = requests.Response()
                r.status_code, r._content = 400, b'{"error": {"code": "unknown_query_id", "message": "expired"}}'
                r.raw = None
                return r
        return super().request(method, url, json=json, **kw)


def test_paginate_covers_range_once_and_reuses_the_id():
    s = SpySession()
    bl = Bloklake(base_url=URL, session=s)
    q = {"fromBlock": 19426000, "toBlock": 19427499, "maxObjects": 2000,
         "logs": [{"topic0": [TRANSFER], "address": [USDC]}], "fields": {"log": ["blockNumber", "logIndex"]}}
    expect_from, rows, pages = 19426000, 0, 0
    for page in bl.paginate(q):
        assert page.meta.from_block == expect_from
        expect_from = page.meta.next_block
        rows += len(page.logs)
        pages += 1
    assert expect_from == 19427500 and pages > 5
    assert all(b.get("queryId") and "logs" not in b for b in s.bodies[1:])
    assert len(bl.collect({k: v for k, v in q.items() if k != "maxObjects"}).logs) == rows


def test_expired_prepared_query_is_resent():
    bl = Bloklake(base_url=URL, session=SpySession(fail_first_id=True))
    page = bl.collect({"fromBlock": 19426000, "toBlock": 19426999, "maxObjects": 5000,
                       "logs": [{"topic0": [TRANSFER]}], "fields": {"log": ["logIndex"]}})
    assert len(page.logs) > 5000
