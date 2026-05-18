"""Third-party market-data integration.

Bloomberg-shaped subscription + snapshot client. A real implementation
would wrap the Bloomberg API; here we expose just the surface area
so distill can recognise it as a library-spec candidate (the vendor's
contract is not ours to redefine).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class MarketDataError(Exception):
    pass


class SubscriptionStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    REJECTED = "rejected"


@dataclass
class SubscriptionRequest:
    instrument_symbol: str
    venue: str
    delivery_endpoint: str        # internal webhook URL to push ticks to


@dataclass
class SubscriptionResponse:
    request: SubscriptionRequest
    subscription_id: str
    status: SubscriptionStatus
    accepted_at: datetime


@dataclass
class PriceSnapshot:
    instrument_symbol: str
    venue: str
    last_price_pence: int
    snapshot_time: datetime


def subscribe_to_ticks(
    *,
    instrument_symbol: str,
    venue: str,
    delivery_endpoint: str,
) -> SubscriptionResponse:
    """Subscribe to live tick events for an instrument. Upstream pushes
    each tick to `delivery_endpoint` via webhook.
    """
    if not instrument_symbol:
        raise MarketDataError("instrument_symbol is required")
    if not venue:
        raise MarketDataError("venue is required")
    if not delivery_endpoint.startswith("https://"):
        raise MarketDataError("delivery_endpoint must be an https URL")
    return SubscriptionResponse(
        request=SubscriptionRequest(
            instrument_symbol=instrument_symbol,
            venue=venue,
            delivery_endpoint=delivery_endpoint,
        ),
        subscription_id=f"sub-{instrument_symbol}-{venue}",
        status=SubscriptionStatus.ACTIVE,
        accepted_at=datetime.now(timezone.utc),
    )


def snapshot_price(*, instrument_symbol: str, venue: str) -> PriceSnapshot:
    """Pull a synchronous snapshot of the last traded price."""
    if not instrument_symbol:
        raise MarketDataError("instrument_symbol is required")
    if not venue:
        raise MarketDataError("venue is required")
    return PriceSnapshot(
        instrument_symbol=instrument_symbol,
        venue=venue,
        last_price_pence=0,        # placeholder in this fixture
        snapshot_time=datetime.now(timezone.utc),
    )
