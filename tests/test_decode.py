"""Arrow → JSON conformance: decoding a recorded /query response must give exactly what the
server's /query/json returned for the same request (recorded alongside it)."""
import io
import json
from pathlib import Path

import pytest

from bloklake import BloklakeError, decode_arrow

FIX = Path(__file__).parent / "fixtures"


def arrow(name: str) -> bytes:
    return (FIX / f"{name}.arrows").read_bytes()


@pytest.mark.parametrize("name", ["eth-all-fields", "rh-all-fields", "eth-logs-only", "eth-empty", "synthetic-u256"])
def test_rows_equal_server_json(name):
    rows, meta = decode_arrow(arrow(name))
    expected = json.loads((FIX / f"{name}.json").read_text())
    for rt in ("transactions", "logs", "blocks"):
        assert rows.get(rt) == expected.get(rt), rt
    assert int(meta["bl.objects"]) == expected["objects"]
    assert int(meta["bl.next_block"]) == expected["next_block"]


def test_uint256_decimal_and_nested_hex():
    rows, _ = decode_arrow(arrow("synthetic-u256"))
    a, b, c = rows["transactions"]
    assert a["max_refund"] == str(2**256 - 1)
    assert b["deposit_value"] == str(2**200)
    assert c["deposit_value"] == "0"
    assert a["authorization_list"][0]["chain_id"] == "0x" + "00" * 31 + "01"
    assert c["max_refund"] is None


def test_streamed_source():
    rows, _ = decode_arrow(io.BytesIO(arrow("rh-all-fields")))
    assert rows["logs"] == json.loads((FIX / "rh-all-fields.json").read_text())["logs"]


def test_cut_mid_batch_is_truncated():
    b = arrow("eth-all-fields")
    with pytest.raises(BloklakeError) as e:
        decode_arrow(b[: len(b) // 2])
    assert e.value.code == "truncated"


def test_fewer_rows_than_announced_is_truncated():
    with pytest.raises(BloklakeError, match="3 of 5"):
        decode_arrow(arrow("synthetic-short"))
