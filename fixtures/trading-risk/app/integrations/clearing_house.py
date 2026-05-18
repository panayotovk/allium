"""Third-party clearing-house integration.

Used to post margin and request collateral release after a margin call
has cleared. The upstream owns the cash movement; the desk only
instructs.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


class ClearingHouseError(Exception):
    pass


@dataclass
class MarginPosting:
    margin_call_id: str
    trader_id: str
    amount_pence: int
    upstream_reference: str
    submitted_at: datetime


@dataclass
class CollateralReleaseRequest:
    trader_id: str
    amount_pence: int
    upstream_reference: str
    submitted_at: datetime


def post_margin_call(
    *,
    margin_call_id: str,
    trader_id: str,
    amount_pence: int,
) -> MarginPosting:
    """Instruct the clearing house to apply `amount_pence` against the
    margin call. Validation happens upstream too; we sanity-check locally.
    """
    if amount_pence <= 0:
        raise ClearingHouseError("amount must be positive")
    if amount_pence > 1_000_000_000_00:  # £1 billion upstream cap
        raise ClearingHouseError("amount exceeds clearing house single-instruction cap")
    return MarginPosting(
        margin_call_id=margin_call_id,
        trader_id=trader_id,
        amount_pence=amount_pence,
        upstream_reference=f"mp-{margin_call_id}",
        submitted_at=datetime.now(timezone.utc),
    )


def request_collateral_release(
    *,
    trader_id: str,
    amount_pence: int,
) -> CollateralReleaseRequest:
    if amount_pence <= 0:
        raise ClearingHouseError("amount must be positive")
    return CollateralReleaseRequest(
        trader_id=trader_id,
        amount_pence=amount_pence,
        upstream_reference=f"cr-{trader_id}",
        submitted_at=datetime.now(timezone.utc),
    )
