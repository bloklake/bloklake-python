"""Bloklake API client: typed queries, Apache Arrow transport, JSON-like rows."""
__version__ = "0.1.0"

from .client import Bloklake  # noqa: E402
from .decode import decode_arrow  # noqa: E402
from .errors import BloklakeError  # noqa: E402
from .fields import Block, BlockField, Log, LogField, Transaction, TransactionField  # noqa: E402
from .types import (  # noqa: E402
    Account, ChainStatus, Fields, LogFilter, Page, PageMeta, Prepared, Query, SetCreated, SetInfo, TransactionFilter,
)

__all__ = [
    "Bloklake", "BloklakeError", "decode_arrow",
    "Query", "Fields", "LogFilter", "TransactionFilter", "LogField", "TransactionField", "BlockField",
    "Log", "Transaction", "Block", "Page", "PageMeta", "ChainStatus", "Account", "SetInfo", "SetCreated", "Prepared",
]
