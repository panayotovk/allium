"""Risk-limit evaluation, breach handling, and margin-call lifecycle.

The risk service is the single source of truth for what counts as a
breach. It is called from two distinct sites:

  1. `apply_fill` in services/orders.py — real-time, after every fill
  2. `intraday_risk_aggregator` in jobs.py — every 15 minutes batch sweep

The two call sites differ in scope: `apply_fill` evaluates only the
trader who just filled; the batch job evaluates every trader.
"""
from __future__ import annotations

import uuid

from app import Store
from app.integrations.clearing_house import post_margin_call
from app.models import (
    MarginCall,
    MarginCallStatus,
    RiskLimit,
    RiskLimitScope,
    RiskLimitStatus,
    _utcnow,
)


class MarginCallError(Exception):
    pass


# Haircut applied to the shortfall when sizing a margin call.
SHORTFALL_HAIRCUT = 1.10


def add_limit(
    store: Store,
    *,
    limit_id: str,
    scope: RiskLimitScope,
    scope_id: str,
    metric,
    threshold_pence: int,
) -> RiskLimit:
    limit = RiskLimit(
        limit_id=limit_id,
        scope=scope,
        scope_id=scope_id,
        metric=metric,
        threshold_pence=threshold_pence,
    )
    store.risk_limits.append(limit)
    return limit


def evaluate_limit_breach(store: Store, trader_id: str) -> list[MarginCall]:
    """Evaluate every limit that applies to this trader's exposure;
    for each that is now in breach but was active, transition the limit
    to BREACHED and issue a MarginCall.

    Returns the list of margin calls issued in this evaluation.
    """
    issued: list[MarginCall] = []
    affected_limits = _limits_affecting_trader(store, trader_id)
    for limit in affected_limits:
        if limit.status != RiskLimitStatus.ACTIVE:
            continue
        if not limit.is_breached(store):
            continue
        # Transition the limit and issue a margin call.
        limit.status = RiskLimitStatus.BREACHED
        limit.breached_at = _utcnow()
        call = _issue_margin_call(store, trader_id=trader_id, limit=limit)
        issued.append(call)
    return issued


def _limits_affecting_trader(store: Store, trader_id: str) -> list[RiskLimit]:
    trader = store.traders.get(trader_id)
    if trader is None:
        return []
    out: list[RiskLimit] = []
    for limit in store.risk_limits:
        if limit.scope == RiskLimitScope.TRADER and limit.scope_id == trader_id:
            out.append(limit)
        elif limit.scope == RiskLimitScope.DESK and limit.scope_id == trader.desk_id:
            out.append(limit)
        elif limit.scope == RiskLimitScope.FIRM:
            out.append(limit)
    return out


def _issue_margin_call(
    store: Store, *, trader_id: str, limit: RiskLimit,
) -> MarginCall:
    observed = limit._observed_value(store)
    shortfall = max(0, observed - limit.threshold_pence)
    required = int(shortfall * SHORTFALL_HAIRCUT)
    call = MarginCall(
        margin_call_id=str(uuid.uuid4()),
        trader_id=trader_id,
        risk_limit_id=limit.limit_id,
        required_amount_pence=required,
    )
    store.margin_calls[call.margin_call_id] = call
    return call


def post_margin(
    store: Store, *, margin_call_id: str, amount_pence: int,
) -> MarginCall:
    """Trader (or operations) posts collateral against an open margin call.

    Guarded transition:
    - call.status == ISSUED
    - amount_pence >= call.required_amount_pence
    - call is not overdue (else it's already in DEFAULTED)
    - the upstream clearing house accepts the posting
    """
    call = store.margin_calls.get(margin_call_id)
    if call is None:
        raise MarginCallError(f"unknown margin call {margin_call_id}")
    if call.status != MarginCallStatus.ISSUED:
        raise MarginCallError(f"cannot post from {call.status.value}")
    if amount_pence < call.required_amount_pence:
        raise MarginCallError(
            f"posting amount {amount_pence} below required {call.required_amount_pence}"
        )
    if call.is_overdue:
        raise MarginCallError("margin call has passed its deadline")
    post_margin_call(
        margin_call_id=margin_call_id,
        amount_pence=amount_pence,
        trader_id=call.trader_id,
    )
    call.status = MarginCallStatus.POSTED
    call.posted_at = _utcnow()
    call.posted_amount_pence = amount_pence
    return call


def clear_margin_call(store: Store, margin_call_id: str) -> MarginCall:
    """Operations confirms the posted collateral has cleared upstream
    and the underlying limit breach is resolved."""
    call = store.margin_calls.get(margin_call_id)
    if call is None:
        raise MarginCallError(f"unknown margin call {margin_call_id}")
    if call.status != MarginCallStatus.POSTED:
        raise MarginCallError(f"cannot clear from {call.status.value}")
    call.status = MarginCallStatus.CLEARED
    call.cleared_at = _utcnow()
    # Re-activate the limit if it is no longer breached.
    for limit in store.risk_limits:
        if limit.limit_id == call.risk_limit_id and limit.status == RiskLimitStatus.BREACHED:
            if not limit.is_breached(store):
                limit.status = RiskLimitStatus.ACTIVE
                limit.breached_at = None
    return call


def default_margin_call(store: Store, margin_call_id: str) -> MarginCall:
    """Move an overdue, still-issued margin call to DEFAULTED."""
    call = store.margin_calls.get(margin_call_id)
    if call is None:
        raise MarginCallError(f"unknown margin call {margin_call_id}")
    if call.status != MarginCallStatus.ISSUED or not call.is_overdue:
        raise MarginCallError("call is not overdue or not in ISSUED state")
    call.status = MarginCallStatus.DEFAULTED
    call.defaulted_at = _utcnow()
    return call
