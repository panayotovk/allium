"""Order lifecycle: submit, cancel, apply a fill.

Each function enforces the guards required to move an Order between
statuses and updates the corresponding Position via the positions service.
A fill that crosses through `quantity` results in `FILLED`; a partial
fill moves the order to `PARTIALLY_FILLED`.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from app import Store
from app.models import (
    Order,
    OrderSide,
    OrderStatus,
    TraderStatus,
)
from app.services import positions as positions_service
from app.services import risk as risk_service


class InvalidOrderTransition(Exception):
    pass


class OrderRejected(Exception):
    pass


def submit_order(
    store: Store,
    *,
    order_id: str,
    trader_id: str,
    instrument_symbol: str,
    side: OrderSide,
    quantity: int,
    limit_price_pence: int,
) -> Order:
    trader = store.traders.get(trader_id)
    if trader is None:
        raise OrderRejected(f"unknown trader {trader_id}")
    if trader.status != TraderStatus.ACTIVE:
        raise OrderRejected(f"trader {trader_id} is {trader.status.value}")
    instrument = store.instruments.get(instrument_symbol)
    if instrument is None:
        raise OrderRejected(f"unknown instrument {instrument_symbol}")
    if instrument.asset_class.value not in trader.authorised_asset_classes:
        raise OrderRejected(
            f"trader {trader_id} not authorised for {instrument.asset_class.value}"
        )
    if quantity <= 0:
        raise OrderRejected("quantity must be positive")
    if quantity % instrument.lot_size != 0:
        raise OrderRejected(f"quantity must be a multiple of lot_size {instrument.lot_size}")

    order = Order(
        order_id=order_id,
        trader_id=trader_id,
        instrument_symbol=instrument_symbol,
        side=side,
        quantity=quantity,
        limit_price_pence=limit_price_pence,
    )
    store.orders[order_id] = order
    trader.touch()
    return order


def cancel_order(store: Store, order_id: str) -> Order:
    order = _require_order(store, order_id)
    if order.status not in {OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED}:
        raise InvalidOrderTransition(f"cannot cancel from {order.status.value}")
    order.status = OrderStatus.CANCELLED
    order.touch()
    return order


def apply_fill(
    store: Store,
    *,
    order_id: str,
    fill_quantity: int,
    fill_price_pence: int,
) -> Order:
    """Record a fill against an open order. Guarded transition:
    - order.status in {PENDING, PARTIALLY_FILLED}
    - fill_quantity <= order.remaining_quantity
    - order.trader.status == ACTIVE
    """
    order = _require_order(store, order_id)
    if order.status not in {OrderStatus.PENDING, OrderStatus.PARTIALLY_FILLED}:
        raise InvalidOrderTransition(f"cannot fill from {order.status.value}")
    if fill_quantity <= 0 or fill_quantity > order.remaining_quantity:
        raise InvalidOrderTransition(
            f"fill_quantity {fill_quantity} invalid (remaining {order.remaining_quantity})"
        )
    trader = store.traders.get(order.trader_id)
    if trader is None or trader.status != TraderStatus.ACTIVE:
        raise InvalidOrderTransition("trader is not active")

    order.quantity_filled += fill_quantity
    order.status = (
        OrderStatus.FILLED if order.remaining_quantity == 0 else OrderStatus.PARTIALLY_FILLED
    )
    order.touch()
    trader.touch()

    positions_service.apply_fill_to_position(
        store,
        trader_id=order.trader_id,
        instrument_symbol=order.instrument_symbol,
        side=order.side,
        fill_quantity=fill_quantity,
        fill_price_pence=fill_price_pence,
    )

    # Scattered logic: limit evaluation is called here on every fill AND in
    # the intraday aggregator job. The risk service is the single source of
    # truth for "what counts as a breach".
    risk_service.evaluate_limit_breach(store, trader_id=order.trader_id)
    return order


def reject_order(store: Store, order_id: str, reason: str) -> Order:
    order = _require_order(store, order_id)
    if order.status != OrderStatus.PENDING:
        raise InvalidOrderTransition(f"cannot reject from {order.status.value}")
    order.status = OrderStatus.REJECTED
    order.rejection_reason = reason
    order.touch()
    return order


def _require_order(store: Store, order_id: str) -> Order:
    order = store.orders.get(order_id)
    if order is None:
        raise OrderRejected(f"unknown order {order_id}")
    return order
