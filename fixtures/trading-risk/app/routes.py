"""HTTP routes — the trader-facing API.

Each route is a thin wrapper over a service-layer call. The handlers
here are not exhaustive — they cover the order flow, margin call
posting, and a couple of read-only views.
"""
from __future__ import annotations

from typing import Any

from app import app, store
from app.models import OrderSide
from app.services.orders import (
    cancel_order,
    reject_order,
    submit_order,
)
from app.services.risk import (
    clear_margin_call,
    post_margin,
)


@app.post("/orders")
def submit_order_route(body: dict[str, Any]) -> dict[str, Any]:
    order = submit_order(
        store,
        order_id=body["order_id"],
        trader_id=body["trader_id"],
        instrument_symbol=body["instrument_symbol"],
        side=OrderSide(body["side"]),
        quantity=int(body["quantity"]),
        limit_price_pence=int(body["limit_price_pence"]),
    )
    return {"order_id": order.order_id, "status": order.status.value}


@app.post("/orders/<order_id>/cancel")
def cancel_order_route(order_id: str) -> dict[str, Any]:
    order = cancel_order(store, order_id)
    return {"order_id": order.order_id, "status": order.status.value}


@app.post("/orders/<order_id>/reject")
def reject_order_route(order_id: str, body: dict[str, Any]) -> dict[str, Any]:
    order = reject_order(store, order_id, body["reason"])
    return {
        "order_id": order.order_id,
        "status": order.status.value,
        "rejection_reason": order.rejection_reason,
    }


@app.post("/margin-calls/<margin_call_id>/post")
def post_margin_route(margin_call_id: str, body: dict[str, Any]) -> dict[str, Any]:
    call = post_margin(
        store,
        margin_call_id=margin_call_id,
        amount_pence=int(body["amount_pence"]),
    )
    return {
        "margin_call_id": call.margin_call_id,
        "status": call.status.value,
        "posted_amount_pence": call.posted_amount_pence,
    }


@app.post("/margin-calls/<margin_call_id>/clear")
def clear_margin_route(margin_call_id: str) -> dict[str, Any]:
    call = clear_margin_call(store, margin_call_id)
    return {"margin_call_id": call.margin_call_id, "status": call.status.value}


@app.get("/orders/<order_id>")
def get_order_route(order_id: str) -> dict[str, Any]:
    order = store.orders[order_id]
    return {
        "order_id": order.order_id,
        "trader_id": order.trader_id,
        "instrument_symbol": order.instrument_symbol,
        "side": order.side.value,
        "quantity": order.quantity,
        "quantity_filled": order.quantity_filled,
        "remaining_quantity": order.remaining_quantity,
        "status": order.status.value,
        "rejection_reason": order.rejection_reason,
    }


@app.get("/traders/<trader_id>/positions")
def list_trader_positions_route(trader_id: str) -> list[dict[str, Any]]:
    return [
        {
            "position_id": p.position_id,
            "instrument_symbol": p.instrument_symbol,
            "quantity": p.quantity,
            "avg_entry_price_pence": p.avg_entry_price_pence,
            "status": p.status.value,
            "mark_to_market_value_pence": p.mark_to_market_value(store),
            "is_concentrated": p.is_concentrated(store),
        }
        for p in store.positions.values()
        if p.trader_id == trader_id
    ]


@app.get("/traders/<trader_id>/risk-summary")
def trader_risk_summary_route(trader_id: str) -> dict[str, Any]:
    trader = store.traders[trader_id]
    return {
        "trader_id": trader.trader_id,
        "total_notional_exposure_pence": trader.total_notional_exposure(store),
        "has_open_margin_calls": trader.has_open_margin_calls(store),
        "is_at_limit": trader.is_at_limit(store),
        "status": trader.status.value,
    }


@app.get("/margin-calls/<margin_call_id>")
def get_margin_call_route(margin_call_id: str) -> dict[str, Any]:
    call = store.margin_calls[margin_call_id]
    return {
        "margin_call_id": call.margin_call_id,
        "trader_id": call.trader_id,
        "risk_limit_id": call.risk_limit_id,
        "required_amount_pence": call.required_amount_pence,
        "status": call.status.value,
        "deadline": call.deadline.isoformat(),
        "is_overdue": call.is_overdue,
    }


@app.get("/desks/<desk_id>/exposure")
def desk_exposure_route(desk_id: str) -> dict[str, Any]:
    desk = store.desks[desk_id]
    traders = desk.active_traders(store)
    return {
        "desk_id": desk_id,
        "desk_name": desk.name,
        "trader_count": len(traders),
        "total_desk_exposure_pence": sum(t.total_notional_exposure(store) for t in traders),
        "risk_appetite_pence": desk.risk_appetite_pence,
    }
