"""Request and response types. Requests are plain dicts (TypedDicts below) with the API's
camelCase keys; rows are plain dicts with the API's snake_case column names."""
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Sequence, TypedDict

from .fields import Block, BlockField, Log, LogField, Transaction, TransactionField

LogFilter = TypedDict("LogFilter", {
    "address": Sequence[str], "topic0": Sequence[str], "topic1": Sequence[str],
    "topic2": Sequence[str], "topic3": Sequence[str],
    "addressSet": str, "topic0Set": str, "topic1Set": str, "topic2Set": str, "topic3Set": str,
}, total=False)
"""Matches logs: every key set must match (AND); a row matches if any filter does (OR)."""

TransactionFilter = TypedDict("TransactionFilter", {
    "from": Sequence[str], "to": Sequence[str], "sighash": Sequence[str],
    "fromSet": str, "toSet": str, "status": Literal[0, 1],
    "type": Sequence[int],  # 0 legacy, 1 access list, 2 EIP-1559, 3 blob, 4 EIP-7702, Arbitrum 100-120
}, total=False)
"""Matches transactions: every key set must match (AND); a row matches if any filter does (OR)."""


class Fields(TypedDict, total=False):
    """Which columns to return, by request (camelCase) name."""
    log: Sequence[LogField]
    transaction: Sequence[TransactionField]
    block: Sequence[BlockField]


class _QueryRequired(TypedDict):
    fields: Fields


class Query(_QueryRequired, total=False):
    """A query. See https://docs.bloklake.com/querying/request-body"""
    fromBlock: int
    toBlock: int
    maxObjects: int
    logs: Sequence[LogFilter]
    transactions: Sequence[TransactionFilter]
    joinTransactions: bool
    blockInterval: int
    blockOffset: int
    #: Reject (400) a selected field this chain doesn't have, instead of an all-null column.
    strictFields: bool


@dataclass
class PageMeta:
    """What a response says about itself."""
    from_block: int
    to_block: int
    #: ``fromBlock`` for the next page.
    next_block: int
    #: Rows in this page, across row types.
    objects: int
    #: The chain head at query time.
    archive_height: int
    #: The newest final block at query time; rows at or below it never change.
    finalized_block: Optional[int]
    #: Hash of ``to_block`` when it is above ``finalized_block`` (compare it to detect reorgs).
    to_block_hash: Optional[str]
    #: Prepared-query id of this query's filter.
    query_id: str
    #: Selected fields this chain doesn't have, as ``<row type>.<column>``.
    unavailable_fields: List[str] = field(default_factory=list)


@dataclass
class Page:
    """One response: the rows of each row type and the response metadata."""
    logs: List[Log]
    transactions: List[Transaction]
    blocks: List[Block]
    meta: PageMeta


@dataclass
class ChainStatus:
    chain_id: int
    first_block: Optional[int]
    archive_height: Optional[int]
    finalized_block: Optional[int]


@dataclass
class Account:
    key_id: str
    project_id: str
    plan: str
    status: str
    rps: int
    #: Pay-as-you-go balance in millionths of a dollar; None on a subscription.
    balance_micro: Optional[int]
    bytes_used: Optional[int]
    quota_bytes: Optional[int]
    usage_period: Optional[str]
    blocked_for_secs: Optional[int]


@dataclass
class SetInfo:
    set_id: str
    kind: str
    size: int
    created_at: int
    last_used_at: int


@dataclass
class SetCreated:
    set_id: str
    size: int
    created: bool


@dataclass
class Prepared:
    query_id: str
    expires_in: int


Schema = Dict[str, object]
