"""Inbound webhooks.

Two external feeds:
  - market-data: ticks for an instrument from the data vendor
  - regulator: notices (trading halts, position limits, etc.) from the regulator
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from app import app, store
from app.models import MarketTickEvent, RegulatoryNotice


@app.post("/webhooks/market-data")
def receive_market_tick(body: dict[str, Any]) -> dict[str, Any]:
    """Each tick carries the last traded price for one instrument."""
    event = MarketTickEvent(
        tick_id=str(uuid.uuid4()),
        instrument_symbol=body["instrument_symbol"],
        last_price_pence=int(body["last_price_pence"]),
        venue=body["venue"],
        tick_time=datetime.fromisoformat(body["tick_time"]),
    )
    store.market_tick_events[event.tick_id] = event
    # Update the live mark for the instrument so MTM uses it on next read.
    store.last_mark_by_symbol[event.instrument_symbol] = event.last_price_pence
    return {"tick_id": event.tick_id, "ack": True}


@app.post("/webhooks/regulator")
def receive_regulatory_notice(body: dict[str, Any]) -> dict[str, Any]:
    """A regulatory notice — trading halt, position limit, or other.
    The notice is stored for compliance review; downstream routing
    (suspending traders, flagging positions) is out of scope here.
    """
    notice = RegulatoryNotice(
        notice_id=str(uuid.uuid4()),
        notice_kind=body["notice_kind"],
        instrument_symbol=body.get("instrument_symbol"),
        description=body["description"],
        effective_from=datetime.fromisoformat(body["effective_from"]),
    )
    store.regulatory_notices[notice.notice_id] = notice
    return {"notice_id": notice.notice_id, "ack": True}
