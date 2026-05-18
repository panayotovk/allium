"""Position update logic.

Positions are keyed by (trader_id, instrument_symbol). A fill mutates the
existing position (or creates one), updates the weighted-average entry
price, and transitions the status if the net quantity reaches zero.
"""
from __future__ import annotations

import uuid

from app import Store
from app.models import (
    OrderSide,
    Position,
    PositionStatus,
    _utcnow,
)


def _position_key(trader_id: str, instrument_symbol: str) -> str:
    return f"{trader_id}::{instrument_symbol}"


def find_open_position(
    store: Store, trader_id: str, instrument_symbol: str,
) -> Position | None:
    for pos in store.positions.values():
        if (
            pos.trader_id == trader_id
            and pos.instrument_symbol == instrument_symbol
            and pos.status == PositionStatus.OPEN
        ):
            return pos
    return None


def apply_fill_to_position(
    store: Store,
    *,
    trader_id: str,
    instrument_symbol: str,
    side: OrderSide,
    fill_quantity: int,
    fill_price_pence: int,
) -> Position:
    """Adjust the trader's open position for the given instrument by the
    signed fill quantity, transitioning the position status if appropriate.
    """
    signed = fill_quantity if side == OrderSide.BUY else -fill_quantity
    existing = find_open_position(store, trader_id, instrument_symbol)

    if existing is None:
        position = Position(
            position_id=str(uuid.uuid4()),
            trader_id=trader_id,
            instrument_symbol=instrument_symbol,
            quantity=signed,
            avg_entry_price_pence=fill_price_pence,
        )
        store.positions[position.position_id] = position
        return position

    new_quantity = existing.quantity + signed
    if existing.quantity * new_quantity < 0:
        # Net side flipped sign — model this as a close + reopen so the
        # weighted average resets.
        existing.status = PositionStatus.CLOSED
        existing.closed_at = _utcnow()
        existing.touch()
        new_position = Position(
            position_id=str(uuid.uuid4()),
            trader_id=trader_id,
            instrument_symbol=instrument_symbol,
            quantity=new_quantity,
            avg_entry_price_pence=fill_price_pence,
        )
        store.positions[new_position.position_id] = new_position
        return new_position

    if new_quantity == 0:
        existing.quantity = 0
        existing.status = PositionStatus.FLAT
        existing.touch()
        return existing

    # Adjust the weighted-average entry price.
    if (signed > 0 and existing.quantity > 0) or (signed < 0 and existing.quantity < 0):
        gross_existing = abs(existing.quantity) * existing.avg_entry_price_pence
        gross_new = fill_quantity * fill_price_pence
        existing.avg_entry_price_pence = (
            (gross_existing + gross_new) // (abs(existing.quantity) + fill_quantity)
        )
    existing.quantity = new_quantity
    existing.touch()
    return existing


def close_flat_position(store: Store, position_id: str) -> Position:
    """Move a FLAT position to CLOSED after the stale-flat window."""
    position = store.positions.get(position_id)
    if position is None:
        raise KeyError(f"unknown position {position_id}")
    if position.status != PositionStatus.FLAT:
        raise ValueError(f"cannot close from {position.status.value}; required FLAT")
    position.status = PositionStatus.CLOSED
    position.closed_at = _utcnow()
    position.touch()
    return position
