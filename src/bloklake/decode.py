"""Arrow → JSON, exactly like the server's ``/query/json``: unsigned integers → int,
FixedSizeBinary(16) → decimal string, the uint256 columns → decimal string, every other
binary value → ``0x`` hex, lists → lists, structs → dicts, null → None."""
from typing import IO, Any, Callable, Dict, List, Tuple, Union

import pyarrow as pa
import pyarrow.ipc as ipc

from .errors import BloklakeError
from .fields import UINT256_COLUMNS

Json = Any
Conv = Callable[[Any], Json]


def _converter(t: pa.DataType, uint256: bool) -> Conv:
    if pa.types.is_unsigned_integer(t):
        return lambda v: v
    if pa.types.is_fixed_size_binary(t):
        if t.byte_width == 16 or uint256:
            return lambda v: None if v is None else str(int.from_bytes(v, "big"))
        return lambda v: None if v is None else "0x" + v.hex()
    if pa.types.is_binary(t) or pa.types.is_large_binary(t):
        return lambda v: None if v is None else "0x" + v.hex()
    if pa.types.is_list(t) or pa.types.is_large_list(t):
        item = _converter(t.value_type, False)  # nested values follow the generic rules
        return lambda v: None if v is None else [item(x) for x in v]
    if pa.types.is_struct(t):
        fields = [(t.field(i).name, _converter(t.field(i).type, False)) for i in range(t.num_fields)]
        return lambda v: None if v is None else {name: conv(v.get(name)) for name, conv in fields}
    raise BloklakeError(0, "unsupported_type", f"unsupported Arrow type {t} in response")


def _table_rows(table: pa.Table, row_type: str) -> List[Dict[str, Json]]:
    wide = UINT256_COLUMNS.get(row_type, frozenset())
    columns = []
    for field, column in zip(table.schema, table.columns):
        conv = _converter(field.type, field.name in wide)
        columns.append((field.name, [conv(v) for v in column.to_pylist()]))
    return [{name: values[i] for name, values in columns} for i in range(table.num_rows)]


def decode_arrow(source: Union[bytes, IO[bytes], Any]) -> Tuple[Dict[str, List[Dict[str, Json]]], Dict[str, str]]:
    """Decode a ``/query`` body (one Arrow IPC stream per row type, back to back) into
    ``({row_type: rows}, metadata)``. Raises ``BloklakeError("truncated")`` if streams are
    missing or fewer rows arrived than ``bl.objects`` announced."""
    stream = pa.BufferReader(source) if isinstance(source, (bytes, bytearray, memoryview)) else source
    rows: Dict[str, List[Dict[str, Json]]] = {}
    meta: Dict[str, str] = {}
    try:
        while True:
            reader = ipc.open_stream(stream)  # one complete Arrow IPC stream
            table = reader.read_all()         # stops at this stream's end-of-stream marker
            meta = {k.decode(): v.decode() for k, v in (reader.schema.metadata or {}).items()}
            rows[meta.get("bl.row_type", f"stream{len(rows)}")] = _table_rows(table, meta.get("bl.row_type", ""))
            if len(rows) >= len([s for s in meta.get("bl.streams", "").split(",") if s]):
                break
    except BloklakeError:
        raise
    except (pa.ArrowInvalid, OSError, EOFError) as e:
        raise BloklakeError(0, "truncated", f"response ended early: {e}") from e
    except Exception as e:  # network errors from the underlying stream
        raise BloklakeError(0, "truncated", f"response ended early: {e}") from e
    received = sum(len(r) for r in rows.values())
    if received != int(meta.get("bl.objects", "-1")):
        raise BloklakeError(0, "truncated", f"got {received} of {meta.get('bl.objects')} rows")
    return rows, meta
